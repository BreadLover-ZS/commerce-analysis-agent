"""SDK transport and worker deadline tests; no external model requests."""

import asyncio
from contextlib import suppress
from datetime import timedelta

import httpx
import pytest
from openai import APITimeoutError
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.core.errors import provider_error_data
from app.research.agents import model_from_settings, structured_call
from app.research.meter import attach_meter
from app.research.schemas import TaskPlan
from app.storage.models import Task, utcnow
from app.storage.repository import BudgetExceeded
from app.tasks.worker import Worker, checkpoint_store
from tests.conftest import new_task


async def test_environment_timeout_configuration_reaches_model_factory(monkeypatch):
    monkeypatch.setenv("MODEL_READ_TIMEOUT_SECONDS", "75")
    monkeypatch.setenv("MODEL_CONNECT_TIMEOUT_SECONDS", "7")
    settings = Settings(_env_file=None, llm_model="offline", openai_api_key="offline-key")
    model = model_from_settings(settings)
    assert model.root_async_client.timeout.as_dict() == {
        "read": 75,
        "connect": 7,
        "write": 10,
        "pool": 5,
    }
    assert model.root_async_client.max_retries == 0
    await model.root_async_client.close()
    model.root_client.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("model_read_timeout_seconds", 0),
        ("model_read_timeout_seconds", 121),
        ("model_read_timeout_seconds", float("nan")),
        ("model_connect_timeout_seconds", 0),
        ("model_connect_timeout_seconds", 31),
    ],
)
def test_invalid_model_timeouts_are_rejected(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


@pytest.mark.parametrize(
    "error_type,phase",
    [
        (httpx.ConnectTimeout, "connect"),
        (httpx.ReadTimeout, "read"),
        (httpx.WriteTimeout, "write"),
        (httpx.PoolTimeout, "pool"),
    ],
)
def test_wrapped_timeouts_keep_only_known_phase(error_type, phase):
    error = APITimeoutError(request=httpx.Request("POST", "https://private.example/key"))
    intermediate = RuntimeError("private content and secret")
    intermediate.__cause__ = error_type("private payload")
    error.__cause__ = intermediate
    assert provider_error_data(error) == {
        "error_type": "APITimeoutError",
        "http_status": None,
        "category": "timeout",
        "timeout_phase": phase,
    }


def test_unknown_or_cyclic_timeout_cause_is_not_guessed():
    error = APITimeoutError(request=httpx.Request("POST", "https://provider.example"))
    assert provider_error_data(error)["timeout_phase"] == "unknown"
    intermediate = RuntimeError("ReadTimeout in untrusted error message")
    error.__cause__ = intermediate
    intermediate.__cause__ = error
    assert provider_error_data(error)["timeout_phase"] == "unknown"


async def test_sdk_transmits_timeouts_without_retry_and_blocks_after_unknown_usage(setup):
    settings, repo, *_ = setup
    settings.llm_model, settings.openai_api_key = "offline", SecretStr("offline-timeout-test")
    settings.model_read_timeout_seconds = 60
    task = new_task(setup)
    ctx = repo.claim()
    model = attach_meter(model_from_settings(settings), repo, ctx)
    original = model.root_async_client
    requests = []

    def timeout(request):
        requests.append(request)
        assert request.extensions["timeout"] == {"connect": 5, "read": 60, "write": 10, "pool": 5}
        raise httpx.ReadTimeout("secret-provider-text", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as http_client:
        async with original.with_options(http_client=http_client) as client:
            model.root_async_client, model.async_client = client, client.chat.completions
            with pytest.raises(APITimeoutError):
                await structured_call(model, TaskPlan, [("user", "离线测试")], repo, ctx)
            with pytest.raises(BudgetExceeded):
                await structured_call(model, TaskPlan, [("user", "再次调用")], repo, ctx)
    await original.close()
    model.root_client.close()
    assert len(requests) == 1
    done = repo.get_task(task["id"])
    assert done["metrics"]["model_calls"] == 1 and not done["metrics"]["usage_complete"]
    event = next(e for e in repo.events(task["id"]) if e["kind"] == "model_error")
    assert event["data"]["timeout_phase"] == "read" and event["data"]["elapsed_ms"] >= 0
    assert "secret-provider-text" not in str(event)
    repo.finish(ctx, "cancelled", 0)


async def test_worker_total_deadline_cancels_request_before_longer_read_timeout(setup):
    settings, repo, *_ = setup
    settings.agent_mode, settings.llm_model = "live", "offline"
    settings.openai_api_key = SecretStr("offline-deadline-test")
    settings.model_read_timeout_seconds = 60
    task = new_task(setup, sources=["documents"])
    ctx = repo.claim()
    with repo.session() as db:
        db.get(Task, task["id"]).deadline_at = utcnow() + timedelta(seconds=0.5)
        db.commit()
    model = model_from_settings(settings)
    original = model.root_async_client
    requests, cancelled = [], asyncio.Event()

    async def slow_request(request):
        requests.append(request)
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow_request)) as http_client:
        async with original.with_options(http_client=http_client) as client:
            model.root_async_client, model.async_client = client, client.chat.completions
            async with checkpoint_store(settings) as saver:
                await asyncio.wait_for(Worker(repo, settings, saver, model).process(ctx), timeout=3)
    await original.close()
    model.root_client.close()
    done = repo.get_task(task["id"])
    assert len(requests) == 1 and cancelled.is_set()
    assert done["status"] == "failed" and done["error_code"] == "runtime_budget"
    assert done["metrics"]["model_calls"] == 1 and not done["metrics"]["usage_complete"]
    assert not repo.artifacts(task["id"])


async def test_keepalive_body_cannot_extend_absolute_task_deadline(setup):
    """Real local HTTP: periodic bytes reset read inactivity, never finish the JSON."""
    settings, repo, *_ = setup
    settings.agent_mode, settings.llm_model = "live", "offline"
    settings.openai_api_key = SecretStr("offline-keepalive-test")
    settings.model_read_timeout_seconds = 1
    writers, handlers, chunks = set(), set(), []

    async def keepalive(reader, writer):
        handler = asyncio.current_task()
        writers.add(writer)
        handlers.add(handler)
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            length = next(
                int(line.split(b":", 1)[1])
                for line in headers.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            await reader.readexactly(length)
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
            )
            while True:
                writer.write(b"1\r\n\n\r\n")
                await writer.drain()
                chunks.append(1)
                await asyncio.sleep(0.1)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writers.discard(writer)
            handlers.discard(handler)
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()

    server = await asyncio.start_server(keepalive, "127.0.0.1", 0)
    model = None
    try:
        port = server.sockets[0].getsockname()[1]
        settings.openai_base_url = f"http://127.0.0.1:{port}/v1"
        task = new_task(setup, sources=["documents"])
        ctx = repo.claim()
        with repo.session() as db:
            db.get(Task, task["id"]).deadline_at = utcnow() + timedelta(seconds=2)
            db.commit()
        model = model_from_settings(settings)
        async with checkpoint_store(settings) as saver:
            await asyncio.wait_for(Worker(repo, settings, saver, model).process(ctx), timeout=5)
        done = repo.get_task(task["id"])
        assert len(chunks) >= 10  # Receiving for longer than the one-second read timeout.
        assert done["status"] == "failed" and done["error_code"] == "runtime_budget"
        assert done["metrics"]["model_calls"] == 1 and not done["metrics"]["usage_complete"]
        assert not repo.artifacts(task["id"])
    finally:
        server.close()
        await server.wait_closed()
        for writer in tuple(writers):
            writer.close()
        for handler in tuple(handlers):
            handler.cancel()
        await asyncio.gather(*handlers, return_exceptions=True)
        if model:
            await model.root_async_client.close()
            model.root_client.close()
