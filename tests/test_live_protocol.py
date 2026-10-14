import pytest
from pydantic import SecretStr

from app.research.meter import attach_meter
from app.research.tools import ToolGateway
from app.tasks.worker import Worker, checkpoint_store
from tests.conftest import new_task
from tests.fake_model import ScriptedCommerceModel


@pytest.mark.parametrize("strategy", ["single", "multi"])
@pytest.mark.parametrize("model_name", ["fake-commerce", "gpt-5.5"])
async def test_real_harness_and_structured_output_with_protocol_double(
    setup, monkeypatch, strategy, model_name
):
    settings, repo, _, _ = setup
    settings.agent_mode, settings.llm_model = "live", "fake-commerce"
    settings.openai_api_key = SecretStr("offline-protocol-test")
    settings.tavily_api_key = SecretStr("offline-search-test")

    async def search_response(*args, **kwargs):
        return {
            "results": [
                {
                    "title": "协议测试资料",
                    "url": "https://example.com/fixture",
                    "content": "保温杯采用便携定位。",
                }
            ]
        }

    monkeypatch.setattr(ToolGateway, "_post", search_response)
    task = new_task(setup, strategy=strategy)
    model = ScriptedCommerceModel(model_name=model_name)
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver, model).run_once()
    done = repo.get_task(task["id"])
    assert done["status"] in {"succeeded", "degraded"}
    assert done["validation"]["passed"]
    assert done["validation"]["semantic_status"] == "model_reviewed"
    assert done["metrics"]["model_calls"] >= 4
    assert done["metrics"]["input_tokens"] == done["metrics"]["model_calls"] * 10
    harness_tool_sets = [
        names for names in model._tool_sets if "search_public_sources" in names or "task" in names
    ]
    assert harness_tool_sets
    assert model._skill_loaded
    assert all(
        not {"write_file", "edit_file", "execute", "delete"}.intersection(names)
        for names in harness_tool_sets
    )
    if strategy == "single":
        assert all("task" not in names for names in harness_tool_sets)
    else:
        assert any("task" in names for names in harness_tool_sets)
    assert any(e["kind"] == "web" for e in repo.evidence(task["id"]))
    assert (
        sum(e["kind"] == "model_start" for e in repo.events(task["id"]))
        == done["metrics"]["model_calls"]
    )


async def test_invalid_model_report_retries_once_then_safe_fallback(setup, monkeypatch):
    settings, repo, _, _ = setup
    settings.agent_mode, settings.llm_model = "live", "fake-commerce"
    settings.openai_api_key = SecretStr("offline-protocol-test")
    task = new_task(setup, sources=["sales", "inventory"])
    model = ScriptedCommerceModel(bad_report=True)
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver, model).run_once()
    done = repo.get_task(task["id"])
    assert done["status"] == "degraded" and done["validation"]["fallback_used"]
    assert done["validation"]["passed"]
    assert "E-invented" not in str(done["report"])
    assert sum("ResearchReport" in names for names in model._tool_sets) == 2


async def test_sdk_summarization_calls_are_metered_and_quota_blocks_generation(setup):
    from langchain.agents.middleware import SummarizationMiddleware
    from langchain_core.messages import AIMessage, HumanMessage

    settings, repo, _, _ = setup
    settings.max_model_calls = 1
    task = new_task(setup, sources=["sales"])
    ctx = repo.claim()
    model = attach_meter(ScriptedCommerceModel(), repo, ctx)
    middleware = SummarizationMiddleware(model=model, trigger=("messages", 2), keep=("messages", 1))
    state = {
        "messages": [
            HumanMessage("需要长期保留的经营上下文" * 20),
            AIMessage("已有经营分析"),
            HumanMessage("继续研究"),
        ]
    }
    await middleware.abefore_model(state, None)
    assert len(model._tool_sets) == 1
    assert repo.get_task(task["id"])["metrics"]["model_calls"] == 1
    # The SDK may catch summarization errors; generation must still remain blocked.
    try:
        await middleware.abefore_model(state, None)
    except Exception:
        pass
    assert len(model._tool_sets) == 1
    repo.finish(ctx, "cancelled", 0)


async def test_missing_usage_stops_additional_model_requests(setup):
    class MissingUsageModel(ScriptedCommerceModel):
        def _generate(self, *args, **kwargs):
            result = super()._generate(*args, **kwargs)
            result.generations[0].message.usage_metadata = None
            return result

    settings, repo, _, _ = setup
    settings.agent_mode, settings.llm_model = "live", "fake-commerce"
    settings.openai_api_key = SecretStr("offline-protocol-test")
    task = new_task(setup, sources=["sales", "inventory"])
    model = MissingUsageModel()
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver, model).run_once()
    done = repo.get_task(task["id"])
    assert done["status"] == "degraded" and not done["metrics"]["usage_complete"]
    assert done["metrics"]["model_calls"] == 1 and len(model._tool_sets) == 1
