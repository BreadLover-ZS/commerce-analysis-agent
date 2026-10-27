"""Freeze fresh formal slots against a verified charge anchor; no execution or key reads."""

import argparse
import json
from pathlib import Path

from evaluations.diagnosis import implementation_manifest, write
from evaluations.diagnosis_cases import load_cases
from evaluations.diagnosis_packet_checks import check_packet, require
from evaluations.diagnosis_plan import historical_question_sources, input_hash
from evaluations.diagnosis_probe import (
    MAX_PROBE_ANCESTRY,
    load_parent,
    require_prior_review,
    validate_probe_http_budget,
)
from evaluations.diagnosis_probe_accounting import LIMIT, file_hash

MODEL = {
    "provider": "DeepSeek",
    "endpoint": "https://api.deepseek.com",
    "model": "deepseek-flash",
    "temperature": 0,
    "max_output_tokens": 3000,
    "sdk_max_retries": 0,
    "accounting_protocol": 2,
}
PACKET_FILES = {
    "cases.json",
    "semantic-contract.json",
    "answer-obligations.json",
    "execution-plan.json",
    "provenance.json",
}


def read(path):
    return json.loads(Path(path).read_bytes())


def history(parent):
    """Include the complete prior corpus and every paid one-Task source; never labels alone."""
    known, files = historical_question_sources(parent["legacy_root"])
    path = Path(parent["directory"]).resolve()
    seen = set()
    while path.is_dir() and (path / "probe.json").exists():
        require(
            path not in seen and len(seen) < MAX_PROBE_ANCESTRY, "formal_history_cycle_or_depth"
        )
        seen.add(path)
        meta = read(path / "probe.json")
        source = Path(meta["cases_file"])
        require(file_hash(source) == meta["cases_sha256"], "formal_ancestral_corpus_changed")
        known.update(input_hash(c) for c in load_cases(source)[0])
        files[str(source.resolve())] = meta["cases_sha256"]
        path = Path(meta["parent"]).resolve()
    return {"known_input_hashes": sorted(known), "case_files": dict(sorted(files.items()))}


def local_verification(evidence, fingerprint):
    value = read(evidence)
    require(
        value.get("implementation_sha256") == fingerprint
        and value.get("local_verification_passed") is True,
        "formal_local_verification_missing_or_stale",
    )
    refs = value.get("evidence_files", [])
    require(bool(refs), "formal_verification_requires_bound_evidence")
    for row in refs:
        require(file_hash(row["file"]) == row["sha256"], "formal_local_evidence_changed")
    require(
        value.get("provider_requests") == 0, "formal_local_verification_must_be_provider_isolated"
    )
    return {"file": str(Path(evidence).resolve()), "sha256": file_hash(evidence)}


def schedule(directory, cases):
    plan = read(Path(directory) / "execution-plan.json")
    expected = []
    for c in cases:
        for repeat in range(1, 4):
            for group in (
                ["fixed", "agent"] if c.expected.baseline_capability == "common" else ["additional"]
            ):
                expected.append((f"{c.case_id}-{repeat}/{group}", c.case_id, repeat, group))
    slots = plan["slots"]
    require(
        len(slots) == 96
        and [(s["slot_id"], s["case_id"], s["repeat"], s["group"]) for s in slots] == expected,
        "formal_slots_order_or_pairing_changed",
    )
    require(
        plan.get("require_sequential_launch") is True
        and plan.get("no_automatic_resume_or_retry") is True
        and plan.get("no_sdk_retries") is True
        and plan.get("no_fallback_extra_calls") is True,
        "formal_stop_policy_not_frozen",
    )
    require(
        set(plan["stop_reasons"])
        == {
            "protocol_error",
            "proposal_or_tool_rejected",
            "call_error",
            "automatic_grade_failed",
            "manual_review_failed",
            "usage_unknown",
            "budget_insufficient",
        },
        "formal_stop_class_missing",
    )
    for s in slots:
        require(
            s["status"] == "UNEXECUTED"
            and s["model_decision_reserve"] == 0
            and type(s["model_decision_reserve"]) is int
            and s["stop_after_model_failure"] is True
            and s["manual_review_required_before_next_slot"] is True,
            "formal_slot_policy_not_frozen",
        )
        c = next(c for c in cases if c.case_id == s["case_id"])
        validate_probe_http_budget(c, s["group"], s["max_http"], answer_boundary_version=2)
    return slots


def freeze(directory, parent_path, verification, repair_evidence=None):
    directory = Path(directory).resolve()
    require(not (directory / "manifest.json").exists(), "formal_manifest_already_exists")
    files, fingerprint = implementation_manifest()
    parent = load_parent(parent_path)
    require(
        not Path(parent["directory"])
        .with_name(Path(parent["directory"]).name + ".budget-continuation.json")
        .exists(),
        "formal_parent_already_claimed",
    )
    require_prior_review(parent, fingerprint, repair_evidence)
    require(
        parent.get("ancestry_depth", 0) + 96 < MAX_PROBE_ANCESTRY,
        "formal_round_cannot_fit_bounded_ancestry",
    )
    provenance = history(parent)
    cases, sem, contract = check_packet(directory / "cases.json", provenance["known_input_hashes"])
    slots = schedule(directory, cases)
    verified = local_verification(verification, fingerprint)
    model = dict(MODEL)
    manifest = {
        "schema_version": 1,
        "formal_entry_version": 1,
        "status": "FROZEN_FORMAL_SLOTS_AUTHORIZATION_REQUIRED",
        "implementation_sha256": fingerprint,
        "source_manifest": files,
        "packet_files_sha256": {name: file_hash(directory / name) for name in sorted(PACKET_FILES)},
        "parent_anchor": str(Path(parent_path).resolve()),
        "parent_accounting": parent,
        "parent_review_sha256": file_hash(Path(parent["directory"]) / "review.json"),
        "history_provenance": provenance,
        "packet_contract": contract,
        "slots": slots,
        "model": model,
        "answer_boundary_version": 2,
        "answer_contract_version": "answer-contract-v5",
        "cumulative_limit": LIMIT,
        "local_verification": verified,
        "repair_evidence": (
            {"file": str(Path(repair_evidence).resolve()), "sha256": file_hash(repair_evidence)}
            if repair_evidence
            else None
        ),
        "paid_authorization_granted": False,
        "release_eligible": False,
    }
    write(directory / "manifest.json", manifest)
    return check(directory)


def check(directory):
    directory = Path(directory).resolve()
    m = read(directory / "manifest.json")
    require(
        m.get("schema_version") == 1
        and m.get("formal_entry_version") == 1
        and m.get("status") == "FROZEN_FORMAL_SLOTS_AUTHORIZATION_REQUIRED",
        "formal_plan_schema_invalid",
    )
    require(
        m.get("paid_authorization_granted") is False and m.get("release_eligible") is False,
        "formal_plan_must_not_grant_its_own_authorization",
    )
    files, fingerprint = implementation_manifest()
    require(
        m["source_manifest"] == files and m["implementation_sha256"] == fingerprint,
        "formal_source_changed_refreeze_required",
    )
    require(set(m["packet_files_sha256"]) == PACKET_FILES, "formal_frozen_file_inventory_changed")
    for name, checksum in m["packet_files_sha256"].items():
        require(file_hash(directory / name) == checksum, "formal_packet_changed")
    require(
        m["model"] == MODEL
        and m["cumulative_limit"] == LIMIT
        and m["answer_boundary_version"] == 2
        and m["answer_contract_version"] == "answer-contract-v5",
        "formal_model_limit_or_semantics_changed",
    )
    parent = load_parent(m["parent_anchor"])
    require(parent == m["parent_accounting"], "formal_parent_accounting_changed")
    require(
        file_hash(Path(parent["directory"]) / "review.json") == m["parent_review_sha256"],
        "formal_anchor_review_changed",
    )
    repair = m["repair_evidence"]
    if repair:
        require(file_hash(repair["file"]) == repair["sha256"], "formal_repair_evidence_changed")
    require_prior_review(parent, fingerprint, repair["file"] if repair else None)
    provenance = history(parent)
    require(provenance == m["history_provenance"], "formal_historical_corpus_changed")
    cases, sem, contract = check_packet(directory / "cases.json", provenance["known_input_hashes"])
    require(
        contract == m["packet_contract"] and schedule(directory, cases) == m["slots"],
        "formal_oracle_or_slots_changed",
    )
    verification = m["local_verification"]
    require(
        file_hash(verification["file"]) == verification["sha256"],
        "formal_verification_record_changed",
    )
    local_verification(verification["file"], fingerprint)
    require(
        parent.get("ancestry_depth", 0) + 96 < MAX_PROBE_ANCESTRY,
        "formal_round_cannot_fit_bounded_ancestry",
    )
    return m


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--directory", type=Path, required=True)
    f.add_argument("--parent", type=Path, required=True)
    f.add_argument("--local-verification", type=Path, required=True)
    f.add_argument("--repair-evidence", type=Path)
    c = sub.add_parser("check")
    c.add_argument("--directory", type=Path, required=True)
    a = p.parse_args()
    m = (
        freeze(a.directory, a.parent, a.local_verification, a.repair_evidence)
        if a.command == "freeze"
        else check(a.directory)
    )
    print(
        json.dumps(
            {
                "implementation_sha256": m["implementation_sha256"],
                "slots": len(m["slots"]),
                "paid_authorization_granted": False,
                "provider_requests": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
