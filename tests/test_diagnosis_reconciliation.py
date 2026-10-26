"""Only directly bound durable denials can explain an old UNKNOWN as known zero."""

import copy
import json

import pytest

from app.core.model_boundary import fingerprint
from evaluations.diagnosis_budget import claim_prior_budget, prior_spend
from evaluations.diagnosis_reconciliation import candidate, inspect, verify_certificate
from tests.test_diagnosis_budget import previous as previous


@pytest.fixture
def unsent_previous(previous):
    meta = json.loads((previous / "run.json").read_bytes())
    meta.update(accounting_protocol=2, implementation_sha256="a" * 64)
    record = json.loads((previous / "case-1/agent.json").read_bytes())
    identity = {
        "call_id": "unsent",
        "task_id": "task",
        "step_id": "step-2",
        "phase": "propose",
        "attempt": 1,
        "lease_token": "old-lease",
        "prompt_hash": "b" * 64,
        "schema_hash": "c" * 64,
        "model_version": "chosen",
    }
    denial = {
        "event": "denied_before_send",
        "spent_tokens": 4,
        "reserved_tokens": 0,
        "reason": "token_budget_capacity",
        "next_reservation": 22,
        "protocol": 2,
        "run_id": "prior",
        "attempt_identity": identity,
        "attempt_admissions": 0,
        "sequence": 1,
    }
    proof = {
        "event": denial,
        "event_sha256": fingerprint(denial),
        "journal": str(previous / "budget-journal.jsonl"),
    }
    record["task"].update(parameters={"accounting_protocol": 2})
    record["task"]["metrics"].update(usage_complete=False, model_calls=2)
    record["measurement"]["metrics"] = copy.deepcopy(record["task"]["metrics"])
    record["model_invocations"] = [
        {
            **identity,
            "call_id": "known",
            "status": "SUCCEEDED",
            "usage_complete": True,
            "usage": {"input_tokens": 3, "output_tokens": 1},
        },
        {**identity, "status": "UNKNOWN", "usage_complete": False, "usage": None},
    ]
    record["provider_trace"][0].update(
        protocol=2, run_id="prior", attempt_identity={**identity, "call_id": "known"}
    )
    record["events"] = [
        {"kind": "model_send_denied", "data": {"call_id": "unsent", "proof": proof}}
    ]
    (previous / "run.json").write_text(json.dumps(meta))
    (previous / "case-1/agent.json").write_text(json.dumps(record))
    (previous / "progress.jsonl").write_text(json.dumps(record["measurement"]) + "\n")
    with (previous / "budget-journal.jsonl").open("a") as stream:
        stream.write(json.dumps(denial) + "\n")
    return previous


def audit(root, certificate=None):
    return prior_spend(
        root,
        provider="test",
        endpoint="https://provider.invalid",
        model="chosen",
        token_limit=25,
        reconciliation=certificate,
    )


def test_explicit_certificate_is_read_only_and_preserves_complete_costs(unsent_previous):
    root = unsent_previous
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="用量未知"):
        audit(root)
    value = inspect(root)
    assert value["status"] == "RECONCILIATION_VERIFIED", value
    certificate = value["certificate"]
    result = audit(root, certificate)
    assert result["known_tokens"] == 4 and result["provider_requests"] == 1
    assert result["prior_run"]["reconciliation"] == certificate
    assert before == {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert (
        json.loads((root / "case-1/agent.json").read_bytes())["model_invocations"][-1]["status"]
        == "UNKNOWN"
    )
    claim_prior_budget(result, root.parent / "next", "next")
    with pytest.raises(FileExistsError):
        claim_prior_budget(result, root.parent / "another", "another")


@pytest.mark.parametrize(
    "damage",
    [
        "legacy_no_identity",
        "two_unknown",
        "two_denials",
        "foreign_identity",
        "missing_trace",
        "other_unknown",
        "admitted",
        "run_id",
        "proof",
        "journal_missing",
    ],
)
def test_ambiguous_or_tampered_evidence_stays_blocked(unsent_previous, damage):
    root = unsent_previous
    record = json.loads((root / "case-1/agent.json").read_bytes())
    journal = [
        json.loads(line) for line in (root / "budget-journal.jsonl").read_bytes().splitlines()
    ]
    if damage == "legacy_no_identity":
        journal[-1].pop("attempt_identity")
    elif damage == "two_unknown":
        record["model_invocations"].append(copy.deepcopy(record["model_invocations"][-1]))
    elif damage == "two_denials":
        journal.append(copy.deepcopy(journal[-1]))
    elif damage == "foreign_identity":
        journal[-1]["attempt_identity"]["task_id"] = "other"
    elif damage == "missing_trace":
        record["provider_trace"] = []
    elif damage == "other_unknown":
        record["model_invocations"][0]["usage_complete"] = False
    elif damage == "admitted":
        journal[-1]["attempt_admissions"] = 1
    elif damage == "run_id":
        journal[-1]["run_id"] = "other"
    elif damage == "proof":
        record["events"] = []
    else:
        (root / "budget-journal.jsonl").unlink()
    (root / "case-1/agent.json").write_text(json.dumps(record))
    if damage != "journal_missing":
        (root / "budget-journal.jsonl").write_text("\n".join(map(json.dumps, journal)) + "\n")
    assert inspect(root)["status"] == "RECONCILIATION_BLOCKED"


@pytest.mark.parametrize(
    "key", ["known_tokens", "identity", "files_sha256", "implementation_sha256", "verifier_sha256"]
)
def test_certificate_cannot_author_its_own_zero_cost_truth(unsent_previous, key):
    certificate = candidate(unsent_previous)
    certificate[key] = "forged"
    with pytest.raises(ValueError, match="changed"):
        verify_certificate(unsent_previous, certificate)
    with pytest.raises(ValueError):
        audit(unsent_previous, certificate)


def test_verified_explanation_recurses_without_losing_charges_or_inheriting_quality(
    unsent_previous,
):
    parent = audit(unsent_previous, candidate(unsent_previous))
    future = unsent_previous.parent / "future"
    future.mkdir()
    meta = {
        "run_id": "future",
        "planned": {"mode": "live"},
        "provider": "test",
        "endpoint": "https://provider.invalid",
        "model": "chosen",
        "max_total_tokens": 25,
        "budget_carry_mode": "accounting_only",
        "prior_paid_budget": {
            "max_total_tokens": 25,
            "known_tokens": 4,
            "provider_requests": 1,
            "prior_run": parent["prior_run"],
        },
    }
    row = {"receipt": "new.json", "provider_requests": 0}
    record = {
        "measurement": row,
        "task": {"metrics": {"usage_complete": True, "input_tokens": 0, "output_tokens": 0}},
        "provider_trace": [],
    }
    summary = {
        "run_id": "future",
        "executed_runs": 1,
        "provider_requests": 1,
        "stopped": "no_new_send",
        "token_budget": {
            "max_total_tokens": 25,
            "known_tokens": 4,
            "unresolved_reserved_tokens": 0,
            "upper_bound_charged_tokens": 4,
        },
    }
    for name, value in {"run.json": meta, "summary.json": summary, "new.json": record}.items():
        (future / name).write_text(json.dumps(value))
    (future / "progress.jsonl").write_text(json.dumps(row) + "\n")
    (future / "budget-journal.jsonl").write_text(
        json.dumps(
            {
                "event": "carried_prior_usage",
                "spent_tokens": 4,
                "reserved_tokens": 0,
                "provider_requests": 1,
                "prior_run_id": "prior",
            }
        )
        + "\n"
    )
    result = audit(future)
    assert result["known_tokens"] == 4 and result["provider_requests"] == 1
    assert result["_rows"] == [row] and result["_accounting_prefix_tokens"] == 4
