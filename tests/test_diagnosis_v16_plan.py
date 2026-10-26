"""v3 sidecar preflight is metadata-only; no unused business case is executed."""

import json
from types import SimpleNamespace

import pytest

from evaluations import diagnosis, diagnosis_plan
from evaluations.diagnosis_grading import VERSION_CONTRACTS
from tests.test_diagnosis_plan import prepared_inputs as prepared_inputs
from tests.test_diagnosis_v15_plan import copied_packet


def packet(inputs):
    directory = copied_packet(inputs)
    path = directory / "semantic-contract.json"
    value = json.loads(path.read_bytes())
    value.update(
        schema_version=3,
        accounting_protocol=2,
        expected_runtime=VERSION_CONTRACTS["diagnosis-v16"],
        expected_fixed_runtime=VERSION_CONTRACTS["commerce-review-bundle-v5"],
    )
    path.write_text(json.dumps(value))
    return directory


def test_v3_requires_both_new_runtime_contracts_and_protocol(prepared_inputs):
    directory = packet(prepared_inputs)
    _, contract = diagnosis_plan.validate_cases(directory / "cases.json")
    assert contract["total_tasks"] == 96


@pytest.mark.parametrize("damage", ["protocol", "diagnosis", "fixed"])
def test_v3_cannot_downgrade_or_omit_accounting_contract(prepared_inputs, damage):
    directory = packet(prepared_inputs)
    path = directory / "semantic-contract.json"
    value = json.loads(path.read_bytes())
    if damage == "protocol":
        value.pop("accounting_protocol")
    else:
        value["expected_runtime" if damage == "diagnosis" else "expected_fixed_runtime"] = (
            VERSION_CONTRACTS[
                "diagnosis-v15" if damage == "diagnosis" else "commerce-review-bundle-v4"
            ]
        )
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        diagnosis_plan.validate_cases(directory / "cases.json")


async def test_new_runtime_mismatch_rejected_before_credentials(prepared_inputs, monkeypatch):
    directory = packet(prepared_inputs)
    _, contract = diagnosis_plan.validate_cases(directory / "cases.json")
    plan = {
        "schema_version": 1,
        "status": "FROZEN_AWAITING_EXPLICIT_PAID_AUTHORIZATION",
        "cases_file": "cases.json",
        "prior_input_hashes": [],
        **contract,
    }
    (directory / "manifest.json").write_text(json.dumps(plan))
    monkeypatch.setattr(
        "evaluations.diagnosis_runtime.EvaluationSettings",
        lambda **kwargs: SimpleNamespace(diagnosis_runtime="v15", commerce_review_answer_version=5),
    )

    def forbidden(*args, **kwargs):
        pytest.fail("credentials must not be read")

    monkeypatch.setattr(diagnosis, "live_configuration", forbidden)
    with pytest.raises(ValueError, match="运行版本"):
        await diagnosis.evaluate(SimpleNamespace(frozen_plan=directory, charge_paid_run=None))
