"""Verify the H01 repair and immutable ancestors before permitting another paid batch."""

import json
import re
from pathlib import Path

from evaluations.diagnosis import implementation_manifest
from evaluations.diagnosis_probe import audit_probe
from evaluations.diagnosis_probe_accounting import file_hash, verify_hold

ROOT = Path(__file__).resolve().parents[3]
BASE = Path(__file__).resolve().parent
RAW = ROOT / "evaluations/results"
SOURCE = RAW / "H01-probe-budget-repair-local-20261026-01"
FAILED = RAW / "model-probe-H01-amounts-20261026-01"


def main():
    live, fingerprint = implementation_manifest()
    snapshots = [
        (
            RAW / "diagnosis-v16-plan-feasibility-20261026-01/baseline-implementation.json",
            RAW / "diagnosis-v16-plan-feasibility-20261026-01/source-snapshot",
        ),
        (
            RAW / "diagnosis-v16-local-20261026-01/delivery-source.json",
            RAW / "diagnosis-v16-local-20261026-01/delivery-source-snapshot",
        ),
        (
            RAW / "pre-model-preflight-20261026-01/implementation.json",
            RAW / "pre-model-preflight-20261026-01/source-snapshot",
        ),
        (
            RAW / "model-probes-20m-preflight-20261026-03/implementation.json",
            RAW / "model-probes-20m-preflight-20261026-03/source-snapshot",
        ),
        (SOURCE / "implementation.json", SOURCE / "source-snapshot"),
    ]
    copies = []
    for manifest, folder in snapshots:
        v = json.loads(manifest.read_bytes())
        assert all(file_hash(folder / name) == checksum for name, checksum in v["files"].items())
        copies.append(
            {
                "manifest": str(manifest.relative_to(ROOT)),
                "files": len(v["files"]),
                "implementation_sha256": v["implementation_sha256"],
                "changed": 0,
            }
        )
    assert live == v["files"] and fingerprint == v["implementation_sha256"]
    protected = json.loads(
        (RAW / "diagnosis-v16-plan-feasibility-20261026-01/protected-history.json").read_bytes()
    )
    assert len(protected) == 199 and all(
        file_hash(ROOT / name) == checksum for name, checksum in protected.items()
    )
    checksums = 0
    for directory in ["diagnosis-v16-local-20261026", "pre-model-preflight-20261026"]:
        folder = ROOT / "docs/acceptance" / directory
        for line in (folder / "SHA256SUMS").read_text().splitlines():
            checksum, name = line.split(maxsplit=1)
            assert file_hash(folder / name) == checksum, name
            checksums += 1
    old = json.loads((BASE / "readiness.json").read_bytes())
    for entry in old["evidence_files"]:
        path = ROOT / entry["file"]
        if path.name == "launch_one_probe.py":
            path = BASE / "launch_one_probe-original.py"
        assert file_hash(path) == entry["sha256"], path
    hold = verify_hold(json.loads((BASE / "conservative-hold.json").read_bytes()))
    parent = audit_probe(FAILED)
    assert parent["known_tokens"] == 11019609 and parent["frozen_unknown_tokens"] == 1051576
    assert (
        parent["parent_failed"]
        and file_hash(FAILED / "receipt.json")
        == "560aca600dc50b35fd79bf5ca0df0d3f3023dd2b0ae1b36c1370764290429bbb"
    )
    network = json.loads((BASE / "H01-repair-network.json").read_bytes())
    assert network["provider_requests"] == network["external_attempts_blocked"] == 0
    pg = (BASE / "H01-repair-postgres.log").read_text()
    match = re.search(r"(\d+) passed", pg)
    assert match and "failed" not in pg.lower() and "skipped" not in pg.lower()
    assert "19 passed" in (BASE / "H01-repair-chrome.log").read_text()
    mutants = json.loads((BASE / "H01-stop-mutations.json").read_bytes())
    assert mutants["implementation_sha256"] == fingerprint
    assert mutants["behavior_breaking_mutants_killed"] == 13
    assert all(
        r["killed"] for r in mutants["results"] if r["mutation"] != "correct_rejected_proposal"
    )
    replay = json.loads((BASE / "H01-offline-replay-release.json").read_bytes())
    assert (
        replay["implementation_sha256"] == fingerprint and replay["actual_provider_requests"] == 0
    )
    assert [r["automatic_passed"] for r in replay["rows"]] == [False, True]
    assert all(r["mock_sdk_calls"] == 2 for r in replay["rows"])
    history = json.loads((BASE / "H01-legacy-regrade.json").read_bytes())
    assert history["implementation_sha256"] == fingerprint
    assert sum(r["checked"] for r in history["results"]) == 267 and all(
        not r["changed"] for r in history["results"]
    )
    value = {
        "implementation_sha256": fingerprint,
        "live_files": len(live),
        "source_copies": copies,
        "original_v15_files_checked": 199,
        "original_v15_files_changed": 0,
        "previous_evidence_checksums_verified": checksums,
        "prior_readiness_evidence_unchanged": True,
        "prior_paid_probes_audited": 4,
        "known_tokens": parent["known_tokens"],
        "frozen_unknown_tokens": hold["frozen_unknown_tokens"],
        "provider_requests": 0,
    }
    with (BASE / "H01-frozen-check.json").open("x") as f:
        json.dump(value, f, indent=2)
        f.write("\n")
    names = [
        "authorization.json",
        "conservative-hold.json",
        "H01-repair-postgres.log",
        "H01-repair-network.json",
        "H01-repair-chrome.log",
        "H01-stop-mutations.json",
        "H01-offline-replay-release.json",
        "H01-legacy-regrade.json",
        "H01-frozen-check.json",
        "launch_one_probe.py",
        "prepare_repair_readiness.py",
    ]
    ready = {
        "implementation_sha256": fingerprint,
        "source_files": len(live),
        "local_verification_passed": True,
        "source_snapshot": str((SOURCE / "source-snapshot").relative_to(ROOT)),
        "postgres_passed": int(match[1]),
        "chrome_passed": 19,
        "behavior_breaking_mutants_detected": 13,
        "legacy_grades_checked": 267,
        "network_guard_file": str((BASE / "H01-repair-network.json").relative_to(ROOT)),
        "evidence_files": [
            {"file": str((BASE / name).relative_to(ROOT)), "sha256": file_hash(BASE / name)}
            for name in names
        ],
        "scope": "H01 two-call repair readiness; not model quality or G6 acceptance",
    }
    with (BASE / "readiness-H01-repair.json").open("x") as f:
        json.dump(ready, f, indent=2)
        f.write("\n")
    repair = {
        "failed_receipt_sha256": parent["parent_receipt_sha256"],
        "implementation_sha256": fingerprint,
        "local_verification_passed": True,
        "root_cause_document": "docs/55-H01-probe-budget-root-cause-and-repair-20261026.md",
        "readiness_file": str((BASE / "readiness-H01-repair.json").relative_to(ROOT)),
        "readiness_sha256": file_hash(BASE / "readiness-H01-repair.json"),
        "http_cap_unchanged": 2,
        "amount_witness_and_grading_unchanged": True,
    }
    with (BASE / "H01-repair-evidence.json").open("x") as f:
        json.dump(repair, f, indent=2)
        f.write("\n")
    print(json.dumps({**value, "postgres_passed": ready["postgres_passed"], "chrome_passed": 19}))


if __name__ == "__main__":
    main()
