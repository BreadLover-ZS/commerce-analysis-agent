"""v16 independent counterexamples. All SDK requests use MockTransport or stubs."""

import copy
import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_openai import ChatOpenAI
from pydantic import SecretStr, ValidationError

from app.commerce.hypotheses import allowed_hypotheses
from app.commerce.review import calculate_periods
from app.commerce.review_actions import action_catalog
from app.commerce.review_report_v5 import binding, build_report, compose, verify_composition
from app.core.model_boundary import BeforeProviderSendDenied
from app.diagnosis.model_calls import DiagnosisModelGateway
from app.research.evidence import validate_report
from app.research.graph import build_workflow
from app.research.schemas import ResearchReport
from app.storage.repository import BudgetExceeded, Conflict, StaleLease
from app.tasks.worker import Worker, checkpoint_store
from evaluations.diagnosis import RequestBudget
from evaluations.diagnosis_cases import load_cases
from evaluations.diagnosis_runtime import admit, collect, execute, new_task, sandbox
from tests.conftest import expire_lease
from tests.test_diagnosis_model_calls import MESSAGES, Choice
from tests.test_diagnosis_tools import gateway, oracle_payload
from tests.test_fixed_v3_protocol import ActionModel
from tests.test_review_bound_inputs import review_task


def fixed_evidence():
    source = oracle_payload().model_dump(mode="json")
    source["daily_sales"][2]["revenue_cents"] = 90000
    payload = calculate_periods(
        source,
        date(2026, 9, 1),
        date(2026, 9, 1),
        comparison_start=date(2026, 8, 1),
        comparison_end=date(2026, 8, 1),
        dataset_id="offline",
        content_hash="f" * 64,
        sample=True,
        answer_version=5,
        objective="列出两期支付金额、退款金额、净销售额及差额，核对记账恒等式。",
    )
    return [
        {
            "id": "fixed-source",
            "kind": "metrics",
            "title": "固定两期事实",
            "payload": {"tool": "sales", **payload},
        }
    ]


def narrative(evidence, count=4):
    payload = evidence[0]["payload"]
    actions = action_catalog(payload)
    action = next(iter(actions))
    return {
        "analysis": list(allowed_hypotheses(payload))[:count],
        "recommendations": [
            {
                "action_id": action,
                "precondition_id": actions[action]["precondition_id"],
                "scope_skus": actions[action]["scope_skus"],
            }
        ],
    }


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_capacity_preserves_all_mandatory_facts_and_baseline(count):
    evidence = fixed_evidence()
    baseline = build_report(evidence, [])
    frozen = baseline.model_dump(mode="json")
    report, metadata = compose(evidence, [], narrative(evidence, count))
    assert validate_report(report, evidence)["passed"]
    assert all(len(s.claims) <= 8 for s in report.sections)
    before = [m.model_dump() for s in baseline.sections for c in s.claims for m in c.metrics]
    after = [m.model_dump() for s in report.sections for c in s.claims for m in c.metrics]
    assert before == after and baseline.model_dump(mode="json") == frozen
    assert len(report.sections[3].claims) == 5 + min(count, 3)
    assert len(metadata["omitted"]) == max(count - 3, 0)
    assert ("补充解释因报告容量省略" in str(report.limitations)) is (count == 4)
    assert verify_composition(report.model_dump(mode="json"), evidence, metadata)


@pytest.mark.parametrize(
    "damage", ["illegal_fourth", "bad_action", "wrong_scope", "extra", "empty"]
)
def test_entire_proposal_is_validated_before_truncation(damage):
    evidence = fixed_evidence()
    value = narrative(evidence)
    if damage == "illegal_fourth":
        value["analysis"][-1] = "invented_cause"
    elif damage == "bad_action":
        value["recommendations"][0]["action_id"] = "invented_action"
    elif damage == "wrong_scope":
        value["recommendations"][0]["scope_skus"] = ["foreign"]
    elif damage == "extra":
        value["secret"] = "not_allowed"
    else:
        value["analysis"] = []
    with pytest.raises(ValueError):
        compose(evidence, [], value)


def test_disclosure_at_twenty_limitations_does_not_drop_any_existing_note():
    evidence = fixed_evidence()
    gaps = [f"待人工核对的限制事项 {i}" for i in range(20)]
    baseline = build_report(evidence, gaps)
    report, _ = compose(evidence, gaps, narrative(evidence))
    assert len(report.limitations) == 20
    assert all(
        any(before in after for after in report.limitations) for before in baseline.limitations
    )
    assert "容量省略" in report.limitations[-1]


def test_explicitly_required_explanations_cannot_be_silently_treated_as_optional():
    evidence = fixed_evidence()
    evidence[0]["payload"]["objective"] += "分别讨论支付金额、退款、商品组合和数据质量的假设解释。"
    report, meta = compose(evidence, [], narrative(evidence))
    assert meta["omitted"] and "指定解释方向尚未完整展示" in str(report.model_dump())
    assert meta["reason"] == "required_explanation_capacity" and meta["unshown_required"] == sorted(
        meta["omitted"]
    )
    assert "请求已答齐" not in str(report.model_dump())
    assert validate_report(report, evidence)["passed"]


@pytest.mark.parametrize(
    "key",
    [
        "gross_revenue",
        "comparison_gross_revenue",
        "gross_revenue_delta",
        "refund_amount",
        "comparison_refund_amount",
        "refund_amount_delta",
        "net_revenue",
        "comparison_net_revenue",
        "net_revenue_delta",
    ],
)
def test_v5_all_nine_requested_amounts_are_mandatory(key):
    evidence = fixed_evidence()
    report, _ = compose(evidence, [], narrative(evidence))
    assert any(m.key == key for s in report.sections for c in s.claims for m in c.metrics)
    for section in report.sections:
        for claim in section.claims:
            claim.metrics = [m for m in claim.metrics if m.key != key]
    assert not validate_report(report, evidence)["passed"]


@pytest.mark.parametrize("damage", ["body", "narrative", "omitted", "gaps"])
def test_composition_and_review_binding_detect_tampering(damage):
    evidence = fixed_evidence()
    report, meta = compose(evidence, [], narrative(evidence))
    body = report.model_dump(mode="json")
    before = binding(body, meta)
    if damage == "body":
        body["sections"][0]["claims"].pop()
    elif damage == "narrative":
        meta["narrative"]["analysis"] = meta["narrative"]["analysis"][:1]
    elif damage == "omitted":
        meta["omitted"] = []
    else:
        meta["gaps"].append("changed")
    assert binding(body, meta) != before
    assert not verify_composition(body, evidence, meta)


class FourHypothesisModel(ActionModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if any(t["function"]["name"] == "ReviewNarrativeV3" for t in kwargs.get("tools", [])):
            context = json.loads(messages[-1].content)
            self._contexts.append(context)
            payload = context["facts"][0]["payload"]
            action, detail = next(iter(payload["allowed_actions"].items()))
            args = {
                "analysis": list(context["allowed_hypotheses"])[:4],
                "recommendations": [
                    {
                        "action_id": action,
                        "precondition_id": detail["precondition_id"],
                        "scope_skus": detail["scope_skus"],
                    }
                ],
            }
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[{"name": "ReviewNarrativeV3", "args": args, "id": "four"}],
                            usage_metadata={
                                "input_tokens": 10,
                                "output_tokens": 5,
                                "total_tokens": 15,
                            },
                        )
                    )
                ]
            )
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


async def test_fixed_v5_worker_four_choices_exports_and_counts_once():
    case = load_cases(Path("docs/acceptance/diagnosis-v14-h01-h20-12m/cases.json"))[0][2]
    with sandbox(
        diagnosis_runtime="v16",
        commerce_review_answer_version=5,
        agent_mode="live",
        openai_api_key="local-offline",
        llm_model="fake-commerce",
    ) as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, case.query, "fixed")
        await execute(repo, settings, model=FourHypothesisModel())
        done = collect(repo, task["id"], "fixed")
        assert done["task"]["status"] == "degraded", done["task"]
        validation = done["task"]["validation"]
        assert validation["composition"] is not None, (
            validation,
            done["events"],
            done["model_invocations"],
        )
        assert validation["passed"] and validation["composition"]["omitted"]
        assert done["task"]["metrics"]["model_calls"] == len(done["model_invocations"]) == 2
        assert done["task"]["metrics"]["input_tokens"] == 20
        assert done["task"]["report_hash"] == binding(
            done["task"]["report"], validation["composition"]
        )
        assert {a["name"] for a in repo.artifacts(task["id"])} == {
            "report.json",
            "report.md",
            "report.pdf",
            "evidence.json",
            "research.json",
        }


def prepared_gateway(setup, fixed=False):
    settings, repo, _, _ = setup
    settings.diagnosis_runtime, settings.commerce_review_answer_version = "v16", 5
    if fixed:
        task, _, _ = review_task(setup)
        ctx = repo.claim()
    else:
        tools, task, _ = gateway(setup)
        ctx = tools.ctx
    return repo, ctx, task


def sdk(client):
    return ChatOpenAI(
        api_key="local-offline",
        base_url="https://provider.invalid/v1",
        model="local-mock",
        max_retries=0,
        max_tokens=3,
        http_async_client=client,
        http_socket_options=(),
    )


@pytest.mark.parametrize("fixed", [False, True])
async def test_full_sdk_denial_is_zero_once_for_both_call_entries(setup, tmp_path, fixed):
    repo, ctx, task = prepared_gateway(setup, fixed)
    budget = RequestBudget(
        9, max_total_tokens=12, context_ceiling=10, journal=tmp_path / "journal.jsonl", protocol=2
    )

    async def transport(request):
        pytest.fail("budget denial must not enter transport")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), event_hooks={"request": [budget.before_send]}
    ) as client:
        calls = DiagnosisModelGateway(repo, ctx, sdk(client), "local-mock")
        one = await calls.invoke(Choice, MESSAGES, step_id="step-1")
        two = await calls.invoke(Choice, MESSAGES, step_id="step-1")
        assert one["status"] == two["status"] == "NOT_SENT"
        assert one["usage_complete"] and two["reused"]
        with pytest.raises(BudgetExceeded):
            await calls.invoke(Choice, MESSAGES, step_id="step-2")
    metrics = repo.get_task(task["id"])["metrics"]
    assert metrics["model_calls"] == 1 and metrics["usage_complete"]
    assert metrics["input_tokens"] == metrics["output_tokens"] == budget.sent == 0


@pytest.mark.parametrize("mode", ["same_text", "forged_type", "timeout", "cancel"])
async def test_unproven_errors_remain_unknown(setup, mode):
    import asyncio

    repo, ctx, task = prepared_gateway(setup)

    async def hook(request):
        if mode == "same_text":
            raise RuntimeError("evaluation_token_budget_capacity")
        if mode == "forged_type":
            raise BeforeProviderSendDenied({"event": {}}, object())

    async def transport(request):
        if mode == "cancel":
            raise asyncio.CancelledError()
        raise httpx.ReadTimeout("local timeout", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), event_hooks={"request": [hook]}
    ) as client:
        calls = DiagnosisModelGateway(repo, ctx, sdk(client), "local-mock")
        # A transport-injected CancelledError may be wrapped by the installed
        # LangChain adapter; it still cannot provide evidence of non-submission.
        result = await calls.invoke(Choice, MESSAGES, step_id="step-1")
        assert result["status"] == "UNKNOWN" and not result["usage_complete"]
    assert not repo.get_task(task["id"])["metrics"]["usage_complete"]


@pytest.mark.parametrize("crash_after_proof", [False, True])
async def test_denial_crash_recovery_never_retries_and_old_lease_is_fenced(
    setup, tmp_path, monkeypatch, crash_after_proof
):
    repo, ctx, task = prepared_gateway(setup)
    budget = RequestBudget(
        9, max_total_tokens=12, context_ceiling=10, journal=tmp_path / "journal.jsonl", protocol=2
    )

    async def transport(request):
        pytest.fail("never sent")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), event_hooks={"request": [budget.before_send]}
    ) as client:
        calls = DiagnosisModelGateway(repo, ctx, sdk(client), "local-mock")
        original = calls._not_sent

        def crash(*args):
            raise SystemExit("local crash window")

        monkeypatch.setattr(calls, "_not_sent" if crash_after_proof else "_persist_denial", crash)
        with pytest.raises(SystemExit):
            await calls.invoke(Choice, MESSAGES, step_id="step-1")
        expire_lease(repo, task["id"])
        new_ctx = repo.claim()
        repo.account_unreported_usage(new_ctx)
        recovered = DiagnosisModelGateway(repo, new_ctx, sdk(client), "local-mock")
        result = await recovered.recover(Choice, step_id="step-1")
        assert result["status"] == ("NOT_SENT" if crash_after_proof else "UNKNOWN")
        assert repo.get_task(task["id"])["metrics"]["usage_complete"] is crash_after_proof
        with pytest.raises(StaleLease):
            original(result["call_id"], 0)
        assert budget.sent == 0


@pytest.mark.parametrize(
    "mode",
    [
        "valid",
        "bad_parse",
        "missing_usage",
        "negative_usage",
        "missing_total",
        "wrong_total",
        "zero_usage",
    ],
)
async def test_sent_response_usage_and_schema_are_independently_accounted(setup, mode):
    repo, ctx, task = prepared_gateway(setup)
    entered = []

    async def transport(request):
        entered.append(request)
        value = {
            "id": "local-response",
            "model": "local-mock",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "choice",
                                "type": "function",
                                "function": {
                                    "name": "Choice",
                                    "arguments": json.dumps(
                                        {"action": "x" * 21 if mode == "bad_parse" else "ok"}
                                    ),
                                },
                            }
                        ],
                    },
                }
            ],
        }
        if mode != "missing_usage":
            value["usage"] = {
                "prompt_tokens": -1 if mode == "negative_usage" else 3,
                "completion_tokens": 1,
                "total_tokens": 4,
            }
            if mode == "missing_total":
                value["usage"].pop("total_tokens")
            elif mode == "wrong_total":
                value["usage"]["total_tokens"] = 5
            elif mode == "zero_usage":
                value["usage"] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        return httpx.Response(200, json=value)

    budget = RequestBudget(
        9,
        max_total_tokens=100,
        context_ceiling=10,
        journal=setup[0].data_dir / "sdk-budget.jsonl",
        protocol=2,
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport),
        event_hooks={"request": [budget.before_send], "response": [budget.after_response]},
    ) as client:
        result = await DiagnosisModelGateway(repo, ctx, sdk(client), "local-mock").invoke(
            Choice, MESSAGES, step_id="step-1"
        )
    assert len(entered) == 1
    expected = {
        "valid": "SUCCEEDED",
        "bad_parse": "FAILED",
        "missing_usage": "UNKNOWN",
        "negative_usage": "UNKNOWN",
        "missing_total": "UNKNOWN",
        "wrong_total": "UNKNOWN",
        "zero_usage": "SUCCEEDED",
    }
    assert result["status"] == expected[mode]
    assert result["usage_complete"] is (mode in {"valid", "bad_parse", "zero_usage"})
    metrics = repo.get_task(task["id"])["metrics"]
    assert metrics["model_calls"] == 1
    assert metrics["input_tokens"] == (3 if mode in {"valid", "bad_parse"} else 0)


async def test_real_task_cancellation_propagates_even_if_sdk_wraps_it(setup):
    import asyncio

    repo, ctx, task = prepared_gateway(setup)
    entered = asyncio.Event()

    async def transport(request):
        entered.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        call = asyncio.create_task(
            DiagnosisModelGateway(repo, ctx, sdk(client), "local-mock").invoke(
                Choice, MESSAGES, step_id="step-1"
            )
        )
        await asyncio.wait_for(entered.wait(), 5)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
    assert repo.get_task(task["id"])["metrics"]["usage_complete"] is False


async def test_denial_identity_is_isolated_between_concurrent_tasks(setup, tmp_path):
    import asyncio

    repo, ctx1, task1 = prepared_gateway(setup)
    _, ctx2, task2 = prepared_gateway(setup)
    budget = RequestBudget(
        9, max_total_tokens=12, context_ceiling=10, journal=tmp_path / "journal.jsonl", protocol=2
    )

    async def transport(request):
        pytest.fail("no call may enter transport")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), event_hooks={"request": [budget.before_send]}
    ) as client:
        results = await asyncio.gather(
            *[
                DiagnosisModelGateway(repo, ctx, sdk(client), "local-mock").invoke(
                    Choice, MESSAGES, step_id="step-1"
                )
                for ctx in (ctx1, ctx2)
            ]
        )
    assert all(r["status"] == "NOT_SENT" for r in results)
    events = [json.loads(line) for line in budget.journal.read_bytes().splitlines()]
    assert {e["attempt_identity"]["task_id"] for e in events} == {task1["id"], task2["id"]}
    assert all(repo.get_task(t["id"])["metrics"]["usage_complete"] for t in (task1, task2))


@pytest.mark.parametrize("group", ["fixed", "agent"])
async def test_budget_stopped_worker_keeps_facts_and_does_not_claim_completion(
    setup, tmp_path, group
):
    settings, repo, user, other = setup
    settings.diagnosis_runtime, settings.commerce_review_answer_version = "v16", 5
    settings.agent_mode, settings.openai_api_key, settings.llm_model = (
        "live",
        SecretStr("local-offline"),
        "local-mock",
    )
    case = load_cases(Path("docs/acceptance/diagnosis-v14-h01-h20-12m/cases.json"))[0][2]
    conv, bundle, _ = admit(repo, user, other, case)
    task = new_task(repo, user, conv, bundle, case.query, group)
    budget = RequestBudget(
        99, max_total_tokens=12, context_ceiling=10, journal=tmp_path / "journal.jsonl", protocol=2
    )

    async def transport(request):
        pytest.fail("budget denial must stop all model stages")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), event_hooks={"request": [budget.before_send]}
    ) as client:
        await execute(repo, settings, model=sdk(client))
    done = collect(repo, task["id"], group)
    assert done["task"]["status"] == "degraded", done["task"]
    assert done["task"]["validation"]["passed"]
    assert done["task"]["metrics"]["usage_complete"]
    assert (
        len(done["model_invocations"]) == 1 and done["model_invocations"][0]["status"] == "NOT_SENT"
    )
    if group == "fixed":
        assert done["evidence"] and "请求尚未答齐" in str(done["task"]["report"])
        assert "请求已答齐" not in str(done["task"]["report"])
    else:
        assert done["task"]["report"]["stop_reason"] == "model_budget"
        assert done["task"]["report"]["answer_completion"]["status"] == "incomplete"


@pytest.mark.parametrize("full", [True, False])
def test_full_or_invalid_program_baseline_is_never_trimmed(monkeypatch, full):
    from app.commerce import review_report_v5 as module

    evidence = fixed_evidence()
    baseline = build_report(evidence, [])
    data = baseline.model_dump(mode="json")
    while len(data["sections"][3]["claims"]) < (8 if full else 9):
        claim = dict(data["sections"][3]["claims"][0])
        claim["id"] = "program-" + str(len(data["sections"][3]["claims"]))
        data["sections"][3]["claims"].append(claim)

    def supplied(*args):
        return ResearchReport.model_validate(data)

    monkeypatch.setattr(module, "build_report", supplied)
    if not full:
        with pytest.raises(ValidationError):
            compose(evidence, [], narrative(evidence))
    else:
        report, meta = compose(evidence, [], narrative(evidence))
        assert report.sections[3].model_dump(mode="json") == data["sections"][3]
        assert len(meta["omitted"]) == 4 and meta["kept"] == []


@pytest.mark.parametrize(
    "case",
    load_cases(Path("docs/acceptance/diagnosis-v14-h01-h20-12m/cases.json"))[0],
    ids=lambda c: c.case_id,
)
async def test_new_runtime_replays_only_previously_executed_cases(case):
    with sandbox(diagnosis_runtime="v16", commerce_review_answer_version=5) as (
        settings,
        repo,
        user,
        other,
    ):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, case.query, "agent")
        await execute(repo, settings)
        done = repo.get_task(task["id"])
        if done["status"] == "waiting_input":
            from evaluations.diagnosis_harness import answer_clarification

            await answer_clarification(
                repo, settings, user, task["id"], case.harness.clarification_choice
            )
            done = repo.get_task(task["id"])
        assert done["status"] in {"succeeded", "degraded"}, done
        assert done["validation"]["passed"]
        assert done["parameters"]["accounting_protocol"] == 2


async def test_saved_corrupt_draft_recovers_before_review_and_export(setup):
    settings, repo, _, _ = setup
    settings.commerce_review_answer_version = 5
    task, _, _ = review_task(setup, require_review=True)
    ctx = repo.claim()
    async with checkpoint_store(settings) as saver:
        graph = build_workflow(repo, settings, ctx, saver)
        config = {"configurable": {"thread_id": ctx.task_id}}
        first = await graph.ainvoke({}, config=config, durability="sync")
        assert first["__interrupt__"]
        bad = copy.deepcopy(first["report"])
        bad["sections"][3]["claims"] *= 5
        await graph.aupdate_state(config, {"report": bad}, as_node="draft")
        recovered = await graph.ainvoke(None, config=config, durability="sync")
        assert recovered["__interrupt__"]
        assert recovered["validation"]["passed"] and recovered["validation"]["fallback_used"]
        assert all(len(s["claims"]) <= 8 for s in recovered["report"]["sections"])
        assert repo.artifacts(task["id"]) == []


@pytest.mark.parametrize("damage", ["body", "composition"])
async def test_review_rejects_body_or_composition_tampering(setup, damage):
    from app.storage.models import Task

    settings, repo, user, _ = setup
    settings.commerce_review_answer_version = 5
    task, _, _ = review_task(setup, require_review=True)
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    with repo.session() as db:
        row = db.get(Task, task["id"])
        if damage == "body":
            value = copy.deepcopy(row.report)
            value["title"] = "changed"
            row.report = value
        else:
            row.validation = {**row.validation, "composition": {"forged": True}}
        db.commit()
    with pytest.raises(Conflict, match="不一致"):
        repo.review(task["id"], user.id, True)


@pytest.mark.parametrize("limit", [1, 12])
async def test_v16_successful_response_checkpoint_recovery(setup, limit):
    from tests.test_diagnosis_v14 import saved_response_recovery

    await saved_response_recovery(setup, "v16", limit)


@pytest.mark.parametrize("fixed", [False, True])
@pytest.mark.parametrize("deny", ["revoked", "cancelled", "stale"])
async def test_new_entry_recovery_reauthorizes_before_reusing_a_denial(
    setup, tmp_path, fixed, deny
):
    from app.storage.models import AnalysisInputBundle, CommerceDataset
    from app.storage.repository import NotFound, TaskCancelled

    repo, ctx, task = prepared_gateway(setup, fixed)
    budget = RequestBudget(
        9, max_total_tokens=12, context_ceiling=10, journal=tmp_path / "journal.jsonl", protocol=2
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: None), event_hooks={"request": [budget.before_send]}
    ) as client:
        calls = DiagnosisModelGateway(repo, ctx, sdk(client), "local-mock")
        assert (await calls.invoke(Choice, MESSAGES, step_id="step-1"))["status"] == "NOT_SENT"
        if deny == "revoked":
            with repo.session() as db:
                dataset_id = db.get(AnalysisInputBundle, task["bundle_id"]).sales_dataset_id
                db.get(CommerceDataset, dataset_id).committed = False
                db.commit()
        elif deny == "cancelled":
            repo.cancel(task["id"], ctx.user_id)
        else:
            expire_lease(repo, task["id"])
            assert repo.claim() is not None
        with pytest.raises(
            {"revoked": NotFound, "cancelled": TaskCancelled, "stale": StaleLease}[deny]
        ):
            await calls.recover(Choice, step_id="step-1")
        assert budget.sent == 0


@pytest.mark.parametrize("damage", ["title", "claim_text", "sections"])
def test_mutable_baseline_field_overflow_is_revalidated_before_commit(monkeypatch, damage):
    from app.commerce import review_report_v5 as module

    evidence = fixed_evidence()
    baseline = build_report(evidence, [])
    if damage == "title":
        baseline.title = "x" * 121
    elif damage == "claim_text":
        baseline.sections[0].claims[0].text = "x" * 1201
    else:
        baseline.sections.append(baseline.sections[0].model_copy(deep=True))
    monkeypatch.setattr(module, "build_report", lambda *args: baseline)
    with pytest.raises(ValidationError):
        compose(evidence, [], narrative(evidence))


@pytest.mark.parametrize("damage", ["missing", "changed"])
async def test_cached_unsent_requires_unchanged_durable_proof(setup, tmp_path, damage):
    repo, ctx, _ = prepared_gateway(setup)
    budget = RequestBudget(
        9, max_total_tokens=12, context_ceiling=10, journal=tmp_path / "journal.jsonl", protocol=2
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: None), event_hooks={"request": [budget.before_send]}
    ) as client:
        calls = DiagnosisModelGateway(repo, ctx, sdk(client), "local-mock")
        result = await calls.invoke(Choice, MESSAGES, step_id="step-1")
        assert result["status"] == "NOT_SENT"
        calls._not_sent(result["call_id"], 0)
        calls._not_sent(result["call_id"], 0)
        from app.storage.models import Task

        with repo.session() as db:
            assert db.get(Task, ctx.task_id).usage_reports == 1
        if damage == "missing":
            budget.journal.unlink()
        else:
            event = json.loads(budget.journal.read_bytes())
            event["attempt_identity"]["task_id"] = "foreign"
            budget.journal.write_text(json.dumps(event) + "\n")
        with pytest.raises(Conflict, match="证明"):
            await calls.recover(Choice, step_id="step-1")
        with pytest.raises(Conflict, match="证明"):
            await calls.invoke(Choice, MESSAGES, step_id="step-1")


async def test_journal_write_failure_never_becomes_free_known_zero(setup, tmp_path, monkeypatch):
    repo, ctx, task = prepared_gateway(setup)
    budget = RequestBudget(
        9, max_total_tokens=12, context_ceiling=10, journal=tmp_path / "journal.jsonl", protocol=2
    )

    def fail(*args, **kwargs):
        raise OSError("local durability failure")

    monkeypatch.setattr(budget, "event", fail)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: None), event_hooks={"request": [budget.before_send]}
    ) as client:
        result = await DiagnosisModelGateway(repo, ctx, sdk(client), "local-mock").invoke(
            Choice, MESSAGES, step_id="step-1"
        )
    assert (
        result["status"] == "UNKNOWN" and not repo.get_task(task["id"])["metrics"]["usage_complete"]
    )


@pytest.mark.parametrize("fixed", [False, True])
def test_upgrade_defaults_never_changes_an_old_idempotent_contract(setup, fixed):
    from uuid import uuid4

    from app.storage.models import Task

    settings, repo, user, _ = setup
    settings.diagnosis_runtime, settings.commerce_review_answer_version = "v15", 4
    old, _, request = review_task(setup)
    if not fixed:
        request = request.model_copy(
            update={"profile": "commerce_diagnosis", "strategy": "diagnostic", "dataset_id": None}
        )
        old = repo.create_task(user, request, str(uuid4()))
    with repo.session() as db:
        key = db.get(Task, old["id"]).idempotency_key
    settings.diagnosis_runtime, settings.commerce_review_answer_version = "v16", 5
    retried = repo.create_task(user, request, key)
    assert retried["id"] == old["id"] and retried["parameters"] == old["parameters"]


@pytest.mark.parametrize("version", [1, 2, 3, 4, 5, 6])
def test_fixed_version_can_be_selected_by_real_environment_string(monkeypatch, version):
    from app.core.config import Settings

    monkeypatch.setenv("COMMERCE_REVIEW_ANSWER_VERSION", str(version))
    assert Settings(_env_file=None).commerce_review_answer_version == version


@pytest.mark.parametrize("version", ["0", "7", "5.0", "5x"])
def test_invalid_fixed_version_environment_is_still_rejected(monkeypatch, version):
    from app.core.config import Settings

    monkeypatch.setenv("COMMERCE_REVIEW_ANSWER_VERSION", version)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
