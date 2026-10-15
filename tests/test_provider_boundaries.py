import gzip
import json
from uuid import uuid4

import httpx
import pytest
from openai import APITimeoutError, AuthenticationError

import app.research.tools as tool_module
from app.core.errors import provider_error_data
from app.research.meter import RuntimeModelMeter
from app.research.tools import ToolGateway
from tests.conftest import new_task


@pytest.mark.parametrize(
    "status,category",
    [
        (400, "request_rejected"),
        (401, "authentication"),
        (403, "authentication"),
        (429, "rate_limit"),
        (503, "service_unavailable"),
    ],
)
def test_error_categories_do_not_retain_provider_messages(status, category):
    request = httpx.Request("POST", "https://provider.example/api")
    error = httpx.HTTPStatusError(
        "private request and secret-provider-key",
        request=request,
        response=httpx.Response(status, request=request, content=b"private response"),
    )
    result = provider_error_data(error)
    assert result["http_status"] == status and result["category"] == category
    assert "private" not in json.dumps(result) and "secret-provider-key" not in json.dumps(result)


def test_timeout_is_distinguished_from_response_rejection():
    error = APITimeoutError(request=httpx.Request("POST", "https://provider.example/api"))
    assert provider_error_data(error)["category"] == "timeout"


async def test_failed_model_request_records_safe_status_and_unknown_usage(setup):
    _, repo, *_ = setup
    task = new_task(setup)
    ctx = repo.claim()
    meter = RuntimeModelMeter(repo, ctx)
    run = uuid4()
    await meter.on_chat_model_start({}, [], run_id=run)
    request = httpx.Request("POST", "https://provider.example/api")
    error = AuthenticationError(
        "private source and secret-provider-key",
        response=httpx.Response(401, request=request),
        body={"private": "secret-provider-key"},
    )
    await meter.on_llm_error(error, run_id=run)
    event = next(e for e in repo.events(task["id"]) if e["kind"] == "model_error")
    assert event["data"]["http_status"] == 401
    assert event["data"]["category"] == "authentication"
    assert "secret-provider-key" not in json.dumps(event)
    assert not repo.get_task(task["id"])["metrics"]["usage_complete"]
    repo.finish(ctx, "cancelled", 0)


class Chunks(httpx.AsyncByteStream):
    def __init__(self, pieces):
        self.pieces, self.reads, self.closed = pieces, 0, False

    async def __aiter__(self):
        for piece in self.pieces:
            self.reads += 1
            yield piece

    async def aclose(self):
        self.closed = True


def mock_client(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        tool_module.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )


async def test_tool_response_size_stops_reading_and_closes_stream(setup, monkeypatch):
    settings, repo, *_ = setup
    stream = Chunks([b"x" * 16384] * 200)
    mock_client(monkeypatch, lambda request: httpx.Response(200, stream=stream))
    monkeypatch.setattr(tool_module, "MAX_TOOL_RESPONSE_BYTES", 65536)
    gateway = ToolGateway(repo, settings, None, {})
    with pytest.raises(ValueError, match="超过大小上限"):
        await gateway._post("https://provider.example/api", {})
    assert stream.reads < 200 and stream.closed


async def test_tool_limit_counts_decoded_bytes_not_compressed_bytes(setup, monkeypatch):
    settings, repo, *_ = setup
    compressed = gzip.compress(b"x" * 131072)
    assert len(compressed) < 65536
    stream = Chunks([compressed])
    mock_client(
        monkeypatch,
        lambda request: httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=stream),
    )
    monkeypatch.setattr(tool_module, "MAX_TOOL_RESPONSE_BYTES", 65536)
    with pytest.raises(ValueError, match="超过大小上限"):
        await ToolGateway(repo, settings, None, {})._post("https://provider.example/api", {})
    assert stream.closed


async def test_tool_retry_is_bounded_and_success_json_is_decoded(setup, monkeypatch):
    settings, repo, *_ = setup
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(503 if len(requests) == 1 else 200, json={"content": "规则"})

    mock_client(monkeypatch, handler)
    result = await ToolGateway(repo, settings, None, {})._post("https://provider.example/api", {})
    assert result == {"content": "规则"} and len(requests) == 2
