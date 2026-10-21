"""Independent acceptance counterexamples; synthetic gate fixtures are not model evidence."""

import copy
import json

import pytest

from evaluations.diagnosis import review_template
from evaluations.diagnosis_capabilities import literal_net_comparison
from evaluations.diagnosis_grading import sha
from evaluations.diagnosis_review import review_run
from tests.test_diagnosis_evaluation import completed_reviews
from tests.test_diagnosis_evaluation import receipt as receipt


def comparison():
    business = {
        "query": "比较两期净销售额的金额变化",
        "scope_skus": ["X", "Y"],
        "period": {"start": "2026-09-01", "end": "2026-09-01"},
        "comparison_period": {"start": "2026-08-01", "end": "2026-08-01"},
        "sales": {
            "products": [{"sku": sku, "active_from": "2026-08-01"} for sku in ("X", "Y")],
            "daily_sales": [
                {
                    "sku": sku,
                    "day": day,
                    "revenue_cents": amount,
                    "refund_cents": 0,
                    "paid_units": 1,
                    "refunded_units": 0,
                }
                for sku, day, amount in (
                    ("X", "2026-08-01", 1200),
                    ("Y", "2026-08-01", 800),
                    ("X", "2026-09-01", 1000),
                    ("Y", "2026-09-01", 500),
                )
            ],
        },
    }
    values = {
        "net_revenue.current": "15.00",
        "net_revenue.comparison": "20.00",
        "net_revenue.delta": "-5.00",
    }
    report = {
        "scope": {"skus": ["X", "Y"]},
        "facts": [
            {"metric_key": key, "entity_id": "X,Y", "value": value} for key, value in values.items()
        ],
    }
    source = {
        "tool": "compare_periods",
        "metric": "net_revenue",
        "status": "ok",
        "scope": {"skus": ["X", "Y"]},
        "fact_catalog": {key: {} for key in values},
    }
    return business, [source], report


@pytest.mark.parametrize("question", ["比较两期净销售额", "两期销售金额有哪些变化"])
def test_literal_amount_questions_accept_exact_comparison_without_ranking(question):
    business, sources, report = comparison()
    business["query"] = question
    assert literal_net_comparison(business, sources, report)


@pytest.mark.parametrize(
    "question",
    [
        "按商品分解两期净销售额贡献",
        "为什么两期净销售额下降",
        "比较两期销售额和退款件数",
        "按日比较两期净销售额",
        "比较两期销售额和利润",
        "比较两期支付金额",
    ],
)
def test_literal_comparison_never_substitutes_different_requested_work(question):
    business, sources, report = comparison()
    assert literal_net_comparison(business, sources, report)
    business["query"] = question
    assert not literal_net_comparison(business, sources, report)


@pytest.mark.parametrize(
    "defect", ["missing_row", "missing_delta", "wrong_scope", "wrong_value", "no_source"]
)
def test_literal_comparison_requires_complete_exact_bound_amounts(defect):
    business, sources, report = comparison()
    assert literal_net_comparison(business, sources, report)
    if defect == "missing_row":
        business["sales"]["daily_sales"].pop()
    elif defect == "missing_delta":
        report["facts"].pop()
    elif defect == "wrong_scope":
        report["facts"][0]["entity_id"] = "X"
    elif defect == "wrong_value":
        report["facts"][0]["value"] = "16.00"
    else:
        sources.clear()
    assert not literal_net_comparison(business, sources, report)


def synthetic_formal(receipt, root, defect):
    """Only importer policy: clone known receipts, explicitly no quality experiment."""
    original_root = receipt[0]
    meta = json.loads((original_root / "run.json").read_text())
    expected = json.loads((original_root / "expectations-manifest.json").read_text())[0]["expected"]
    ids = [f"H{i:02}" for i in range(1, 21)]
    meta["planned"] = {"case_ids": ids, "split": "holdout", "repeats": 3, "mode": "live"}
    expected_by_id = {cid: {**expected, "critical": index == 19} for index, cid in enumerate(ids)}
    meta["expected_sha256"] = sha(list(expected_by_id.values()))
    root.mkdir()
    (root / "run.json").write_text(json.dumps(meta))
    (root / "expectations-manifest.json").write_text(
        json.dumps([{"case_id": cid, "expected": expected_by_id[cid]} for cid in ids])
    )
    rows = []
    for index, cid in enumerate(ids):
        for repeat in range(1, 4):
            folder = root / f"{cid}-{repeat}"
            folder.mkdir()
            (folder / "expectations.json").write_text(json.dumps(expected_by_id[cid]))
            for group in ("fixed", "agent"):
                if defect == "missing_baseline" and index == 0 and repeat == 1 and group == "fixed":
                    continue
                record = json.loads((original_root / f"D01-1/{group}.json").read_text())
                row = record["measurement"]
                row.update(
                    case_id=cid,
                    repeat=repeat,
                    critical=index == 19,
                    receipt=f"{cid}-{repeat}/{group}.json",
                )
                if group == "agent":
                    failed_cases = {
                        "completion": 1,
                        "completion_boundary": 2,
                        "below_target": 3,
                    }.get(defect, 0)
                    if index < failed_cases:
                        record["grading"]["assertions"]["expected_outcome"] = False
                    elif defect == "unsafe" and index == 0 and repeat == 1:
                        record["grading"]["assertions"]["facts_match_raw_records"] = False
                    elif defect == "unfair" and index == 0 and repeat == 1:
                        row["budget"] = {**row["budget"], "max_model_calls": 99}
                record["grading"]["automatic_passed"] = all(
                    record["grading"]["assertions"].values()
                )
                row["automatic_passed"] = record["grading"]["automatic_passed"]
                (folder / f"{group}.json").write_text(json.dumps(record))
                rows.append(copy.deepcopy(row))
    (root / "progress.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    (root / "summary.json").write_text(json.dumps({"executed_runs": len(rows), "stopped": None}))
    (root / "review-template.json").write_text(json.dumps(review_template(root, rows)))
    reviews = root / "synthetic-reviews.json"
    reviews.write_text(json.dumps(completed_reviews(root)))
    return review_run(root, reviews)


@pytest.mark.parametrize(
    "defect,eligible",
    [
        ("none", True),
        ("completion", True),
        ("completion_boundary", True),
        ("below_target", False),
        ("unsafe", False),
        ("missing_baseline", False),
        ("unfair", False),
    ],
)
def test_review_enforces_ninety_percent_completion_and_all_safety_fair_pairs(
    receipt, tmp_path, defect, eligible
):
    result = synthetic_formal(receipt, tmp_path / "synthetic-policy-only", defect)
    assert result["release_eligible"] is eligible
    if defect == "completion":
        assert result["expected_behavior_rate"] == 0.95
        assert result["automatic_safety_all_passed"]
        assert len(result["failures"]) == 3  # Ordinary failures remain, never erased.
    if defect == "unsafe":
        assert not result["automatic_safety_all_passed"]
