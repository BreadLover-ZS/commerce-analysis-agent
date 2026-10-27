"""Bind actual local evidence and held accounting before the first credential read."""

import importlib.metadata
import json
from pathlib import Path

from evaluations.diagnosis import implementation_manifest
from evaluations.diagnosis_probe_accounting import file_hash, verify_hold

ROOT = Path(__file__).resolve().parents[3]
BASE = Path(__file__).resolve().parent


def main():
    source = json.loads(
        (
            ROOT / "evaluations/results/model-probes-20m-preflight-20261026-03/implementation.json"
        ).read_bytes()
    )
    files, fingerprint = implementation_manifest()
    assert source["files"] == files and source["implementation_sha256"] == fingerprint
    for name, checksum in files.items():
        assert (
            file_hash(
                ROOT
                / "evaluations/results/model-probes-20m-preflight-20261026-03/source-snapshot"
                / name
            )
            == checksum
        ), name
    assert "1316 passed" in (BASE / "preflight-postgres-release.log").read_text()
    assert "19 passed" in (BASE / "preflight-chrome-release.log").read_text()
    network = json.loads((BASE / "preflight-network-release.json").read_bytes())
    assert network["provider_requests"] == network["external_attempts_blocked"] == 0
    mutants = json.loads((BASE / "stop-mutations.json").read_bytes())
    assert mutants["implementation_sha256"] == fingerprint
    assert all(
        r["killed"] for r in mutants["results"] if r["mutation"] != "correct_rejected_proposal"
    )
    assert (
        next(r for r in mutants["results"] if r["mutation"] == "correct_rejected_proposal")[
            "mutant_exit_code"
        ]
        == 0
    )
    assert sum(r["killed"] for r in mutants["results"]) == 9
    tamper = json.loads((BASE / "hold-tampering.json").read_bytes())
    assert len(tamper["results"]) == 12 and all(r["rejected"] for r in tamper["results"])
    history = json.loads((BASE / "legacy-regrade-preflight.json").read_bytes())
    assert sum(r["checked"] for r in history["results"]) == 267 and all(
        not r["changed"] for r in history["results"]
    )
    hold = verify_hold(json.loads((BASE / "conservative-hold.json").read_bytes()))
    names = [
        "authorization.json",
        "conservative-hold.json",
        "preflight-postgres-release.log",
        "preflight-network-release.json",
        "preflight-chrome-release.log",
        "stop-mutations.json",
        "hold-tampering.json",
        "legacy-regrade-preflight.json",
        "launch_one_probe.py",
    ]
    value = {
        "implementation_sha256": fingerprint,
        "source_files": len(files),
        "local_verification_passed": True,
        "source_snapshot": "evaluations/results/model-probes-20m-preflight-20261026-03/source-snapshot",
        "postgres_passed": 1316,
        "chrome_passed": 19,
        "behavior_breaking_mutants_detected": 9,
        "redundant_guard_control_survived": 1,
        "hold_tampering_rejected": 12,
        "legacy_grades_checked": 267,
        "known_tokens_before_new_calls": hold["known_tokens"],
        "frozen_unknown_tokens": hold["frozen_unknown_tokens"],
        "remaining_tokens_before_new_calls": 20000000
        - hold["known_tokens"]
        - hold["frozen_unknown_tokens"],
        "network_guard_file": str((BASE / "preflight-network-release.json").relative_to(ROOT)),
        "evidence_files": [
            {"file": str((BASE / name).relative_to(ROOT)), "sha256": file_hash(BASE / name)}
            for name in names
        ],
        "dependency_versions": {
            name: importlib.metadata.version(name)
            for name in [
                "openai",
                "langchain-openai",
                "langchain-core",
                "langgraph",
                "pydantic",
                "httpx",
            ]
        },
        "new_provider_requests_at_preparation": 0,
        "scope": "Local readiness only, not model quality or G6 acceptance",
    }
    with (BASE / "readiness.json").open("x") as f:
        json.dump(value, f, indent=2)
        f.write("\n")
    print(
        json.dumps(
            {k: v for k, v in value.items() if k not in ["evidence_files", "dependency_versions"]}
        )
    )


if __name__ == "__main__":
    main()
