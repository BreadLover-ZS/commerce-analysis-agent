"""Failure replays and negative controls; all models are local protocol stubs."""

import copy
import json
from pathlib import Path

import pytest

from app.diagnosis import planner_v11, planner_v12
from app.diagnosis.reporting import validate_report
from evaluations.diagnosis_capabilities import multi_sku_daily_covered
from evaluations.diagnosis_cases import Case, business_input, load_cases
from evaluations.diagnosis_runtime import admit, collect, execute, new_task, sandbox
from tests.test_diagnosis_graph import tool_proposal
from tests.test_diagnosis_model_calls import ScriptedDecisionModel
from tests.test_diagnosis_v3 import stop

ROOT = Path(__file__).resolve().parents[1]
# Executed old fixtures are repair regressions, not new formal holdout scores.
FAILED = {
    c.case_id: c for c in load_cases(ROOT / "docs/acceptance/diagnosis-v11-h01-h20/cases.json")[0]
}
DEVELOPMENT = {c.case_id: c for c in load_cases()[0] if c.split != "holdout"}


@pytest.mark.parametrize("runtime", ["v11", "v12"])
async def test_h08_local_pre_activation_is_not_whole_scope_failure(runtime):
    case = FAILED["H08"]
    with sandbox(diagnosis_runtime=runtime) as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, case.query, "agent")
        old, new = case.scope_skus
        model = ScriptedDecisionModel(
            tool_proposal("compare_periods", {"filters": {"skus": [old]}}),
            tool_proposal("compare_periods", {"filters": {"skus": [new]}}),
            tool_proposal("rank_contributors", {"dimension": "sku", "top_k": 2}),
        )
        await execute(repo, settings, model=model)
        r = collect(repo, task["id"], "agent")
        if runtime == "v11":
            assert r["task"]["status"] == "failed"
            assert r["task"]["error_code"] == "report_validation_failed"
            return
        assert r["task"]["validation"]["passed"], r["task"]
        report = r["task"]["report"]
        assert report["stop_reason"] == "target_covered"
        assert not report["open_questions"]
        assert len(model.requests) == 3
        wrong = copy.deepcopy(report)
        wrong["open_questions"] = [planner_v12.NO_CURRENT]
        assert (
            "unverified_gap_statement"
            in validate_report(wrong, r["task"], bundle, r["evidence"], r["steps"])["errors"]
        )


@pytest.mark.parametrize("case_id", ["H05", "D01"])
async def test_literal_zero_and_nonzero_need_no_model_but_exploration_keeps_gap(case_id):
    case = FAILED[case_id] if case_id == "H05" else DEVELOPMENT[case_id]
    case = case.model_copy(update={"query": FAILED["H05"].query})
    with sandbox(diagnosis_runtime="v12") as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, case.query, "agent")
        model = ScriptedDecisionModel()
        await execute(repo, settings, model=model)
        r = collect(repo, task["id"], "agent")
        assert r["task"]["validation"]["passed"], r["task"]
        assert r["task"]["report"]["stop_reason"] == "target_covered"
        assert not r["task"]["report"]["open_questions"]
        assert not model.requests and r["task"]["metrics"]["model_calls"] == 0
        assert r["task"]["metrics"]["tool_calls"] == 2
        assert planner_v12.missing_coverage(
            case.query + "请分析变化来源", r["evidence"], r["task"]["report"]["scope"]
        )


@pytest.mark.parametrize(
    "suffix",
    [
        "为什么下降",
        "分析变化",
        "解释差额",
        "核对贡献",
        "逐日查看",
        "核对库存",
        "核对退款金额",
        "查阅规则",
    ],
)
def test_literal_allowlist_does_not_swallow_other_obligations(suffix):
    assert not planner_v12.literal_comparison("比较两期净销售额；" + suffix)


@pytest.mark.parametrize("case_id", ["D06", "D07"])
async def test_incomplete_or_absent_source_cannot_complete_literal(case_id):
    case = DEVELOPMENT[case_id].model_copy(update={"query": FAILED["H05"].query})
    with sandbox(diagnosis_runtime="v12") as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, case.query, "agent")
        await execute(repo, settings, model=ScriptedDecisionModel(stop()))
        r = collect(repo, task["id"], "agent")
        assert r["task"]["validation"]["passed"], r["task"]
        assert r["task"]["report"]["stop_reason"] != "target_covered"
        wrong = copy.deepcopy(r["task"]["report"])
        wrong["stop_reason"] = "target_covered"
        wrong["open_questions"] = []
        assert not validate_report(wrong, r["task"], bundle, r["evidence"], r["steps"])["passed"]


async def test_h01_batch_capability_context_and_completion_without_extra_request():
    case = FAILED["H01"]
    with sandbox(diagnosis_runtime="v12", max_model_calls=2) as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, case.query, "agent")
        model = ScriptedDecisionModel(
            tool_proposal("rank_contributors", {"top_k": 1}), tool_proposal("analyze_refunds", {})
        )
        await execute(repo, settings, model=model)
        r = collect(repo, task["id"], "agent")
        assert r["task"]["validation"]["passed"], r["task"]
        assert r["task"]["report"]["stop_reason"] == "target_covered"
        assert len(model.requests) == 2
        before, after = [json.loads(m[1][1]) for m in model.requests]
        frontier = before["capability_frontier"]
        assert frontier["rank_contributors"]["pending_component_items_covered_in_one_call"] == 18
        assert frontier["compare_periods"]["remaining_components_require_at_least_calls"] == 6
        assert (
            after["capability_frontier"]["rank_contributors"][
                "pending_component_items_covered_in_one_call"
            ]
            == 0
        )
        assert after["remaining_budget"]["model_calls"] == 1
        assert (
            after["remaining_budget"]["tokens_before_soft_stop"]
            < before["remaining_budget"]["tokens_before_soft_stop"]
        )
        assert all(i["status"] == "acquired" for i in after["component_obligations"])
        assert all(
            e["bundle_hash"] == bundle["bundle_hash"] and e["query_spec_hash"]
            for e in after["evidence"]
        )
        old = planner_v11.prompt(
            task, bundle, r["task"]["report"]["scope"], r["steps"], r["evidence"], before["tools"]
        )
        compact = planner_v12.prompt(
            r["task"],
            bundle,
            r["task"]["report"]["scope"],
            r["steps"],
            r["evidence"],
            before["tools"],
        )
        assert len(compact[1][1].encode()) < len(old[1][1].encode()) * 0.65


async def test_multi_sku_daily_assertion_checks_scope_and_raw_day_facts():
    raw = DEVELOPMENT["D10"].model_dump(mode="json")
    raw["scope_skus"] = [p["sku"] for p in raw["sales"]["products"]]
    raw["query"] = "分别核对每个授权商品的两期每日净销售额与日覆盖"
    case = Case.model_validate(raw)
    with sandbox(diagnosis_runtime="v12") as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, case.query, "additional")
        model = ScriptedDecisionModel(
            *(
                tool_proposal("drilldown_series", {"filters": {"skus": [sku]}})
                for sku in case.scope_skus
            )
        )
        await execute(repo, settings, model=model)
        r = collect(repo, task["id"], "additional")
        assert r["task"]["validation"]["passed"], r["task"]
        sources = [e["payload"] for e in r["evidence"]]
        assert multi_sku_daily_covered(business_input(case), sources)
        assert not multi_sku_daily_covered(business_input(case), sources[:-1])
        changed = copy.deepcopy(sources)
        series = [p for p in changed if p["tool"] == "drilldown_series"]
        key = series[-1]["series"][0]["fact_keys"]["net_revenue"]
        series[-1]["fact_catalog"][key]["value"] = "1234567.89"
        assert not multi_sku_daily_covered(business_input(case), changed)
        narrowed = business_input(case)
        narrowed["scope_skus"] = case.scope_skus[:1]
        assert not multi_sku_daily_covered(narrowed, sources)


async def test_v12_saved_contract_survives_runtime_change_and_publishes(setup):
    settings, repo, user, other = setup
    settings.diagnosis_runtime = "v12"
    case = FAILED["H08"]
    conv, bundle, _ = admit(repo, user, other, case)
    task = new_task(repo, user, conv, bundle, case.query, "agent")
    settings.diagnosis_runtime = "legacy"
    model = ScriptedDecisionModel(tool_proposal("rank_contributors", {}))
    await execute(repo, settings, model=model)
    r = collect(repo, task["id"], "agent")
    assert r["task"]["validation"]["passed"], r["task"]
    assert r["task"]["report"]["versions"]["graph_version"] == "diagnosis-v12"
    assert r["task"]["report"]["stop_reason"] == "target_covered"
    assert len(model.requests) == 1
    assert any(a["name"].endswith(".pdf") for a in repo.artifacts(task["id"]))


async def test_v12_missing_refund_request_is_not_hidden_by_batch_components():
    case = FAILED["H01"]
    with sandbox(diagnosis_runtime="v12", max_model_calls=1) as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, case.query, "agent")
        model = ScriptedDecisionModel(tool_proposal("rank_contributors", {}))
        await execute(repo, settings, model=model)
        r = collect(repo, task["id"], "agent")
        assert r["task"]["validation"]["passed"], r["task"]
        assert r["task"]["report"]["stop_reason"] == "model_budget"
        assert "退款金额、件数和分母核对" in r["task"]["report"]["open_questions"]
        assert len(model.requests) == 1


@pytest.mark.parametrize("sku_length", [8, 1000])
def test_large_context_retains_all_obligations_or_stops_before_sending(sku_length):
    from app.diagnosis.schemas import SALES_TOOLS
    from app.storage.repository import BudgetExceeded

    scope = {"skus": [f"CAP{i:03}" + "x" * sku_length for i in range(100)], "objective": None}
    task = {
        "query": "逐商品列出两期支付金额、退款金额和净销售额及差额",
        "parameters": {
            "budget": {"max_tokens": 60000, "max_tool_calls": 10, "max_model_calls": 12},
            "sources": ["sales"],
        },
    }
    bundle = {"bundle_id": "b", "bundle_hash": "h", "period": {}, "comparison_period": {}}
    catalog = {k: v.model_json_schema() for k, v in SALES_TOOLS.items()}
    if sku_length == 1000:
        with pytest.raises(BudgetExceeded, match="diagnosis_context_budget"):
            planner_v12.prompt(task, bundle, scope, [], [], catalog)
    else:
        messages = planner_v12.prompt(task, bundle, scope, [], [], catalog)
        assert len(messages[1][1].encode()) < 96 * 1024
        context = json.loads(messages[1][1])
        assert context["scope"] == scope
        assert len(context["component_obligations"]["rows"]) == 900
        assert {row[0] for row in context["component_obligations"]["rows"]} == set(scope["skus"])
        assert context["remaining_budget"]["decision_calls"] == 9
        assert context["bundle"]["bundle_hash"] == "h"
