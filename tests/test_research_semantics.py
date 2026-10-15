"""Frozen human counterexamples + protocol tests; not an LLM quality benchmark."""

import json
from pathlib import Path

import pytest
from pydantic import PrivateAttr, SecretStr
from sqlalchemy import select

from app.research.schemas import ResearchSemanticReview, ResearchSynthesis
from app.research.synthesis import research_review_issues, validate_synthesis
from app.storage.models import Artifact
from app.tasks.worker import Worker, checkpoint_store
from evaluations.run import research_semantics_ok
from tests.conftest import new_task
from tests.fake_model import ScriptedCommerceModel

FIXTURE = json.loads((Path(__file__).parent / "fixtures/research_semantics.json").read_text())


def verdict(finding, value="supported", explanation="原文支持此陈述"):
    return {"finding_id": finding["id"], "verdict": value, "explanation": explanation}


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["id"])
def test_historical_semantic_errors_pass_quotes_but_reject_review(case):
    candidate = ResearchSynthesis(findings=[case["rejected"]])
    assert not validate_synthesis(candidate, FIXTURE["sources"])
    review = ResearchSemanticReview(
        findings=[verdict(case["rejected"], case["expected_verdict"], case["explanation"])],
        unsupported_gap_indices=[],
    )
    assert research_review_issues(candidate, review)
    corrected = ResearchSynthesis(findings=[case["corrected"]])
    assert not validate_synthesis(corrected, FIXTURE["sources"])
    accepted = ResearchSemanticReview(
        findings=[verdict(case["corrected"])], unsupported_gap_indices=[]
    )
    assert not research_review_issues(corrected, accepted)


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "unknown", "negative-gap", "unknown-gap"]
)
def test_incomplete_or_invalid_research_review_cannot_pass(mutation):
    finding = FIXTURE["cases"][0]["corrected"]
    candidate = ResearchSynthesis(findings=[finding], gaps=["尚需实际数据"])
    entries, gaps = [verdict(finding)], []
    if mutation == "missing":
        entries = []
    elif mutation == "duplicate":
        entries *= 2
    elif mutation == "unknown":
        entries[0]["finding_id"] = "invented"
    else:
        gaps = [-1 if mutation == "negative-gap" else 1]
    with pytest.raises(ValueError):
        research_review_issues(
            candidate, ResearchSemanticReview(findings=entries, unsupported_gap_indices=gaps)
        )


def test_real_contradiction_and_uncertain_verdict_are_distinct():
    candidate = ResearchSynthesis(
        findings=[
            {
                "id": "same-condition",
                "kind": "conflict",
                "statement": "同一地区同一活动对优惠是否适用的规定互斥。",
                "support": [
                    {"evidence_id": "A", "quote": "本活动适用于甲地区"},
                    {"evidence_id": "B", "quote": "本活动不适用于甲地区"},
                ],
            }
        ]
    )
    sources = [
        {"id": s.evidence_id, "kind": "document", "locator": s.evidence_id, "excerpt": s.quote}
        for s in candidate.findings[0].support
    ]
    assert not validate_synthesis(candidate, sources)
    finding = candidate.findings[0].model_dump()
    for value in ("supported", "uncertain"):
        review = ResearchSemanticReview(
            findings=[verdict(finding, value)], unsupported_gap_indices=[]
        )
        assert bool(research_review_issues(candidate, review)) is (value == "uncertain")


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["id"])
@pytest.mark.parametrize("repair", [True, False], ids=["repair", "reject"])
async def test_rejected_findings_never_reach_report_and_repair_gets_feedback(setup, case, repair):
    class AnnotatedReviewModel(ScriptedCommerceModel):
        _drafts: int = PrivateAttr(default=0)
        _reviews: int = PrivateAttr(default=0)

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            calls = result.generations[0].message.tool_calls
            if not calls:
                return result
            call = calls[0]
            content = messages[-1].content
            context = json.loads(content.split("\n证据：\n", 1)[0])
            if call["name"] == "ResearchSynthesis":
                if self._drafts:
                    assert (
                        context["prior_synthesis"]["findings"][0]["statement"]
                        == case["rejected"]["statement"]
                    )
                    assert case["explanation"] in context["prior_issues"][0]
                    assert context["prior_review"]["findings"][0]["verdict"] == "unsupported"
                finding = json.loads(
                    json.dumps(case["corrected"] if repair and self._drafts else case["rejected"])
                )
                sources = json.loads(content.split("\n证据：\n", 1)[1])
                for support in finding["support"]:
                    support["evidence_id"] = next(
                        source["id"] for source in sources if support["quote"] in source["excerpt"]
                    )
                call["args"] = {"findings": [finding], "gaps": []}
                self._drafts += 1
            elif call["name"] == "ResearchSemanticReview":
                assert any(
                    source["kind"] == "metrics"
                    for source in json.loads(content.split("\n证据：\n", 1)[1])
                )
                finding = context["synthesis"]["findings"][0]
                rejected = finding["statement"] == case["rejected"]["statement"]
                call["args"] = {
                    "findings": [
                        verdict(
                            finding, "unsupported" if rejected else "supported", case["explanation"]
                        )
                    ],
                    "unsupported_gap_indices": [],
                }
                self._reviews += 1
            elif call["name"] == "ResearchReport":
                assert case["rejected"]["statement"] not in json.dumps(
                    context["research_findings"], ensure_ascii=False
                )
            return result

    settings, repo, *_ = setup
    settings.agent_mode, settings.llm_model = "live", "fake-commerce"
    settings.openai_api_key = SecretStr("offline-research-review")
    settings.max_model_calls = 8
    task = new_task(setup, sources=["inventory", "documents"])
    model = AnnotatedReviewModel()
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver, model).run_once()
    done = repo.get_task(task["id"])
    assert model._drafts == model._reviews == 2
    assert done["metrics"]["model_calls"] == 7
    with repo.session() as db:
        artifact = db.scalar(
            select(Artifact).where(Artifact.task_id == task["id"], Artifact.name == "research.json")
        )
    research = json.loads((settings.data_dir / "artifacts" / artifact.storage_key).read_text())
    assert research_semantics_ok("live", repo.evidence(task["id"]), research) is repair
    assert research["status"] == ("completed" if repair else "failed")
    assert research["validation"]["passed"] is repair
    assert done["validation"]["research_validation"] == research["validation"]
    rejected = [e for e in repo.events(task["id"]) if e["kind"] == "research_synthesis_rejected"]
    assert len(rejected) == (1 if repair else 2)
    assert (
        rejected[0]["data"]["research"]["findings"][0]["statement"] == case["rejected"]["statement"]
    )


@pytest.mark.parametrize("failure", ["timeout", "unknown-id", "disabled", "budget"])
async def test_unavailable_research_review_keeps_excerpts_and_cannot_pass(setup, failure):
    class FailingReviewer(ScriptedCommerceModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            names = [tool["function"]["name"] for tool in kwargs.get("tools", [])]
            if "ResearchSemanticReview" in names and failure == "timeout":
                raise TimeoutError("sensitive provider text must not be retained")
            result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            if "ResearchSemanticReview" in names and failure == "unknown-id":
                result.generations[0].message.tool_calls[0]["args"]["findings"][0]["finding_id"] = (
                    "invented"
                )
            return result

    settings, repo, *_ = setup
    settings.agent_mode, settings.llm_model = "live", "fake-commerce"
    settings.openai_api_key = SecretStr("offline-failing-review")
    if failure == "disabled":
        settings.semantic_review = False
    if failure == "budget":
        settings.max_model_calls = 2  # plan + synthesis; review cannot be admitted
    task = new_task(setup, sources=["documents"])
    model = FailingReviewer()
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver, model).run_once()
    done = repo.get_task(task["id"])
    assert done["status"] == "degraded"
    validation = done["validation"]["research_validation"]
    assert not validation["passed"]
    assert validation["semantic_status"] == ("disabled" if failure == "disabled" else "unavailable")
    with repo.session() as db:
        artifact = db.scalar(
            select(Artifact).where(Artifact.task_id == task["id"], Artifact.name == "research.json")
        )
    research = json.loads((settings.data_dir / "artifacts" / artifact.storage_key).read_text())
    assert not research_semantics_ok("live", repo.evidence(task["id"]), research)
    assert all("原文摘录：" in finding["statement"] for finding in research["research"]["findings"])
    assert "sensitive provider text" not in json.dumps(repo.events(task["id"]), ensure_ascii=False)
    if failure in {"budget", "timeout"}:
        assert done["metrics"]["model_calls"] == (2 if failure == "budget" else 3)
