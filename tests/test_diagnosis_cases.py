"""Corpus integrity and independent money checks; not Agent or holdout quality scores."""

import hashlib
import json
from collections import Counter

import pytest

from app.diagnosis.inputs import digest
from app.diagnosis.metrics import DiagnosticMetrics
from app.diagnosis.schemas import Filters
from evaluations.diagnosis_cases import DEFAULT_CASES, business_input, load_cases

CASES, _ = load_cases()
MATH_CASES = [c for c in CASES if c.split != "holdout" and c.execution == "diagnostic"]


def test_sixty_cases_have_frozen_splits_and_disjoint_business_inputs():
    assert len(CASES) == 60
    assert Counter(c.split for c in CASES) == {"development": 20, "regression": 20, "holdout": 20}
    seen = set()
    for case in CASES:
        value = business_input(case)
        assert not {"expected", "facet", "split", "case_id", "harness"} & set(value)
        value.pop("query")
        key = digest(value)
        assert key not in seen, case.case_id
        seen.add(key)
    assert all(c.execution == "diagnostic" for c in CASES if c.split == "holdout")
    targets = json.loads(DEFAULT_CASES.with_name("diagnosis_targets_v1.json").read_text())
    assert targets["cases_canonical_sha256"] == load_cases()[1]
    assert targets["case_file_sha256"] == hashlib.sha256(DEFAULT_CASES.read_bytes()).hexdigest()


@pytest.mark.parametrize("case", MATH_CASES, ids=lambda c: c.case_id)
def test_development_regression_math_matches_independent_literal_expected(case):
    # Scope/period/source data are inputs. No case label or expected answer is
    # sent to production calculations, and no production function generates truth.
    raw = business_input(case)
    bundle = {
        "bundle_id": "fixture",
        "bundle_hash": digest(raw),
        "metric_version": "diagnosis-v1",
        "sample": True,
        "allowed_skus": case.scope_skus,
        "allowed_categories": sorted(
            {p.category for p in case.sales.products if p.sku in case.scope_skus}
        ),
        "period": raw["period"],
        "comparison_period": raw["comparison_period"],
    }
    calculation = DiagnosticMetrics(raw["sales"], bundle, Filters(skus=case.scope_skus))
    result = calculation.compare("net_revenue")
    facts = result["fact_catalog"]
    assert facts["net_revenue.current"]["raw_value"] == case.expected.net_cents
    assert facts["net_revenue.comparison"]["raw_value"] == case.expected.comparison_net_cents
    assert result["comparable"] == case.expected.comparable
    if case.expected.comparable:
        assert facts["net_revenue.delta"]["raw_value"] == case.expected.delta_cents
        rank = calculation.rank("sku", 1)
        reconciliation = rank["reconciliation"]
        assert reconciliation["total_delta_cents"] == case.expected.delta_cents
        assert (
            reconciliation["shown_delta_cents"] + reconciliation["other_delta_cents"]
            == case.expected.delta_cents
        )
    else:
        assert "net_revenue.delta" not in facts


def test_invalid_oracle_is_rejected_instead_of_silently_grading_self(tmp_path):
    value = json.loads(DEFAULT_CASES.read_text())
    value["cases"][0]["expected"]["net_cents"] += 1
    path = tmp_path / "wrong-cases.json"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="独立预期"):
        load_cases(path)


def test_subset_selection_preserves_ids_split_and_hash():
    rows, fingerprint = load_cases(split="regression", case_ids=["R01", "R02"])
    assert [c.case_id for c in rows] == ["R01", "R02"]
    assert fingerprint == load_cases(split="regression", case_ids=["R01", "R02"])[1]
    with pytest.raises(ValueError):
        load_cases(split="holdout", case_ids=["D01"])
