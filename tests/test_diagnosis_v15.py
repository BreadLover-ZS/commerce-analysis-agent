"""Independent counterexamples for R1-R4. All model responses are protocol stubs."""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.commerce.answer_contract_v4 import request_contract
from app.diagnosis.completion_v15 import amount_witness, completion
from app.diagnosis.proposals_v3 import DocumentProposalV3, SalesProposalV3, correction
from app.diagnosis.reporting import markdown, validate_report
from app.research.evidence import validate_report as fixed_validate
from app.research.schemas import ResearchReport
from evaluations.diagnosis_cases import business_input, load_cases
from evaluations.diagnosis_request_checks_v3 import amount_identity, completion_checks
from evaluations.diagnosis_runtime import admit, collect, execute, new_task, sandbox
from tests.test_diagnosis_model_calls import ScriptedDecisionModel

USED = Path("docs/acceptance/diagnosis-v14-h01-h20-12m/cases.json")
CASES = {c.case_id: c for c in load_cases(USED)[0]}


def tool(name, **args):
    return {
        "action": "tool",
        "question": "核对现有事实",
        "hypothesis_code": "data_coverage",
        "tool_name": name,
        "arguments": args,
    }


async def run_case(case, *, group="demo", model=None):
    with sandbox(diagnosis_runtime="v15", commerce_review_answer_version=4) as (
        settings,
        repo,
        user,
        other,
    ):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(
            repo, user, conv, bundle, case.query, "fixed" if group == "fixed" else "agent"
        )
        await execute(repo, settings, model=model)
        if (
            repo.get_task(task["id"])["status"] == "waiting_input"
            and case.harness.clarification_choice
        ):
            from evaluations.diagnosis_harness import answer_clarification

            await answer_clarification(
                repo, settings, user, task["id"], case.harness.clarification_choice, model=model
            )
        result = collect(repo, task["id"], group)
        result["test_bundle"] = bundle
        assert result["task"]["status"] in {"succeeded", "degraded"}, result["task"]
        assert result["task"]["validation"]["passed"], result["task"]["validation"]
        return result


@pytest.mark.parametrize("action", ["tool", "clarify", "stop"])
def test_rule_annotation_absent_and_rejected_for_sales(action):
    schema = SalesProposalV3.model_json_schema()
    assert "rule_interpretations" not in json.dumps(schema)
    obj = tool("analyze_refunds") if action == "tool" else {"action": action, "question": "请核对"}
    obj.update(
        {"clarification_scope": "sku"}
        if action == "clarify"
        else {"stop_reason": "target_covered"}
        if action == "stop"
        else {}
    )
    obj["rule_interpretations"] = []
    with pytest.raises(ValidationError):
        SalesProposalV3.model_validate(obj)


@pytest.mark.parametrize("mutation", ["empty", "verified", "foreign", "extra"])
def test_document_annotations_still_have_strict_structure(mutation):
    value = {
        "action": "stop",
        "question": "解释规则",
        "stop_reason": "target_covered",
        "rule_interpretations": [
            {
                "text": "未核验",
                "status": "unverified",
                "references": [{"evidence_id": "E-1", "chunk_id": "chunk", "quote_hash": "0" * 64}],
            }
        ],
    }
    x = value["rule_interpretations"][0]
    if mutation == "empty":
        x["references"] = []
    if mutation == "verified":
        x["status"] = "verified"
    if mutation == "foreign":
        x["references"][0]["quote_hash"] = "foreign"
    if mutation == "extra":
        x["secret"] = "not permitted"
    with pytest.raises(ValidationError):
        DocumentProposalV3.model_validate(value)


async def test_schema_failure_is_accounted_and_clean_new_proposal_executes():
    bad = tool("analyze_refunds")
    bad["rule_interpretations"] = [{"secret": "never_echo_secret"}]
    model = ScriptedDecisionModel(bad, tool("analyze_refunds"))
    r = await run_case(CASES["H02"], model=model)
    assert [s["status"] for s in r["steps"]] == ["REJECTED", "SUCCEEDED"]
    calls = r["model_invocations"]
    assert (
        calls[0]["status"] == "FAILED"
        and calls[0]["error_code"] == "proposal_rule_annotation_invalid"
    )
    assert all(c["usage_complete"] for c in calls) and len(calls) == 2
    assert r["steps"][0]["assessment"]["correction"]["tool_executed"] is False
    assert len([x for x in r["evidence"] if x["payload"]["tool"] == "analyze_refunds"]) == 1
    assert "never_echo_secret" not in json.dumps(r)
    context = json.loads(model.requests[1][-1][1])
    assert context["schema"] == SalesProposalV3.model_json_schema()
    assert "rule_interpretations" not in json.dumps(context["schema"])
    assert r["task"]["report"]["answer_completion"]["status"] == "fulfilled"


async def test_unknown_is_never_correction_or_free_retry():
    r = await run_case(CASES["H02"], model=ScriptedDecisionModel(TimeoutError("provider_secret")))
    assert len(r["model_invocations"]) == 1
    assert r["model_invocations"][0]["status"] == "UNKNOWN"
    assert r["task"]["report"]["stop_reason"] == "usage_unknown"
    assert r["task"]["report"]["answer_completion"]["status"] == "incomplete"
    assert not any(s.get("assessment", {}).get("correction") for s in r["steps"])
    assert "provider_secret" not in json.dumps(r)


@pytest.mark.parametrize("case_id", ["H06", "H07", "H17"])
async def test_explicit_boundaries_keep_gap_but_answer_complete(case_id):
    r = await run_case(CASES[case_id])
    report = r["task"]["report"]
    assert report["stop_reason"] == "evidence_gap"
    assert report["answer_completion"]["status"] == "boundary_explained"
    assert report["gap_records"] and all(g["evidence_refs"] for g in report["gap_records"])
    assert all(
        completion_checks(
            business_input(CASES[case_id]),
            report,
            [e["payload"] for e in r["evidence"]],
            report["scope"]["skus"],
        ).values()
    )
    assert "缺口与限制已答齐" in markdown(report, "hash")


async def test_inventory_unqueried_does_not_complete_and_double_reject_stops():
    bad = tool("inventory_risk")
    bad["rule_interpretations"] = []
    r = await run_case(CASES["H17"], model=ScriptedDecisionModel(bad, bad))
    report = r["task"]["report"]
    assert report["stop_reason"] == "invalid_decision"
    assert report["answer_completion"]["status"] == "incomplete"
    assert not report["gap_records"]
    assert len(r["model_invocations"]) == 2
    assert not any(e["payload"]["tool"] == "inventory_risk" for e in r["evidence"])


@pytest.mark.parametrize("group", ["demo", "fixed"])
async def test_nine_amounts_and_typed_identity_are_direct(group):
    case = CASES["H03"].model_copy(
        update={"query": "请列出两期支付金额、退款金额和净销售额及差额，并核对记账恒等式。"}
    )
    r = await run_case(case, group=group)
    report = r["task"]["report"]
    sources = [e["payload"] for e in r["evidence"]]
    assert all(completion_checks(business_input(case), report, sources, case.scope_skus).values())
    assert amount_identity(business_input(case), report, sources, case.scope_skus)
    assert amount_witness(r["evidence"], {"skus": case.scope_skus})
    if group == "fixed":
        assert "comparison_gross_revenue" in {
            m["key"] for s in report["sections"] for c in s["claims"] for m in c.get("metrics", [])
        }
    else:
        assert report["capability_witnesses"]


@pytest.mark.parametrize("damage", ["unit", "entity", "period", "cent", "missing"])
async def test_identity_rejects_mutated_proofs(damage):
    r = await run_case(CASES["H03"])
    ev = copy.deepcopy(r["evidence"])
    scope = r["task"]["report"]["scope"]
    source = next(e for e in ev if "gross_revenue.current" in e["payload"].get("fact_catalog", {}))
    f = source["payload"]["fact_catalog"]["gross_revenue.current"]
    if damage == "unit":
        f["unit"] = "count"
    if damage == "entity":
        f["entity_id"] = "FOREIGN"
    if damage == "period":
        f["period"]["start"] = "1900-01-01"
    if damage == "cent":
        f["value"] = "1.001"
    if damage == "missing":
        source["payload"]["fact_catalog"].pop("gross_revenue.current")
    assert amount_witness(ev, scope) is None


@pytest.mark.parametrize("group", ["demo", "fixed"])
async def test_removed_required_value_rejected_independently(group):
    case = CASES["H03"].model_copy(
        update={"query": "列出两期支付金额、退款金额、净销售额及差额并对账。"}
    )
    r = await run_case(case, group=group)
    report = copy.deepcopy(r["task"]["report"])
    if group == "fixed":
        for s in report["sections"]:
            for c in s["claims"]:
                c["metrics"] = [
                    m for m in c.get("metrics", []) if m["key"] != "comparison_gross_revenue"
                ]
        assert not fixed_validate(ResearchReport.model_validate(report), r["evidence"])["passed"]
    else:
        report["facts"] = [
            f for f in report["facts"] if f["metric_key"] != "gross_revenue.comparison"
        ]
        assert not validate_report(report, r["task"], r["test_bundle"], r["evidence"], r["steps"])[
            "passed"
        ]
    checks = completion_checks(
        business_input(case), report, [e["payload"] for e in r["evidence"]], case.scope_skus
    )
    assert not checks["v15_required_values_displayed"]


def test_delta_only_and_nine_value_questions_are_distinct():
    assert (
        len(request_contract("支付金额、退款金额和净销售额各变化多少？请核对记账差额。")["periods"])
        == 1
    )
    assert request_contract("列出两期支付金额、退款金额和净销售额及差额。")["periods"] == [
        "current",
        "comparison",
        "delta",
    ]


def test_safe_correction_has_no_unrecognized_error_text():
    assert correction("secret_http_error")["code"] == "proposal_schema_invalid"
    assert "secret_http_error" not in json.dumps(correction("secret_http_error"))


@pytest.mark.parametrize("limit", [1, 12])
async def test_v15_durable_output_recovery_keeps_schema_and_one_charge(limit):
    from tests.test_diagnosis_v14 import saved_response_recovery

    with sandbox(diagnosis_runtime="v15", max_model_calls=limit) as setup:
        await saved_response_recovery(setup, "v15", limit)


async def test_document_interpretation_unbound_rejected_then_bound_stop_completes():
    case = CASES["H19"].model_copy(
        update={"query": "请逐字引用两份补货规则，并解释规则含义，适用范围仍待核验。"}
    )
    with sandbox(diagnosis_runtime="v15") as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, case.query, "agent")

        class QuotingModel(ScriptedDecisionModel):
            async def ainvoke(self, messages, **kwargs):
                if len(self.requests):
                    e = next(e for e in repo.evidence(task["id"]) if e["payload"].get("quotes"))
                    q = e["payload"]["quotes"][0]
                    self.responses = [
                        {
                            "action": "stop",
                            "question": "未核验解释",
                            "stop_reason": "target_covered",
                            "rule_interpretations": [
                                {
                                    "text": "规则仍待核验。",
                                    "status": "unverified",
                                    "references": [
                                        {
                                            "evidence_id": e["id"],
                                            "chunk_id": q["chunk_id"],
                                            "quote_hash": "0" * 64
                                            if len(self.requests) == 1
                                            else q["quote_hash"],
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                return await super().ainvoke(messages, **kwargs)

        model = QuotingModel(tool("search_rule_documents", query="补货规则"))
        await execute(repo, settings, model=model)
        run = collect(repo, task["id"], "agent")
        report = run["task"]["report"]
        assert run["task"]["validation"]["passed"], run["task"]
        assert [s["status"] for s in run["steps"]] == ["SUCCEEDED", "REJECTED", "SUCCEEDED"]
        hint = run["steps"][1]["assessment"]["correction"]
        assert hint["code"] == "unbound_rule_interpretation" and hint["allowed_quote_references"]
        assert report["answer_completion"]["status"] == "fulfilled"
        assert report["rule_interpretations"][0]["status"] == "unverified"
        context = json.loads(model.requests[-1][-1][1])
        assert context["schema"] == DocumentProposalV3.model_json_schema()


async def test_actual_sdk_json_mode_strict_schema_feedback_without_network():
    import httpx
    from langchain_openai import ChatOpenAI

    responses = [dict(tool("analyze_refunds"), rule_interpretations=[]), tool("analyze_refunds")]
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["response_format"] == {"type": "json_object"}
        system = body["messages"][0]["content"]
        human = json.loads(body["messages"][-1]["content"])
        assert (
            json.loads(system.split("JSON，不输出解释：", 1)[1])
            == human["schema"]
            == SalesProposalV3.model_json_schema()
        )
        return httpx.Response(
            200,
            json={
                "id": "sdk-stub",
                "object": "chat.completion",
                "created": 1,
                "model": "stub",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": json.dumps(responses.pop(0))},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model = ChatOpenAI(
            api_key="stub",
            base_url="https://api.deepseek.com",
            model="stub",
            max_retries=0,
            http_async_client=client,
        )
        object.__setattr__(model, "diagnosis_protocol_stub", True)
        r = await run_case(CASES["H02"], model=model)
    assert len(requests) == 2
    assert r["model_invocations"][0]["error_code"] == "proposal_rule_annotation_invalid"
    assert r["task"]["report"]["answer_completion"]["status"] == "fulfilled"


@pytest.mark.parametrize("damage", ["scope", "source", "coverage"])
async def test_identity_cannot_mix_scope_source_or_missing_rows(damage):
    r = await run_case(CASES["H03"])
    ev = copy.deepcopy(r["evidence"])
    scope = r["task"]["report"]["scope"]
    p = next(
        e["payload"] for e in ev if "gross_revenue.current" in e["payload"].get("fact_catalog", {})
    )
    if damage == "scope":
        p["scope"]["skus"] = p["scope"]["skus"][:1]
    if damage == "source":
        p["source_hashes"]["sales"] = "0" * 64
    if damage == "coverage":
        p["coverage"]["current"]["complete"] = False
    assert amount_witness(ev, scope) is None


async def test_completed_report_cannot_hide_failure_by_changing_status_only():
    bad = dict(tool("inventory_risk"), rule_interpretations=[])
    r = await run_case(CASES["H17"], model=ScriptedDecisionModel(bad, bad))
    report = copy.deepcopy(r["task"]["report"])
    report["answer_completion"]["status"] = "boundary_explained"
    assert not validate_report(report, r["task"], r["test_bundle"], r["evidence"], r["steps"])[
        "passed"
    ]
    checks = completion_checks(
        business_input(CASES["H17"]),
        report,
        [e["payload"] for e in r["evidence"]],
        CASES["H17"].scope_skus,
    )
    assert (
        not checks["v15_boundary_requires_source_proof"]
        and not checks["v15_execution_failure_not_complete"]
    )


async def check_v15_and_fixed4_saved_admission(setup):
    from uuid import uuid4

    from app.research.schemas import TaskRequest

    settings, repo, user, other = setup
    settings.diagnosis_runtime = "v15"
    settings.commerce_review_answer_version = 4
    case = CASES["H03"].model_copy(
        update={"query": "列出两期支付金额、退款金额、净销售额及差额，并核对记账恒等式。"}
    )
    conv, bundle, _ = admit(repo, user, other, case)
    for profile, strategy in [
        ("commerce_diagnosis", "diagnostic"),
        ("commerce_review", "workflow"),
    ]:
        key = str(uuid4())
        request = TaskRequest(
            conversation_id=conv,
            bundle_id=bundle["bundle_id"],
            profile=profile,
            strategy=strategy,
            sources=["sales"],
            query=case.query,
            require_review=False,
        )
        task = repo.create_task(user, request, key)
        settings.diagnosis_runtime = "legacy"
        settings.commerce_review_answer_version = 1
        assert repo.create_task(user, request, key)["id"] == task["id"]
        await execute(repo, settings)
        t = repo.get_task(task["id"])
        assert t["validation"]["passed"], t
        assert t["graph_version"] == (
            "diagnosis-v15" if profile == "commerce_diagnosis" else "commerce-review-bundle-v4"
        )
        settings.diagnosis_runtime = "v15"
        settings.commerce_review_answer_version = 4


async def test_v15_and_fixed4_admissions_survive_configuration_change(setup):
    await check_v15_and_fixed4_saved_admission(setup)


@pytest.mark.parametrize("sku_length", [8, 1000])
def test_large_request_never_sends_truncated_obligations(sku_length):
    from app.commerce.answer_contract_v4 import bound_request
    from app.diagnosis.planner_v15 import prompt
    from app.diagnosis.schemas import SALES_TOOLS
    from app.storage.repository import BudgetExceeded

    scope = {"skus": [f"CAP{i:03}" + "x" * sku_length for i in range(100)], "objective": None}
    bundle = {
        "bundle_id": "b",
        "bundle_hash": "h",
        "sales_content_hash": "s",
        "period": {},
        "comparison_period": {},
        "allowed_skus": scope["skus"],
    }
    query = "逐商品列出两期净销售额、支付金额、退款金额、支付件数、退款件数及差额"
    products = [{"sku": sku, "category": "类别"} for sku in scope["skus"]]
    task = {
        "query": query,
        "parameters": {
            "budget": {"max_tokens": 60000, "max_tool_calls": 10, "max_model_calls": 12},
            "sources": ["sales"],
            "answer_contract": bound_request(query, bundle, products),
        },
    }
    try:
        messages = prompt(
            task, bundle, scope, [], [], {k: v.model_json_schema() for k, v in SALES_TOOLS.items()}
        )
    except BudgetExceeded as error:
        assert str(error) == "diagnosis_context_budget"
    else:
        context = json.loads(messages[1][1])
        assert len(messages[1][1].encode()) <= 96 * 1024
        assert len(context["component_obligations"]["rows"]) == 1500
        assert {r[0] for r in context["component_obligations"]["rows"]} == set(scope["skus"])


async def test_current_only_amounts_do_not_require_optional_identity_or_prior_data():
    case = CASES["H07"].model_copy(update={"query": "核对本期支付金额和退款金额。"})
    r = await run_case(case)
    report = r["task"]["report"]
    assert report["answer_completion"]["status"] == "fulfilled"
    assert not report["capability_witnesses"] and not report["gap_records"]
    assert not request_contract(case.query)["amount_identity_requested"]


async def test_amount_witness_supports_complete_multi_day_periods():
    from datetime import date, timedelta

    from evaluations.diagnosis_cases import Case

    raw = CASES["H03"].model_dump(mode="json")
    raw["query"] = "列出两期支付金额、退款金额、净销售额及差额并对账。"
    for part in ["period", "comparison_period"]:
        raw[part]["end"] = str(date.fromisoformat(raw[part]["end"]) + timedelta(days=1))
    raw["sales"]["daily_sales"].extend(
        [
            {**row, "day": str(date.fromisoformat(row["day"]) + timedelta(days=1))}
            for row in list(raw["sales"]["daily_sales"])
        ]
    )
    case = Case.model_validate(raw)
    r = await run_case(case)
    assert amount_witness(r["evidence"], r["task"]["report"]["scope"])
    assert amount_identity(
        business_input(case),
        r["task"]["report"],
        [e["payload"] for e in r["evidence"]],
        case.scope_skus,
    )
    ev = copy.deepcopy(r["evidence"])
    p = next(
        e["payload"] for e in ev if "gross_revenue.current" in e["payload"].get("fact_catalog", {})
    )
    p["period"]["start"] = "2000-01-01"
    for f in p["fact_catalog"].values():
        if f["period"]["start"] == raw["period"]["start"]:
            f["period"]["start"] = "2000-01-01"
    assert amount_witness(ev, r["task"]["report"]["scope"]) is None


@pytest.mark.parametrize("bad_action", [False, True])
async def test_fixed4_model_context_full_review_and_fallback_keep_required_facts(setup, bad_action):
    from pydantic import SecretStr

    from app.tasks.worker import Worker, checkpoint_store
    from tests.test_fixed_v3_protocol import ActionModel

    settings, repo, user, other = setup
    settings.commerce_review_answer_version = 4
    settings.agent_mode, settings.llm_model = "live", "fake-commerce"
    settings.openai_api_key = SecretStr("offline-protocol-only")
    case = CASES["H03"].model_copy(
        update={"query": "列出两期支付金额、退款金额、净销售额及差额并对账。"}
    )
    conv, bundle, _ = admit(repo, user, other, case)
    task = new_task(repo, user, conv, bundle, case.query, "fixed")
    settings.commerce_review_answer_version = 1
    model = ActionModel(bad_action=bad_action)
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver, model).run_once()
    t = repo.get_task(task["id"])
    assert t["validation"]["passed"], t
    assert t["validation"]["draft_fallback_used"] is bad_action
    payload = model._contexts[0]["facts"][0]["payload"]
    assert payload["answer_version"] == 4
    assert payload["answer_contract"]["version"] == "answer-contract-v4"
    assert "comparison_gross_revenue" in payload["fact_catalog"]
    assert payload["answer_completion"] and payload["allowed_actions"]
    assert len(model._contexts[1]["report"]["sections"]) == 5
    assert "review-answer-completion" in str(model._contexts[1]["report"])
    assert "comparison_gross_revenue" in {
        m["key"] for s in t["report"]["sections"] for c in s["claims"] for m in c["metrics"]
    }


@pytest.mark.parametrize("group", ["demo", "fixed"])
async def test_pre_activation_not_applicable_is_not_an_unavailable_required_value(group):
    case = CASES["H08"]
    r = await run_case(case, group=group)
    assert all(
        completion_checks(
            business_input(case),
            r["task"]["report"],
            [e["payload"] for e in r["evidence"]],
            case.scope_skus,
        ).values()
    )


async def test_inventory_computed_but_not_displayed_cannot_claim_complete():
    case = CASES["H17"]
    r = await run_case(case)
    report = copy.deepcopy(r["task"]["report"])
    report["facts"] = [f for f in report["facts"] if not f["metric_key"].endswith(".coverage_days")]
    computed = completion(
        case.query,
        r["evidence"],
        report["scope"],
        r["steps"],
        report=report,
        stop_reason=report["stop_reason"],
    )
    assert computed["status"] == "incomplete" and computed["omitted_items"]
    assert not completion_checks(
        business_input(case), report, [e["payload"] for e in r["evidence"]], case.scope_skus
    )["v15_required_values_displayed"]


@pytest.mark.parametrize("deny", ["revoked", "cancelled", "stale"])
async def test_v15_saved_response_recovery_reauthorizes_all_boundaries(deny):
    from app.diagnosis.model_calls import DiagnosisModelGateway
    from app.diagnosis.proposals_v3 import proposal_schema
    from app.storage.models import CommerceDataset
    from app.storage.repository import NotFound, StaleLease, TaskCancelled
    from tests.conftest import expire_lease

    with sandbox(diagnosis_runtime="v15") as (settings, repo, user, other):
        case = CASES["H02"]
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, case.query, "agent")
        ctx = repo.claim()
        model = ScriptedDecisionModel(tool("analyze_refunds"))
        calls = DiagnosisModelGateway(repo, ctx, model, "local")
        schema = proposal_schema(task)
        await calls.invoke(schema, [("human", "safe local proposal")], step_id="step-1")
        if deny == "revoked":
            with repo.session() as db:
                db.get(CommerceDataset, bundle["sales_dataset_id"]).committed = False
                db.commit()
        elif deny == "cancelled":
            repo.cancel(task["id"], user.id)
        else:
            expire_lease(repo, task["id"])
            assert repo.claim() is not None
        with pytest.raises(
            {"revoked": NotFound, "cancelled": TaskCancelled, "stale": StaleLease}[deny]
        ):
            await calls.recover(schema, step_id="step-1")
        assert len(model.requests) == 1


async def test_v15_failed_response_recovery_keeps_safe_classification():
    from app.diagnosis.model_calls import DiagnosisModelGateway
    from app.diagnosis.proposals_v3 import proposal_schema

    with sandbox(diagnosis_runtime="v15") as (settings, repo, user, other):
        case = CASES["H02"]
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, case.query, "agent")
        ctx = repo.claim()
        model = ScriptedDecisionModel(dict(tool("analyze_refunds"), rule_interpretations=[]))
        calls = DiagnosisModelGateway(repo, ctx, model, "local")
        schema = proposal_schema(task)
        before = await calls.invoke(schema, [("human", "safe local proposal")], step_id="step-1")
        after = await calls.recover(schema, step_id="step-1")
        assert before["status"] == "FAILED" and before["usage_complete"]
        assert (
            after["call_id"] == before["call_id"]
            and after["error_code"] == "proposal_rule_annotation_invalid"
        )
        assert after["reused"] and len(model.requests) == 1
