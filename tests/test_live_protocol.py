import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from pydantic import PrivateAttr, SecretStr
from sqlalchemy import select

from app.research.meter import attach_meter
from app.research.tools import ToolGateway
from app.storage.models import Artifact
from app.tasks.worker import Worker, checkpoint_store
from tests.conftest import new_task
from tests.fake_model import ScriptedCommerceModel


async def test_review_receives_period_and_deduplicated_system_limitations(setup):
    note = "库存为标注日期的快照；历史销量仅用于估计，不自动执行补货"

    class LimitationReviewModel(ScriptedCommerceModel):
        _reviewed: bool = PrivateAttr(default=False)

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            calls = result.generations[0].message.tool_calls
            if not calls:
                return result
            if calls[0]["name"] == "ResearchReport":
                calls[0]["args"]["limitations"].extend([note + "。", note + "."])
            elif calls[0]["name"] == "SemanticReview":
                context = json.loads(messages[-1].content.split("\n证据：\n", 1)[0])
                assert context["period"] == ["2026-09-01", "2026-09-30"]
                notes = context["report"]["limitations"]
                assert sum(item.rstrip("。.!！") == note for item in notes) == 1
                self._reviewed = True
            return result

    settings, repo, *_ = setup
    settings.agent_mode, settings.llm_model = "live", "fake-commerce"
    settings.openai_api_key = SecretStr("offline-limitations-review")
    task = new_task(setup, sources=["inventory"])
    model = LimitationReviewModel()
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver, model).run_once()
    done = repo.get_task(task["id"])
    assert model._reviewed and done["validation"]["semantic_status"] == "model_reviewed"
    assert not done["validation"].get("fallback_used")


async def test_research_child_with_empty_next_is_resumed_before_reading_result(setup, monkeypatch):
    import app.research.graph as graph_module

    class PendingResearcher:
        resumed = False

        async def aget_state(self, config):
            return SimpleNamespace(
                values={"messages": [AIMessage("stale research snapshot")]}, next=()
            )

        async def ainvoke(self, value, config, *, durability):
            assert value is None
            assert durability == "sync"
            self.resumed = True
            return {"messages": [AIMessage("finalized pending research")]}

    child = PendingResearcher()
    monkeypatch.setattr(graph_module, "create_researcher", lambda *args: child)

    class InspectSynthesis(ScriptedCommerceModel):
        _finalized_seen: bool = PrivateAttr(default=False)

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            names = [tool["function"]["name"] for tool in kwargs.get("tools", [])]
            if "ResearchSynthesis" in names:
                context = json.loads(messages[-1].content.split("\n证据：\n", 1)[0])
                assert context["researcher_output"] == "finalized pending research"
                self._finalized_seen = True
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    settings, repo, *_ = setup
    settings.agent_mode, settings.llm_model = "live", "fake-commerce"
    settings.openai_api_key = SecretStr("offline-child-recovery")
    task = new_task(setup, sources=["documents", "web"])
    model = InspectSynthesis()
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver, model).run_once()
    assert child.resumed and model._finalized_seen
    assert repo.get_task(task["id"])["status"] == "degraded"


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


async def test_research_synthesis_receives_text_sources_while_draft_receives_metrics(setup):
    class TextOnlyResearchModel(ScriptedCommerceModel):
        _text_seen: bool = PrivateAttr(default=False)

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            names = [tool["function"]["name"] for tool in kwargs.get("tools", [])]
            if "ResearchSynthesis" in names:
                sources = json.loads(messages[-1].content.split("证据：\n", 1)[1])
                assert sources and all(e["kind"] != "metrics" for e in sources)
                self._text_seen = True
            elif "ResearchReport" in names:
                sources = json.loads(messages[-1].content.split("证据：\n", 1)[1])
                assert any(e["kind"] == "metrics" for e in sources)
                assert any(e["kind"] == "document" for e in sources)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    settings, repo, *_ = setup
    settings.agent_mode, settings.llm_model = "live", "fake-commerce"
    settings.openai_api_key = SecretStr("offline-text-research-test")
    task = new_task(setup, sources=["inventory", "documents"])
    model = TextOnlyResearchModel()
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver, model).run_once()
    done = repo.get_task(task["id"])
    assert model._text_seen and model._research_forwarded
    assert done["validation"]["passed"] and not done["validation"]["draft_fallback_used"]
    with repo.session() as db:
        research_file = db.scalar(
            select(Artifact).where(Artifact.task_id == task["id"], Artifact.name == "research.json")
        )
    research = json.loads((settings.data_dir / "artifacts" / research_file.storage_key).read_text())
    assert research["status"] == "completed"


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


async def test_business_repair_receives_failed_claim_and_keeps_numeric_validation(setup):
    class RepairingModel(ScriptedCommerceModel):
        _drafts: int = PrivateAttr(default=0)

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            calls = result.generations[0].message.tool_calls
            if calls and calls[0]["name"] == "ResearchReport":
                metadata = json.loads(messages[-1].content.split("\n证据：\n", 1)[0])
                if self._drafts:
                    assert any(issue.startswith("unbound:") for issue in metadata["prior_issues"])
                    assert any(
                        c["id"] == "unbound" and c["text"] == "退款率为0.91%。"
                        for s in metadata["prior_report"]["sections"]
                        for c in s["claims"]
                    )
                else:
                    claims = calls[0]["args"]["sections"][0]["claims"]
                    claims.append(
                        {
                            "id": "unbound",
                            "kind": "analysis",
                            "text": "退款率为0.91%。",
                            "evidence_ids": claims[0]["evidence_ids"],
                            "metrics": [],
                        }
                    )
                self._drafts += 1
            return result

    settings, repo, *_ = setup
    settings.agent_mode, settings.llm_model = "live", "fake-commerce"
    settings.openai_api_key = SecretStr("offline-repair-test")
    task = new_task(setup, sources=["sales"])
    model = RepairingModel()
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver, model).run_once()
    done = repo.get_task(task["id"])
    assert model._drafts == 2
    assert done["validation"]["passed"] and not done["validation"].get("fallback_used")
    assert not done["validation"]["draft_fallback_used"]
    assert done["validation"]["semantic_status"] == "model_reviewed"


async def test_model_proposed_absence_is_not_forced_into_report_and_is_reviewed(setup):
    false_gap = "未提供库存风险的量化判断条件"

    class GapReviewModel(ScriptedCommerceModel):
        _drafts: int = PrivateAttr(default=0)
        _gap_index: int = PrivateAttr(default=0)

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            calls = result.generations[0].message.tool_calls
            if not calls:
                return result
            call = calls[0]
            if call["name"] == "ResearchSynthesis":
                call["args"]["gaps"] = [false_gap]
            elif call["name"] == "ResearchReport":
                metadata = json.loads(messages[-1].content.split("\n证据：\n", 1)[0])
                assert false_gap in metadata["research_findings"]["gaps"]
                assert false_gap not in metadata["known_gaps"]
                if self._drafts == 0:
                    call["args"]["limitations"].append(false_gap)
                    self._gap_index = len(call["args"]["limitations"]) - 1
                else:
                    assert any("limitations[" in issue for issue in metadata["prior_issues"])
                    assert false_gap in metadata["prior_report"]["limitations"]
                    assert "原文已提供" in metadata["prior_review"]["explanation"]
                self._drafts += 1
            elif call["name"] == "SemanticReview":
                context = json.loads(messages[-1].content.split("\n证据：\n", 1)[0])
                assert context["allowed_sources"] == ["documents"]
                assert context["selected_sources"] == ["documents"]
                call["args"]["unsupported_limitation_indices"] = (
                    [self._gap_index] if self._drafts == 1 else []
                )
                call["args"]["explanation"] = "原文已提供库存风险判断条件，不能将其称为缺失。"
            return result

    settings, repo, *_ = setup
    settings.agent_mode, settings.llm_model = "live", "fake-commerce"
    settings.openai_api_key = SecretStr("offline-gap-review-test")
    task = new_task(setup, sources=["documents"])
    model = GapReviewModel()
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver, model).run_once()
    done = repo.get_task(task["id"])
    assert model._drafts == 2
    assert false_gap not in done["report"]["limitations"]
    rejected = next(e for e in repo.events(task["id"]) if e["kind"] == "draft_rejected")
    assert false_gap in rejected["data"]["report"]["limitations"]
    assert done["validation"]["passed"] and not done["validation"].get("fallback_used")
    assert done["validation"]["semantic_status"] == "model_reviewed"
