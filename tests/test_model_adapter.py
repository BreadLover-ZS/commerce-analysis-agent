import json

import httpx
import pytest
from langchain_core.exceptions import OutputParserException
from openai import AsyncOpenAI
from pydantic import SecretStr

from app.core.config import Settings
from app.research.agents import model_from_settings, structured_call
from app.research.schemas import ResearchReport
from tests.conftest import new_task


@pytest.mark.parametrize(
    ("base_url", "deepseek"),
    [
        ("https://api.deepseek.com", True),
        ("https://api.deepseek.com/v1", True),
        ("https://api.deepseek.com/beta", True),
        ("https://api.openai.com/v1", False),
        ("https://api.deepseek.com.other.example/v1", False),
    ],
)
async def test_structured_report_request_matches_provider_contract(base_url, deepseek):
    settings = Settings(
        _env_file=None,
        openai_base_url=base_url,
        openai_api_key=SecretStr("offline-model-adapter-test"),
        llm_model="deepseek-flash",
    )
    report = {"title": "协议测试报告", "sections": [], "limitations": ["仅验证请求协议"]}
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.path.endswith("/chat/completions")
        if deepseek:
            assert body["thinking"] == {"type": "disabled"}
            assert body["response_format"] == {"type": "json_object"}
            assert "tool_choice" not in body and "tools" not in body
            assert "JSON Schema" in body["messages"][0]["content"]
            assert '"title"' in body["messages"][0]["content"]
        else:
            assert "thinking" not in body
            assert "response_format" not in body
            assert body["tool_choice"] == {
                "type": "function",
                "function": {"name": "ResearchReport"},
            }
            assert body["tools"][0]["function"]["name"] == "ResearchReport"
        return httpx.Response(
            200,
            json={
                "id": "offline-completion",
                "object": "chat.completion",
                "created": 0,
                "model": settings.llm_model,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop" if deepseek else "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(report, ensure_ascii=False) if deepseek else None,
                            "tool_calls": []
                            if deepseek
                            else [
                                {
                                    "id": "offline-tool-call",
                                    "type": "function",
                                    "function": {
                                        "name": "ResearchReport",
                                        "arguments": json.dumps(report, ensure_ascii=False),
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            },
        )

    model = model_from_settings(settings)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        async with AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            base_url=base_url,
            http_client=http_client,
            max_retries=0,
        ) as client:
            model.root_async_client = client
            model.async_client = client.chat.completions
            result = await structured_call(
                model, ResearchReport, [("user", "生成协议测试报告")], None, None
            )
    assert result.model_dump() == report
    assert len(requests) == 1


async def test_schema_failure_records_field_without_private_model_output(setup):
    _, repo, *_ = setup
    task = new_task(setup)
    ctx = repo.claim()
    private_value = "private-model-output-" * 20
    try:
        ResearchReport.model_validate({"title": private_value, "sections": []})
    except ValueError as error:
        parsing_error = error

    class InvalidOutput:
        calls = 0

        def with_structured_output(self, *args, **kwargs):
            return self

        async def ainvoke(self, *args, **kwargs):
            self.calls += 1
            return {"parsed": None, "parsing_error": parsing_error}

    model = InvalidOutput()
    with pytest.raises(ValueError, match="模型未返回有效的结构化输出"):
        await structured_call(model, ResearchReport, [], repo, ctx)
    events = [e for e in repo.events(task["id"]) if e["kind"] == "structured_output_error"]
    assert model.calls == 2 and len(events) == 2
    event = events[0]
    assert event["data"]["issues"] == [{"field": ["title"], "type": "string_too_long"}]
    assert private_value not in json.dumps(event)
    repo.finish(ctx, "cancelled", 0)


async def test_wrapped_schema_error_is_repaired_once_without_relaxing_schema(setup):
    _, repo, *_ = setup
    task = new_task(setup)
    ctx = repo.claim()
    try:
        ResearchReport.model_validate({"title": "x" * 121, "sections": []})
    except ValueError as error:
        parsing_error = OutputParserException("invalid report")
        parsing_error.__cause__ = error
    repaired = ResearchReport(title="修正后的报告", sections=[])

    class RecoveringOutput:
        calls = 0

        def with_structured_output(self, *args, **kwargs):
            return self

        async def ainvoke(self, messages, config):
            self.calls += 1
            if self.calls == 1:
                return {"parsed": None, "parsing_error": parsing_error}
            assert config["metadata"]["phase"] == "报告草稿修正"
            feedback = json.loads(messages[-1][1].split("\n", 1)[1])
            assert feedback["schema_issues"] == [{"field": ["title"], "type": "string_too_long"}]
            return {"parsed": repaired, "parsing_error": None}

    model = RecoveringOutput()
    result = await structured_call(model, ResearchReport, [("user", "生成报告")], repo, ctx)
    assert result == repaired and model.calls == 2
    assert sum(e["kind"] == "structured_output_error" for e in repo.events(task["id"])) == 1
    repo.finish(ctx, "cancelled", 0)
