"""Independent counterexamples from the first failed paid H01 probe."""

import copy
import json
from pathlib import Path

import pytest

from app.diagnosis.inputs import get_bundle
from app.diagnosis.planner_v16 import prompt
from app.diagnosis.tools import DiagnosisToolGateway
from evaluations import diagnosis_probe as probe
from evaluations.diagnosis_probe_accounting import HOLD, LIMIT, file_hash
from tests.test_diagnosis_probe import new_probe_task
from tests.test_diagnosis_probe import (
    test_complete_one_task_probe_has_exact_spend_and_stops_on_first_error as check_probe,
)


def context(setup, *, reserve=True):
    task = new_probe_task(setup, group="agent")
    _, repo, user, _ = setup
    task = copy.deepcopy(task)
    task["parameters"]["budget"]["max_model_calls"] = 4
    if reserve:
        task["parameters"]["budget"]["decision_call_reserve"] = 0
    gateway = DiagnosisToolGateway(repo, repo.claim())
    bundle = get_bundle(repo, user, task["bundle_id"])
    scope = {"skus": bundle["allowed_skus"], "objective": None}
    return json.loads(prompt(task, bundle, scope, [], [], gateway.catalog())[1][1])


def test_explicit_zero_reserve_is_reported_as_four_decision_calls(setup):
    c = context(setup)
    assert c["remaining_budget"]["model_calls"] == 4
    assert c["remaining_budget"]["decision_calls"] == 4


def test_legacy_absent_reserve_keeps_three_finalization_slots(setup):
    c = context(setup, reserve=False)
    assert c["remaining_budget"]["decision_calls"] == 1


def test_refund_capability_declares_the_gross_amounts_it_actually_returns(setup):
    c = context(setup)
    outputs = c["capability_frontier"]["analyze_refunds"]["outputs"]
    assert any("gross_revenue" in item for item in outputs)
    assert any("refund_amount" in item for item in outputs)


async def test_driver_freezes_its_two_http_limit_in_the_task(tmp_path, monkeypatch):
    original = probe.collect

    def inspect(*args, **kw):
        record = original(*args, **kw)
        assert record["task"]["parameters"]["budget"]["max_model_calls"] == 2
        assert record["task"]["parameters"]["budget"]["decision_call_reserve"] == 0
        return record

    monkeypatch.setattr(probe, "collect", inspect)
    await check_probe(tmp_path, monkeypatch, "valid", 2)


async def test_one_call_fixed_probe_refuses_before_loading_credentials(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    source = Path("docs/acceptance/diagnosis-v15-h01-h20/cases.json").resolve()
    (legacy / "run.json").write_text(json.dumps({"cases_file_sha256": file_hash(source)}))
    (legacy / "progress.jsonl").write_text(json.dumps({"case_id": "H03", "group": "fixed"}) + "\n")
    monkeypatch.setattr(
        probe,
        "load_parent",
        lambda *a, **kw: {
            "known_tokens": 4,
            "frozen_unknown_tokens": HOLD,
            "confirmed_http_requests": 1,
            "directory": str(legacy),
            "legacy_root": str(legacy),
            "run_id": "local",
            "parent_failed": False,
        },
    )

    def forbidden(*a, **kw):
        pytest.fail("probe reached credentials with a structurally insufficient fixed-call budget")

    monkeypatch.setattr(probe, "live_configuration", forbidden)
    args = probe.parser().parse_args(
        [
            "--parent",
            str(tmp_path / "hold.json"),
            "--legacy-root",
            str(legacy),
            "--cases",
            str(source),
            "--case-id",
            "H03",
            "--group",
            "fixed",
            "--output",
            str(tmp_path / "out"),
            "--max-http",
            "1",
            "--max-total-tokens",
            str(LIMIT),
            "--allow-paid",
            "--api-key-env",
            "NEVER_READ",
        ]
    )
    with pytest.raises(ValueError, match="minimum"):
        await probe.run(args)
    assert not (tmp_path / "out").exists()


def test_one_refund_query_completes_the_scope_identity_after_ranked_components(setup):
    from app.diagnosis.completion_v15 import amount_witness, completion

    query = "请对每个授权商品分别列出支付金额、退款金额和净销售额的本期值、比较期值及差额；按商品核对净销售额变化贡献并对账。"
    task = new_probe_task(setup, group="agent", query=query)
    _, repo, _, _ = setup
    gateway = DiagnosisToolGateway(repo, repo.claim())
    scope = {"skus": ["A", "B"], "objective": None}
    filters = {"skus": scope["skus"]}
    gateway.invoke("inspect_dataset", {"filters": filters}, "baseline-inspect")
    gateway.invoke(
        "compare_periods", {"metric": "net_revenue", "filters": filters}, "baseline-compare"
    )
    gateway.invoke("rank_contributors", {"dimension": "sku", "filters": filters}, "rank")
    assert amount_witness(repo.evidence(task["id"]), scope) is None
    result = gateway.invoke("analyze_refunds", {"filters": filters}, "refund")
    for metric in ["gross_revenue", "refund_amount"]:
        for part in ["current", "comparison", "delta"]:
            assert f"{metric}.{part}" in result["fact_catalog"]
    witness = amount_witness(repo.evidence(task["id"]), scope)
    assert witness and len(witness["fact_refs"]) == 9
    assert completion(query, repo.evidence(task["id"]), scope)["status"] == "fulfilled"


@pytest.mark.parametrize(
    "limit,budget,expected",
    [
        (12, {}, 9),
        (2, {}, 2),
        (2, {"decision_call_reserve": 0}, 2),
        (4, {"decision_call_reserve": 1}, 3),
    ],
)
def test_frozen_decision_limit_preserves_legacy_and_explicit_policy(limit, budget, expected):
    from app.diagnosis.planner_budget import decision_limit

    assert decision_limit(limit, budget) == expected


@pytest.mark.parametrize("reserve", [-1, True, 1.5, 12, 2])
def test_invalid_explicit_decision_reserve_is_rejected(reserve):
    from app.diagnosis.planner_budget import decision_limit

    with pytest.raises(ValueError):
        decision_limit(2, {"decision_call_reserve": reserve})


@pytest.mark.parametrize(
    "case_id,group,cap,minimum",
    [
        ("H01", "agent", 2, 2),
        ("H13", "agent", 4, 3),
        ("H03", "fixed", 2, 2),
        ("H06", "agent", 1, 0),
    ],
)
def test_structural_minimum_uses_actual_request_and_coverage(case_id, group, cap, minimum):
    from evaluations.diagnosis_cases import Case

    cases = json.loads(Path("docs/acceptance/diagnosis-v15-h01-h20/cases.json").read_bytes())
    case = next(Case.model_validate(c) for c in cases["cases"] if c["case_id"] == case_id)
    assert probe.validate_probe_http_budget(case, group, cap)["minimum_known_calls"] == minimum
    if minimum > 1:
        with pytest.raises(ValueError, match="minimum"):
            probe.validate_probe_http_budget(case, group, minimum - 1)
