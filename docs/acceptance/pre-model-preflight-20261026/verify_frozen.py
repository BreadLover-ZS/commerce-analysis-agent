"""Compare live implementation, frozen copies and immutable historical bytes."""

import argparse
import hashlib
import json
from pathlib import Path

from evaluations.diagnosis import implementation_manifest

ROOT = Path(__file__).resolve().parents[3]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(output):
    raw = ROOT / "evaluations/results"
    live, fingerprint = implementation_manifest()
    snapshots = [
        (
            raw / "diagnosis-v16-plan-feasibility-20261026-01/baseline-implementation.json",
            raw / "diagnosis-v16-plan-feasibility-20261026-01/source-snapshot",
        ),
        (
            raw / "diagnosis-v16-local-20261026-01/delivery-source.json",
            raw / "diagnosis-v16-local-20261026-01/delivery-source-snapshot",
        ),
        (
            raw / "pre-model-preflight-20261026-01/implementation.json",
            raw / "pre-model-preflight-20261026-01/source-snapshot",
        ),
    ]
    checked = []
    for manifest, snapshot in snapshots:
        value = json.loads(manifest.read_bytes())
        for name, checksum in value["files"].items():
            assert sha(snapshot / name) == checksum, (snapshot, name)
        checked.append(
            {
                "manifest": str(manifest.relative_to(ROOT)),
                "files": len(value["files"]),
                "implementation_sha256": value["implementation_sha256"],
                "changed": 0,
            }
        )
    assert live == value["files"] and fingerprint == value["implementation_sha256"]
    history = json.loads(
        (raw / "diagnosis-v16-plan-feasibility-20261026-01/protected-history.json").read_bytes()
    )
    assert all(sha(ROOT / name) == expected for name, expected in history.items())
    old = ROOT / "docs/acceptance/diagnosis-v16-local-20261026"
    old_count = 0
    for line in (old / "SHA256SUMS").read_text().splitlines():
        checksum, name = line.split(maxsplit=1)
        assert sha(old / name) == checksum, name
        old_count += 1
    value = {
        "implementation_sha256": fingerprint,
        "live_files": len(live),
        "source_copies": checked,
        "original_v15_files_checked": len(history),
        "original_v15_files_changed": 0,
        "prior_v16_evidence_checksums_verified": old_count,
        "credentials_included": False,
        "provider_requests": 0,
    }
    with output.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    print(json.dumps(value))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args().output)
