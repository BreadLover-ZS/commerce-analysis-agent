"""Read-only historical byte, charge and UNKNOWN checks; no provider requests."""
import hashlib
import json
from pathlib import Path

from evaluations.diagnosis import implementation_manifest
from evaluations.diagnosis_probe import load_parent
from evaluations.diagnosis_reconciliation import inspect

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent


def main():
    baseline = json.loads((HERE / "baseline.json").read_bytes())
    protected = baseline["protected_files"]
    changed = [p for p, h in protected.items()
               if not (ROOT / p).is_file()
               or hashlib.sha256((ROOT / p).read_bytes()).hexdigest() != h]
    assert not changed, changed
    leaf = ROOT / "evaluations/results/model-probe-H07-prior-boundary-20261027-01"
    accounting = load_parent(leaf)
    assert accounting["known_tokens"] == baseline["known_tokens"] == 11049569
    assert accounting["frozen_unknown_tokens"] == baseline["frozen_unknown_tokens"] == 1051576
    assert accounting["confirmed_http_requests"] == 1587
    claim = leaf.with_name(leaf.name + ".budget-continuation.json")
    assert not claim.exists(), "local preparation must not consume the real leaf"
    strict = inspect(ROOT / "evaluations/results/diagnosis-v15-h01-h20-live-20261026-01")
    assert strict["status"] == "RECONCILIATION_BLOCKED" and strict["certificate"] is None
    snapshots = []
    for name in ["H01-probe-budget-repair-local-20261026-01",
                 "boundary-formal-repair-local-20261027-02"]:
        directory = ROOT / "evaluations/results" / name
        manifest = json.loads((directory / "implementation.json").read_bytes())
        for p, h in manifest["files"].items():
            assert hashlib.sha256((directory / "source-snapshot" / p).read_bytes()).hexdigest() == h
        snapshots.append({"directory": str(directory.relative_to(ROOT)),
                          "files_verified": len(manifest["files"]),
                          "implementation_sha256": manifest["implementation_sha256"]})
    value = {"implementation_sha256": implementation_manifest()[1],
             "protected_files_checked": len(protected), "changed": changed,
             "accounting": accounting, "strict_reconciliation": strict,
             "real_leaf_claim_created": False, "provider_requests": 0,
             "remaining_tokens": 20000000 - 11049569 - 1051576,
             "source_snapshots": snapshots}
    (HERE / "history-protection.json").write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"protected_files_checked": len(protected), "changed": 0,
                      "known_tokens": accounting["known_tokens"],
                      "strict_reconciliation": strict["status"], "provider_requests": 0}))


if __name__ == "__main__":
    main()
