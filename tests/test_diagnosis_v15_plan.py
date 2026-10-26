"""v2 sidecar checks are metadata-only: fresh formal data never enters a graph."""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluations import diagnosis_plan as planning
from tests.test_diagnosis_plan import prepared_inputs as prepared_inputs

PACKET = Path("docs/acceptance/diagnosis-v15-h01-h20")


def copied_packet(inputs):
    directory, _, _ = inputs
    for name in ("cases.json", "semantic-contract.json", "answer-obligations.json"):
        shutil.copyfile(PACKET / name, directory / name)
    return directory


@pytest.mark.parametrize(
    "damage", ["target_only", "missing_item", "changed_sidecar", "wrong_version"]
)
def test_new_semantic_contract_cannot_silently_downgrade(prepared_inputs, damage):
    directory = copied_packet(prepared_inputs)
    semantic = json.loads((directory / "semantic-contract.json").read_bytes())
    if damage == "target_only":
        semantic["cases"]["H06"]["expected_stop_reasons"] = ["target_covered"]
    if damage == "missing_item":
        semantic["author_obligations"]["cases"][2]["scope_matrix"].pop()
    if damage == "changed_sidecar":
        author = json.loads((directory / "answer-obligations.json").read_bytes())
        author["cases"][2]["scope_matrix"].pop()
        (directory / "answer-obligations.json").write_text(json.dumps(author))
    if damage == "wrong_version":
        semantic["expected_fixed_runtime"]["graph_version"] = "commerce-review-bundle-v3"
    (directory / "semantic-contract.json").write_text(json.dumps(semantic))
    with pytest.raises(ValueError):
        planning.validate_cases(directory / "cases.json")


async def test_runtime_mismatch_blocks_before_credentials_or_http(prepared_inputs, monkeypatch):
    from evaluations import diagnosis

    directory = copied_packet(prepared_inputs)
    # Isolate the runtime gate with a metadata-only manifest; no graph or provider.
    _, contract = planning.validate_cases(directory / "cases.json")
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
        lambda **kwargs: SimpleNamespace(diagnosis_runtime="v14", commerce_review_answer_version=4),
    )

    def no_credentials(*args, **kwargs):
        pytest.fail("must not read credentials")

    monkeypatch.setattr(diagnosis, "live_configuration", no_credentials)
    with pytest.raises(ValueError, match="运行版本"):
        await diagnosis.evaluate(SimpleNamespace(frozen_plan=directory, charge_paid_run=None))
