"""Admission tests use synthetic accounting files only; no credential or HTTP calls."""

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from app.core.model_boundary import fingerprint
from evaluations import diagnosis
from evaluations import diagnosis_plan as planning
from evaluations.diagnosis_budget import prior_spend
from evaluations.diagnosis_grading import VERSION_CONTRACTS
from evaluations.diagnosis_reconciliation import candidate
from tests.test_diagnosis_plan import prepared_inputs as prepared_inputs
from tests.test_diagnosis_reconciliation import previous as previous
from tests.test_diagnosis_reconciliation import unsent_previous as unsent_previous
from tests.test_diagnosis_v15_plan import copied_packet


def packet(inputs):
    directory, cases, prior = inputs
    original = cases.read_bytes()
    (prior / "cases-source.json").write_bytes(original)
    meta = json.loads((prior / "run.json").read_bytes())
    meta["cases_file_sha256"] = hashlib.sha256(original).hexdigest()
    (prior / "run.json").write_text(json.dumps(meta))
    copied_packet(inputs)
    path = directory / "semantic-contract.json"
    value = json.loads(path.read_bytes())
    value.update(
        schema_version=4,
        accounting_protocol=2,
        expected_runtime=VERSION_CONTRACTS["diagnosis-v16"],
        expected_fixed_runtime=VERSION_CONTRACTS["commerce-review-bundle-v6"],
    )
    path.write_text(json.dumps(value))
    # The generic fixture has 99 artificial accounting rows; this sidecar has
    # 96 slots. Normalize ONLY this synthetic reference before taking snapshots.
    attempts = planning.validate_cases(cases)[1]["total_tasks"]
    rows = [json.loads(line) for line in (prior / "progress.jsonl").read_bytes().splitlines()]
    for row in rows[attempts:]:
        (prior / row["receipt"]).unlink()
    rows = rows[:attempts]
    (prior / "progress.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    events = (prior / "budget-journal.jsonl").read_text().splitlines()[: 2 * attempts]
    (prior / "budget-journal.jsonl").write_text("\n".join(events) + "\n")
    summary = json.loads((prior / "summary.json").read_bytes())
    summary.update(executed_runs=attempts, provider_requests=attempts)
    summary["token_budget"].update(
        known_tokens=attempts * 4, upper_bound_charged_tokens=attempts * 4
    )
    (prior / "summary.json").write_text(json.dumps(summary))
    return directory, cases, prior


def args_for(directory, cases, prior, limit=8000000, certificate=None):
    return SimpleNamespace(
        frozen_plan=directory,
        cases=cases,
        charge_paid_run=prior,
        prior_paid_run=None,
        prior_reconciliation=certificate,
        mode="live",
        split="holdout",
        repeats=3,
        final_holdout=True,
        case_ids=None,
        max_paid_requests=None,
        max_total_tokens=limit,
        provider="DeepSeek",
        endpoint="https://api.deepseek.com",
        model="deepseek-flash",
    )


def test_complete_v4_freeze_and_admission_recomputes_ancestral_input_provenance(prepared_inputs):
    directory, cases, prior = packet(prepared_inputs)
    before = {p.name: p.read_bytes() for p in prior.iterdir()}
    plan, _ = planning.freeze(cases, directory, prior, 8000000, prior)
    assert plan["prior_input_hashes"] and plan["prior_case_files"]
    assert planning.check(directory)["validation_passed"]
    planning.guard_run(args_for(directory, cases, prior))
    assert before == {p.name: p.read_bytes() for p in prior.iterdir()}
    assert not prior.with_name(prior.name + ".budget-continuation.json").exists()


@pytest.mark.parametrize("damage", ["missing_source", "source_changed", "reused_inputs"])
def test_freshness_cannot_be_claimed_without_matching_historical_questions(prepared_inputs, damage):
    directory, cases, prior = packet(prepared_inputs)
    if damage == "missing_source":
        (prior / "cases-source.json").unlink()
        meta = json.loads((prior / "run.json").read_bytes())
        meta["cases_file_sha256"] = "f" * 64
        (prior / "run.json").write_text(json.dumps(meta))
    elif damage == "source_changed":
        (prior / "cases-source.json").write_text("{}")
    else:
        content = cases.read_bytes()
        (prior / "cases-source.json").write_bytes(content)
        meta = json.loads((prior / "run.json").read_bytes())
        meta["cases_file_sha256"] = hashlib.sha256(content).hexdigest()
        (prior / "run.json").write_text(json.dumps(meta))
    with pytest.raises(ValueError):
        planning.freeze(cases, directory, prior, 8000000, prior)
    assert not (directory / "manifest.json").exists()


def test_removing_prior_input_hashes_from_manifest_does_not_reenable_reused_cases(prepared_inputs):
    directory, cases, prior = packet(prepared_inputs)
    plan, _ = planning.freeze(cases, directory, prior, 8000000, prior)
    plan["prior_input_hashes"] = []
    (directory / "manifest.json").write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="题源|去重"):
        planning.check(directory)


async def test_missing_semantic_sidecar_is_rejected_before_credentials(
    prepared_inputs, monkeypatch
):
    directory, cases, prior = prepared_inputs
    planning.freeze(cases, directory, prior, 8000000, prior)

    def forbidden(*args, **kwargs):
        pytest.fail("credential boundary was reached")

    monkeypatch.setattr(diagnosis, "live_configuration", forbidden)
    with pytest.raises(ValueError, match="schema4"):
        await diagnosis.evaluate(args_for(directory, cases, prior))


@pytest.mark.parametrize("damage", ["protocol", "diagnosis", "fixed"])
def test_v4_binds_protocol_and_both_runtime_contracts(prepared_inputs, damage):
    directory, cases, _ = packet(prepared_inputs)
    path = directory / "semantic-contract.json"
    value = json.loads(path.read_bytes())
    if damage == "protocol":
        value["accounting_protocol"] = 1
    elif damage == "diagnosis":
        value["expected_runtime"] = VERSION_CONTRACTS["diagnosis-v15"]
    else:
        value["expected_fixed_runtime"] = VERSION_CONTRACTS["commerce-review-bundle-v5"]
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        planning.validate_cases(cases)


def test_verified_append_only_certificate_is_bound_through_freeze_check_and_guard(
    prepared_inputs, unsent_previous, tmp_path
):
    directory, cases, reference = packet(prepared_inputs)
    root = unsent_previous
    meta = json.loads((root / "run.json").read_bytes())
    meta.update(provider="DeepSeek", endpoint="https://api.deepseek.com", model="deepseek-flash")
    content = (reference / "cases-source.json").read_bytes()
    (root / "cases-source.json").write_bytes(content)
    meta["cases_file_sha256"] = hashlib.sha256(content).hexdigest()
    (root / "run.json").write_text(json.dumps(meta))
    record = json.loads((root / "case-1/agent.json").read_bytes())
    for call in record["model_invocations"]:
        call["model_version"] = "deepseek-flash"
    record["provider_trace"][0]["attempt_identity"]["model_version"] = "deepseek-flash"
    journal = [
        json.loads(line) for line in (root / "budget-journal.jsonl").read_bytes().splitlines()
    ]
    journal[-1]["attempt_identity"]["model_version"] = "deepseek-flash"
    proof = record["events"][0]["data"]["proof"]
    proof["event"] = copy.deepcopy(journal[-1])
    proof["event_sha256"] = fingerprint(journal[-1])
    (root / "budget-journal.jsonl").write_text("".join(json.dumps(row) + "\n" for row in journal))
    (root / "case-1/agent.json").write_text(json.dumps(record))
    cert = candidate(root)
    path = tmp_path / "certificate.json"
    path.write_text(json.dumps(cert))
    original = {p.name: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    plan, _ = planning.freeze(cases, directory, root, 2000000, reference, reconciliation=path)
    assert plan["history"]["known_tokens"] == 4 and plan["history"]["provider_requests"] == 1
    assert plan["history"]["reconciliation"] == cert
    assert planning.check(directory)["validation_passed"]
    args = args_for(directory, cases, root, limit=2000000, certificate=path)
    planning.guard_run(args)
    args.prior_reconciliation = None
    with pytest.raises(ValueError, match="显式"):
        planning.guard_run(args)
    changed = copy.deepcopy(cert)
    changed["known_tokens"] = 0
    path.write_text(json.dumps(changed))
    args.prior_reconciliation = path
    with pytest.raises(ValueError):
        planning.guard_run(args)
    assert original == {p.name: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="用量未知"):
        prior_spend(
            root,
            provider="DeepSeek",
            endpoint="https://api.deepseek.com",
            model="deepseek-flash",
            token_limit=25,
        )
