"""Hard-coded literal acceptance and old-version counterfactuals, no paid calls."""

import copy
import json
from uuid import uuid4

import pytest

from app.commerce.answer_contract_v2 import entity_selection, request_contract
from app.diagnosis.reporting import validate_report
from app.research.schemas import TaskRequest
from app.storage.repository import Conflict
from evaluations.diagnosis_cases import Case, load_cases, reference_answer
from evaluations.diagnosis_grading import grade
from evaluations.diagnosis_oracle import numeric_errors
from evaluations.diagnosis_runtime import admit, collect, execute, new_task, sandbox
from tests.test_diagnosis_graph import tool_proposal
from tests.test_diagnosis_model_calls import ScriptedDecisionModel
from tests.test_diagnosis_v3 import stop

CASES = {c.case_id: c for c in load_cases()[0] if c.split != "holdout"}


@pytest.mark.parametrize(
    "query,wanted",
    [
        ("比较除商品 A 以外商品的两期净销售额及差额", ["B"]),
        ("不要 A，只看 B 的两期净销售额", ["B"]),
        ("不分析商品 A，分析商品 B 的净销售额", ["B"]),
        ("不计算利润，核对商品 A 两期净销售额", ["A"]),
        ("不要 A、B，核对其他商品", ["C"]),
        ("除 A、B 之外，核对其他商品", ["C"]),
    ],
)
def test_entity_polarity_has_independent_literal_expectations(query, wanted):
    assert (
        list(entity_selection(query, ["A", "B", "C"] if wanted == ["C"] else ["A", "B"]).selected)
        == wanted
    )


@pytest.mark.parametrize(
    "query", ["排除 A 和 B", "只看 A；排除 A", "不要不看 A", "只核对商品 FOREIGN 的净销售额"]
)
def test_empty_conflicting_unknown_or_double_negative_entities_require_restating(query):
    with pytest.raises(ValueError):
        entity_selection(query, ["A", "B"])


@pytest.mark.parametrize("runtime,scope", [("v12", ["A"]), ("v13", ["B"])])
async def test_excluded_entity_worker_version_counterfactual(runtime, scope):
    query = "比较除商品 A 以外商品的两期净销售额及差额。"
    case = CASES["D01"].model_copy(
        update={
            "query": query,
            "expected": CASES["D01"].expected.model_copy(
                update={
                    "required_assertions": [
                        a
                        for a in CASES["D01"].expected.required_assertions
                        if a != "signed_contributions_reconcile"
                    ]
                }
            ),
        }
    )
    with sandbox(diagnosis_runtime=runtime) as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, query, "agent")
        model = ScriptedDecisionModel()
        await execute(repo, settings, model=model)
        r = collect(repo, task["id"], "agent")
        report = r["task"]["report"]
        assert r["task"]["validation"]["passed"], r["task"]
        assert report["scope"]["skus"] == scope
        assert not model.requests
        if runtime == "v13":
            assert report["request_contract"]["entity_scope"] == {
                "included": [],
                "excluded": ["A"],
                "selected": ["B"],
            }
            assert all(f["entity_id"] == "B" for f in report["facts"])
            assert grade(case, bundle, r)["automatic_passed"], {
                k: v for k, v in grade(case, bundle, r)["assertions"].items() if not v
            }


@pytest.mark.parametrize(
    "query,metric_set",
    [
        (
            "请对每个授权商品分别列出支付金额、退款金额和净销售额的本期值、比较期值及差额。",
            {"net_revenue", "gross_revenue", "refund_amount"},
        ),
        ("逐商品列出本期与比较期的支付件数、退款件数及差额。", {"paid_units", "refunded_units"}),
    ],
)
async def test_unfulfilled_per_entity_requests_cannot_be_covered_and_batch_can_answer(
    query, metric_set
):
    case = CASES["D01"].model_copy(update={"query": query})
    with sandbox(diagnosis_runtime="v13", max_model_calls=3) as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, query, "agent")
        model = ScriptedDecisionModel(
            tool_proposal("analyze_refunds", {}),
            stop(),
            tool_proposal("rank_contributors", {"top_k": 1}),
        )
        await execute(repo, settings, model=model)
        r = collect(repo, task["id"], "agent")
        report = r["task"]["report"]
        assert r["task"]["validation"]["passed"], r["task"]
        assert report["stop_reason"] == "target_covered"
        assert r["steps"][1]["status"] == "REJECTED"
        assert r["steps"][1]["assessment"]["error_code"] == "incomplete_evidence"
        for sku in ["A", "B"]:
            for metric in metric_set:
                for part in ["current", "comparison", "delta"]:
                    fact = next(
                        f
                        for f in report["facts"]
                        if f["metric_key"] == f"component.{sku}.{metric}.{part}"
                    )
                    assert fact["entity_id"] == sku
                    assert fact["unit"] == ("count" if metric.endswith("units") else "CNY")
        rank = next(
            e["payload"] for e in r["evidence"] if e["payload"]["tool"] == "rank_contributors"
        )
        assert not numeric_errors(
            case.sales.model_dump(mode="json"),
            bundle,
            "rank_contributors",
            {"dimension": "sku", "top_k": 1, "filters": {"skus": ["A", "B"], "categories": []}},
            rank,
        )
        bad = copy.deepcopy(rank)
        key = "component.A.paid_units.current"
        bad["fact_catalog"][key]["value"] = "99999"
        assert numeric_errors(
            case.sales.model_dump(mode="json"),
            bundle,
            "rank_contributors",
            {"dimension": "sku", "top_k": 1, "filters": {"skus": ["A", "B"], "categories": []}},
            bad,
        )


async def test_per_sku_count_comparisons_are_a_valid_equivalent_path():
    query = "逐商品列出两期支付件数及差额。"
    case = CASES["D01"].model_copy(update={"query": query})
    with sandbox(diagnosis_runtime="v13") as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, query, "agent")
        model = ScriptedDecisionModel(
            *(
                tool_proposal(
                    "compare_periods", {"metric": "paid_units", "filters": {"skus": [sku]}}
                )
                for sku in ["A", "B"]
            )
        )
        await execute(repo, settings, model=model)
        r = collect(repo, task["id"], "agent")
        assert r["task"]["validation"]["passed"], r["task"]
        assert r["task"]["report"]["stop_reason"] == "target_covered"
        assert len(model.requests) == 2 and all(
            i["status"] == "acquired" for i in r["task"]["report"]["request_obligations"]
        )


@pytest.mark.parametrize(
    "wanted,wrong,label", [("category", "sku", "品类"), ("sku", "category", "商品")]
)
async def test_wrong_dimension_remains_a_gap_and_independent_grader_rejects_it(
    wanted, wrong, label
):
    raw = CASES["D13"].model_dump(mode="json")
    raw["query"] = f"按{label}核对两期净销售额的变化贡献并对账。"
    raw["expected"].update(reference_answer(Case.model_validate(raw)))
    case = Case.model_validate(raw)
    with sandbox(diagnosis_runtime="v13", max_model_calls=1) as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, case.query, "agent")
        model = ScriptedDecisionModel(tool_proposal("rank_contributors", {"dimension": wrong}))
        await execute(repo, settings, model=model)
        r = collect(repo, task["id"], "agent")
        report = r["task"]["report"]
        assert r["task"]["validation"]["passed"], r["task"]
        assert report["stop_reason"] == "model_budget"
        assert any("贡献维度" in q for q in report["open_question_details"])
        forged = copy.deepcopy(report)
        forged.update(
            stop_reason="target_covered",
            open_questions=[],
            open_question_details=[],
            gap_summary={"total": 0, "displayed": 0, "omitted": 0},
        )
        assert not validate_report(forged, r["task"], bundle, r["evidence"], r["steps"])["passed"]
        bad = copy.deepcopy(r)
        bad["task"]["report"] = forged
        assert grade(case, bundle, bad)["assertions"]["requested_dimension_bound"] is False
        # A separate task may explore the wrong dimension and then complete the right one.
        settings.max_model_calls = 3
        good_task = new_task(repo, user, conv, bundle, case.query, "agent")
        good_model = ScriptedDecisionModel(
            tool_proposal("rank_contributors", {"dimension": wrong}),
            stop(),
            tool_proposal("rank_contributors", {"dimension": wanted}),
        )
        await execute(repo, settings, model=good_model)
        good = collect(repo, good_task["id"], "agent")
        assert good["task"]["report"]["stop_reason"] == "target_covered", good["task"]
        assert grade(case, bundle, good)["automatic_passed"], grade(case, bundle, good)


async def test_all_gaps_are_retained_and_tampered_summary_or_short_list_is_rejected():
    raw = CASES["D01"].model_dump(mode="json")
    raw["query"] = "逐商品列出两期支付金额、退款金额和净销售额及差额。"
    raw["sales"]["products"].append({**raw["sales"]["products"][0], "sku": "C"})
    raw["sales"]["daily_sales"].extend(
        [{**r, "sku": "C"} for r in list(raw["sales"]["daily_sales"]) if r["sku"] == "A"]
    )
    raw["scope_skus"].append("C")
    case = Case.model_validate(raw)
    with sandbox(diagnosis_runtime="v13", max_model_calls=1) as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, case.query, "agent")
        await execute(
            repo, settings, model=ScriptedDecisionModel(tool_proposal("analyze_refunds", {}))
        )
        r = collect(repo, task["id"], "agent")
        report = r["task"]["report"]
        assert r["task"]["validation"]["passed"], r["task"]
        assert report["gap_summary"] == {"total": 27, "displayed": 20, "omitted": 7}
        assert len(report["open_question_details"]) == 27 and len(report["open_questions"]) == 20
        assert any("省略7项" in q for q in report["limitations"])
        for field in [
            "open_question_details",
            "gap_summary",
            "open_questions",
            "request_obligations",
        ]:
            bad = copy.deepcopy(report)
            if field == "gap_summary":
                bad[field]["omitted"] = 0
            else:
                bad[field] = bad[field][:-1]
            assert not validate_report(bad, r["task"], bundle, r["evidence"], r["steps"])[
                "passed"
            ], field
        from evaluations.diagnosis_request_checks import completion_checks

        bad = copy.deepcopy(report)
        bad["open_question_details"] = bad["open_questions"]
        bad["gap_summary"] = {"total": 20, "displayed": 20, "omitted": 0}
        assert not completion_checks(
            case.model_dump(mode="json"),
            bad,
            [e["payload"] for e in r["evidence"]],
            ["A", "B", "C"],
        )["complete_gap_inventory"]


async def test_v13_persisted_contract_survives_defaults_and_client_retry(setup):
    settings, repo, user, other = setup
    settings.diagnosis_runtime = "v13"
    case = CASES["D01"].model_copy(update={"query": "比较除商品 A 以外商品的两期净销售额及差额。"})
    conv, bundle, _ = admit(repo, user, other, case)
    request = TaskRequest(
        conversation_id=conv,
        query=case.query,
        profile="commerce_diagnosis",
        bundle_id=bundle["bundle_id"],
        sources=["sales"],
    )
    key = str(uuid4())
    task = repo.create_task(user, request, key)
    settings.diagnosis_runtime = "legacy"
    assert repo.create_task(user, request, key)["id"] == task["id"]
    await execute(repo, settings, model=ScriptedDecisionModel())
    r = collect(repo, task["id"], "agent")
    assert r["task"]["report"]["scope"]["skus"] == ["B"]
    assert r["task"]["report"]["versions"]["graph_version"] == "diagnosis-v13"
    assert any(a["name"].endswith(".pdf") for a in repo.artifacts(task["id"]))
    with pytest.raises(Conflict):
        repo.create_task(user, request.model_copy(update={"query": "另一问题"}), key)


def test_old_v12_contract_is_not_reparsed_by_new_defaults_on_retry(setup):
    settings, repo, user, other = setup
    settings.diagnosis_runtime = "v12"
    case = CASES["D01"]
    conv, bundle, _ = admit(repo, user, other, case)
    request = TaskRequest(
        conversation_id=conv,
        query="只看 A，同时排除 A 的净销售额",
        profile="commerce_diagnosis",
        bundle_id=bundle["bundle_id"],
        sources=["sales"],
    )
    key = str(uuid4())
    old = repo.create_task(user, request, key)
    settings.diagnosis_runtime = "v13"
    assert repo.create_task(user, request, key)["id"] == old["id"]


@pytest.mark.parametrize(
    "query,metrics,parts",
    [
        ("每个授权商品本期支付数量", ["paid_units"], ["current"]),
        ("各个商品前期退款数量", ["refunded_units"], ["comparison"]),
        ("所有已授权的商品两期净额", ["net_revenue"], ["current", "comparison"]),
        (
            "逐个商品本期支付额及退款额的差额",
            ["gross_revenue", "refund_amount"],
            ["current", "comparison", "delta"],
        ),
    ],
)
def test_contract_keeps_literal_metric_aliases_and_period_obligations(query, metrics, parts):
    contract = request_contract(query)
    assert set(contract["component_metrics"]) == set(metrics)
    assert contract["periods"] == parts


@pytest.mark.parametrize("case_id", ["D14", "D15"])
async def test_unsupported_only_requests_keep_specific_gap_instead_of_failing(case_id):
    case = CASES[case_id]
    with sandbox(diagnosis_runtime="v13") as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, case.query, "agent")
        model = ScriptedDecisionModel()
        await execute(repo, settings, model=model)
        run = collect(repo, task["id"], "agent")
        assert run["task"]["validation"]["passed"]
        assert run["task"]["report"]["stop_reason"] == "evidence_gap"
        assert not model.requests
        assert grade(case, bundle, run)["automatic_passed"]


def test_v13_prompt_frontier_uses_full_new_count_obligations():
    from app.commerce.answer_contract_v2 import bound_request
    from app.diagnosis.planner_v13 import prompt
    from app.diagnosis.schemas import SALES_TOOLS

    query = "逐商品列出两期支付件数、退款件数及差额"
    scope = {"skus": ["A", "B"], "objective": None}
    bundle = {
        "bundle_id": "b",
        "bundle_hash": "h",
        "period": {},
        "comparison_period": {},
        "allowed_skus": ["A", "B"],
    }
    task = {
        "query": query,
        "parameters": {
            "budget": {"max_tokens": 60000, "max_tool_calls": 10, "max_model_calls": 12},
            "sources": ["sales"],
            "answer_contract": bound_request(query, bundle),
        },
    }
    context = json.loads(
        prompt(
            task, bundle, scope, [], [], {k: v.model_json_schema() for k, v in SALES_TOOLS.items()}
        )[1][1]
    )
    assert len(context["component_obligations"]) == 12
    assert (
        context["capability_frontier"]["rank_contributors"][
            "pending_component_items_covered_in_one_call"
        ]
        == 12
    )
    assert (
        context["capability_frontier"]["compare_periods"][
            "remaining_components_require_at_least_calls"
        ]
        == 4
    )
    assert (
        context["capability_frontier"]["analyze_refunds"]["explicit_requirement_pending"] is False
    )


@pytest.mark.parametrize("sku_length", [8, 1000])
def test_large_v13_context_keeps_all_item_identities_or_stops_before_send(sku_length):
    from app.commerce.answer_contract_v2 import bound_request
    from app.diagnosis.planner_v13 import prompt
    from app.diagnosis.schemas import SALES_TOOLS
    from app.storage.repository import BudgetExceeded

    scope = {"skus": [f"CAP{i:03}" + "x" * sku_length for i in range(100)], "objective": None}
    bundle = {
        "bundle_id": "b",
        "bundle_hash": "h",
        "period": {},
        "comparison_period": {},
        "allowed_skus": scope["skus"],
    }
    query = "逐商品列出两期净销售额、支付金额、退款金额、支付件数、退款件数及差额"
    task = {
        "query": query,
        "parameters": {
            "budget": {"max_tokens": 60000, "max_tool_calls": 10, "max_model_calls": 12},
            "sources": ["sales"],
            "answer_contract": bound_request(query, bundle),
        },
    }
    try:
        messages = prompt(
            task, bundle, scope, [], [], {k: v.model_json_schema() for k, v in SALES_TOOLS.items()}
        )
    except BudgetExceeded as exc:
        assert str(exc) == "diagnosis_context_budget"
    else:
        context = json.loads(messages[1][1])
        assert len(messages[1][1].encode()) <= 96 * 1024
        assert len(context["component_obligations"]["rows"]) == 1500
        assert {r[0] for r in context["component_obligations"]["rows"]} == set(scope["skus"])
