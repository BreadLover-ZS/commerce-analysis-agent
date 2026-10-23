"""Request completeness and adversarial public-advice regressions; no paid models."""

import copy

import pytest
from pydantic import ValidationError

from app.commerce.answer_contract import request_contract
from app.commerce.review_actions import action_catalog, render_actions
from app.diagnosis.planner_v11 import missing_coverage, unsupported_question
from app.diagnosis.reporting import validate_report
from app.research.context import evidence_package
from app.research.evidence import validate_report as validate_fixed
from app.research.schemas import ResearchReport, ReviewActionV3
from evaluations.diagnosis_cases import load_cases
from evaluations.diagnosis_oracle import fixed_numeric_errors, numeric_errors
from evaluations.diagnosis_runtime import admit, collect, execute, new_task, sandbox
from tests.test_diagnosis_graph import tool_proposal
from tests.test_diagnosis_model_calls import ScriptedDecisionModel

CASES = {c.case_id: c for c in load_cases()[0] if c.split != "holdout"}


@pytest.mark.parametrize(
    "phrase",
    ["不算利润", "不计算利润", "不用计算利润", "不需要计算利润", "利润不能算", "无需核算利润"],
)
def test_negative_metric_is_retained_as_exclusion_not_a_whole_question_veto(phrase):
    query = "核对退款金额，" + phrase
    c = request_contract(query)
    assert not unsupported_question(query)
    assert c["goals"] == ["refunds"]
    assert not c["unsupported_requests"]
    assert any(i["metric"] == "profit" and i["polarity"] == "excluded" for i in c["items"])


@pytest.mark.parametrize("phrase", ["不能不计算利润", "计算利润", "并非不计算利润"])
def test_positive_or_double_negative_unsupported_request_remains_a_gap(phrase):
    c = request_contract("核对退款金额；" + phrase)
    assert c["unsupported_requests"] == ["profit"]
    assert c["goals"] == ["refunds"]
    assert not unsupported_question("核对退款金额；" + phrase)
    assert unsupported_question(phrase)


async def test_completed_components_stop_without_another_model_request_and_missing_items_do_not():
    case = CASES["D01"]
    case = case.model_copy(update={"query": "请逐商品列出两期净销售额、支付金额和退款金额及差额"})
    with sandbox(diagnosis_runtime="v11") as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, case.query, "agent")
        model = ScriptedDecisionModel(
            tool_proposal("rank_contributors", {"dimension": "sku", "top_k": 1}),
            tool_proposal("analyze_refunds", {}),
        )
        await execute(repo, settings, model=model)
        r = collect(repo, task["id"], "agent")
        assert r["task"]["validation"]["passed"], r["task"]
        assert r["task"]["report"]["stop_reason"] == "target_covered"
        assert len(model.requests) == 2
        assert not missing_coverage(case.query, r["evidence"], r["task"]["report"]["scope"])
        rank = next(
            e["payload"] for e in r["evidence"] if e["payload"]["tool"] == "rank_contributors"
        )
        assert not numeric_errors(
            case.sales.model_dump(mode="json"),
            bundle,
            "rank_contributors",
            {
                "dimension": "sku",
                "top_k": 1,
                "filters": {"skus": case.scope_skus, "categories": []},
            },
            rank,
        )
        changed = copy.deepcopy(rank)
        key = changed["component_fact_keys"][0]
        changed["fact_catalog"][key]["value"] = "99999.00"
        assert numeric_errors(
            case.sales.model_dump(mode="json"),
            bundle,
            "rank_contributors",
            {
                "dimension": "sku",
                "top_k": 1,
                "filters": {"skus": case.scope_skus, "categories": []},
            },
            changed,
        )
        partial = [e for e in r["evidence"] if e["payload"]["tool"] != "rank_contributors"]
        assert any(
            g.startswith("必答项")
            for g in missing_coverage(case.query, partial, r["task"]["report"]["scope"])
        )
        wrong = copy.deepcopy(r["task"]["report"])
        wrong["open_questions"] = ["所问指标或操作超出当前数据与只读边界"]
        assert not validate_report(wrong, r["task"], bundle, r["evidence"], r["steps"])["passed"]


@pytest.mark.parametrize("mixed", [False, True])
async def test_refund_answer_not_swallowed_by_denied_or_requested_profit(mixed):
    case = CASES["D11"].model_copy(
        update={"query": "仅比较退款金额与记账占比，" + ("并计算利润" if mixed else "不算利润")}
    )
    with sandbox(diagnosis_runtime="v11") as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, case.query, "additional")
        model = ScriptedDecisionModel(tool_proposal("analyze_refunds", {}))
        await execute(repo, settings, model=model)
        r = collect(repo, task["id"], "additional")
        assert r["task"]["validation"]["passed"], r["task"]
        assert len(model.requests) == 1
        assert any(f["metric_key"] == "refund_amount.current" for f in r["task"]["report"]["facts"])
        assert ("请求指标 profit" in str(r["task"]["report"]["open_questions"])) == mixed
        assert r["task"]["report"]["stop_reason"] == ("evidence_gap" if mixed else "target_covered")


@pytest.mark.parametrize("case_id", ["D01", "D02", "D05", "D08", "D09", "D15"])
async def test_fixed_v3_facts_context_and_adversarial_recommendations(case_id):
    case = CASES[case_id]
    with sandbox(commerce_review_answer_version=3) as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, case.query, "fixed")
        settings.commerce_review_answer_version = 1
        await execute(repo, settings)
        r = collect(repo, task["id"], "fixed")
        assert r["task"]["validation"]["passed"], r["task"]
        assert r["task"]["graph_version"] == "commerce-review-bundle-v3"
        payload = r["evidence"][0]["payload"]
        assert payload["answer_version"] == 3
        assert not fixed_numeric_errors(case.sales.model_dump(mode="json"), bundle, payload)
        displayed = {
            m["key"]
            for s in r["task"]["report"]["sections"]
            for c in s["claims"]
            for m in c["metrics"]
        }
        if "comparison_net_revenue" in payload["metrics"]:
            assert "comparison_net_revenue" in displayed
        package = evidence_package(r["evidence"], case.query)
        packed = package["sources"][0]["payload"]
        assert packed["direct_answers"] == payload["direct_answers"]
        assert packed["answer_contract"] == payload["answer_contract"]
        assert packed["allowed_actions"] == action_catalog(payload)
        bad = copy.deepcopy(r["task"]["report"])
        bad["sections"][4]["claims"][0]["text"] = "商品启用前的所有日期必须补零，才能计算贡献。"
        assert not validate_fixed(ResearchReport.model_validate(bad), r["evidence"])["passed"]
        action = {
            "action_id": "verify_activation",
            **{
                k: v for k, v in action_catalog(payload)["verify_activation"].items() if k != "text"
            },
        }
        assert "启用前不适用" in render_actions([action], payload)[0]
        with pytest.raises(ValueError, match="premise_or_scope"):
            render_actions([{**action, "scope_skus": ["foreign"]}], payload)
        with pytest.raises(ValueError, match="premise_or_scope"):
            render_actions([{**action, "precondition_id": "pre_activation_missing"}], payload)
        with pytest.raises(ValidationError):
            ReviewActionV3.model_validate({**action, "text": "先补零"})


async def test_missing_refund_obligation_cannot_be_completed_when_model_budget_ends():
    case = CASES["D01"].model_copy(update={"query": "逐商品列出净销售额、支付金额和退款金额变化"})
    with sandbox(diagnosis_runtime="v11", max_model_calls=1) as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, case.query, "agent")
        model = ScriptedDecisionModel(tool_proposal("rank_contributors", {}))
        await execute(repo, settings, model=model)
        r = collect(repo, task["id"], "agent")
        assert r["task"]["validation"]["passed"], r["task"]
        assert r["task"]["report"]["stop_reason"] == "model_budget"
        assert "退款金额、件数和分母核对" in r["task"]["report"]["open_questions"]
        assert len(model.requests) == 1


async def test_v11_saved_contract_survives_default_change_and_two_call_budget(setup):
    settings, repo, user, other = setup
    settings.diagnosis_runtime, settings.max_model_calls = "v11", 2
    case = CASES["D01"].model_copy(update={"query": "逐商品列两期净销售额、支付金额及退款金额变化"})
    conv, bundle, _ = admit(repo, user, other, case)
    task = new_task(repo, user, conv, bundle, case.query, "agent")
    settings.diagnosis_runtime = "v10"
    model = ScriptedDecisionModel(
        tool_proposal("rank_contributors", {}), tool_proposal("analyze_refunds", {})
    )
    await execute(repo, settings, model=model)
    r = collect(repo, task["id"], "agent")
    assert r["task"]["validation"]["passed"], r["task"]
    assert r["task"]["graph_version"] == "diagnosis-v11"
    assert r["task"]["report"]["stop_reason"] == "target_covered"
    assert len(model.requests) == 2
    assert any(a["name"].endswith(".pdf") for a in repo.artifacts(task["id"]))


def test_not_applicable_period_has_no_fabricated_zero_or_delta_obligation():
    from app.diagnosis.planner_v11 import component_obligations

    evidence = [
        {
            "payload": {
                "fact_catalog": {},
                "component_coverage": {
                    "NEW": {
                        "current": {"expected_records": 1},
                        "comparison": {"expected_records": 0},
                    }
                },
            }
        }
    ]
    items = component_obligations("逐商品核对支付金额", evidence, {"skus": ["NEW"]})
    assert [(i["part"], i["status"]) for i in items] == [
        ("current", "missing_source"),
        ("comparison", "not_applicable"),
        ("delta", "not_applicable"),
    ]
    assert all(i["fact_key"] is None for i in items)


async def test_display_capacity_cannot_silently_complete_and_fixed_lists_omitted_entities():
    from evaluations.diagnosis_cases import Case

    raw = CASES["D01"].model_dump(mode="json")
    product = raw["sales"]["products"][0]
    rows = [r for r in raw["sales"]["daily_sales"] if r["sku"] == product["sku"]]
    skus = [f"CAP{i:02}" for i in range(30)]
    raw["sales"]["products"] = [{**product, "sku": sku, "name": sku} for sku in skus]
    raw["sales"]["daily_sales"] = [{**r, "sku": sku} for sku in skus for r in rows]
    raw["scope_skus"] = skus
    raw["query"] = "逐商品列出两期支付金额、退款金额和净销售额及差额"
    case = Case.model_validate(raw)
    with sandbox(diagnosis_runtime="v11", commerce_review_answer_version=3) as (
        settings,
        repo,
        user,
        other,
    ):
        conv, bundle, _ = admit(repo, user, other, case)
        agent = new_task(repo, user, conv, bundle, case.query, "agent")
        fixed = new_task(repo, user, conv, bundle, case.query, "fixed")
        await execute(repo, settings)
        await execute(repo, settings)
        diagnosis, review = repo.get_task(agent["id"]), repo.get_task(fixed["id"])
        assert diagnosis["validation"]["passed"], diagnosis
        assert len(diagnosis["report"]["facts"]) == 250
        assert diagnosis["report"]["stop_reason"] == "evidence_gap"
        assert "未展示必答项" in str(diagnosis["report"]["open_questions"])
        assert review["validation"]["passed"], review
        notes = review["report"]["limitations"]
        omitted = next(n for n in notes if "不能视为已完整展示" in n)
        payload = repo.evidence(fixed["id"])[0]["payload"]
        package = evidence_package(repo.evidence(fixed["id"]), case.query)
        assert package["sources"] and not package["manifest"]["excluded_ids"]
        packed = package["sources"][0]["payload"]
        assert packed["direct_answers"] == payload["direct_answers"]
        assert packed["scope"]["skus"] == skus
        assert all(
            key in packed["fact_catalog"]
            for entry in payload["dimensions"]["products"][:4]
            for key in entry["component_fact_keys"].values()
        )
        assert all(e["entity"] in omitted for e in payload["dimensions"]["products"][4:])
        report = ResearchReport.model_validate(review["report"])
        report.limitations = [n for n in notes if "不能视为已完整展示" not in n]
        assert not validate_fixed(report, repo.evidence(fixed["id"]))["passed"]
