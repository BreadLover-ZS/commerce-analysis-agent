"""The evaluation oracle checks raw rows rather than calling production truth code."""

import copy

import pytest

from app.diagnosis.tools import DiagnosisToolGateway
from evaluations.diagnosis_oracle import numeric_errors, raw_totals
from tests.test_diagnosis_extensions import extended_task
from tests.test_diagnosis_sources import stock_payload
from tests.test_diagnosis_tools import gateway, oracle_payload


@pytest.mark.parametrize(
    "tool,args",
    [
        ("inspect_dataset", {}),
        ("compare_periods", {}),
        ("compare_periods", {"metric": "paid_units", "filters": {"skus": ["B"]}}),
        ("rank_contributors", {"top_k": 1}),
        ("rank_contributors", {"dimension": "category", "top_k": 1}),
        ("analyze_refunds", {}),
        ("drilldown_series", {"filters": {"skus": ["A"]}}),
    ],
)
def test_independent_oracle_checks_complete_catalog_and_structural_results(setup, tool, args):
    tools, _, bundle = gateway(setup)
    result = tools.invoke(tool, args, "step-1")
    sales = oracle_payload().model_dump(mode="json")
    assert numeric_errors(sales, bundle, tool, args, result) == []
    if result["fact_catalog"]:
        wrong = copy.deepcopy(result)
        next(iter(wrong["fact_catalog"].values()))["value"] = "9999.00"
        assert any(
            "fact_mismatch" in error for error in numeric_errors(sales, bundle, tool, args, wrong)
        )


def test_independent_catalog_does_not_accept_self_consistent_but_wrong_money(setup):
    tools, _, bundle = gateway(setup)
    result = tools.invoke("compare_periods", {}, "step-1")
    # Matching the returned raw_value and formatted value is insufficient.
    fact = result["fact_catalog"]["net_revenue.current"]
    fact.update(raw_value=999900, value="9999.00")
    assert "independent_fact_mismatch:net_revenue.current" in numeric_errors(
        oracle_payload().model_dump(mode="json"), bundle, "compare_periods", {}, result
    )


def test_independent_stock_oracle_covers_missing_lead_and_snapshot(setup):
    _, repo, _, _ = setup
    _, bundle, _ = extended_task(setup, sources=["sales", "inventory"])
    tools = DiagnosisToolGateway(repo, repo.claim())
    result = tools.invoke("inventory_risk", {}, "step-1")
    sales, stock = oracle_payload().model_dump(mode="json"), stock_payload().model_dump(mode="json")
    assert numeric_errors(sales, bundle, "inventory_risk", {}, result, stock) == []
    result["items"][1]["below_lead_proxy"] = False
    assert "independent_structure_mismatch:items" in numeric_errors(
        sales, bundle, "inventory_risk", {}, result, stock
    )


def test_oracle_rejects_extra_facts_unknown_tools_and_unauthorized_filter(setup):
    tools, _, bundle = gateway(setup)
    result = tools.invoke("compare_periods", {}, "step-1")
    sales = oracle_payload().model_dump(mode="json")
    wrong = copy.deepcopy(result)
    wrong["fact_catalog"]["profit"] = wrong["fact_catalog"]["net_revenue.current"]
    assert "fact_key_set_mismatch" in numeric_errors(sales, bundle, "compare_periods", {}, wrong)
    assert numeric_errors(sales, bundle, "unknown_tool", {}, result) == [
        "invalid_or_unknown_oracle_input"
    ]
    assert numeric_errors(
        sales, bundle, "compare_periods", {"filters": {"skus": ["A", "FOREIGN"]}}, result
    ) == ["invalid_or_unknown_oracle_input"]


def test_raw_oracle_keeps_missing_pre_activation_and_zero_distinct():
    sales = oracle_payload().model_dump(mode="json")
    _, before_launch = raw_totals(sales, ["A"], {"start": "2026-07-01", "end": "2026-07-01"})
    _, missing = raw_totals(sales, ["A"], {"start": "2026-08-02", "end": "2026-08-02"})
    sales["daily_sales"][0].update(revenue_cents=0, refund_cents=0, paid_units=0, refunded_units=0)
    zero_values, zero = raw_totals(sales, ["A"], {"start": "2026-08-01", "end": "2026-08-01"})
    assert before_launch["expected_records"] == before_launch["missing_records"] == 0
    assert missing["missing_records"] == 1 and not missing["complete"]
    assert zero["complete"] and zero["observed_records"] == 1 and not any(zero_values.values())
