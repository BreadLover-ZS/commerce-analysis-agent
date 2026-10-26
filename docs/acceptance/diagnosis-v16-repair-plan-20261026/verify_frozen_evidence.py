"""Read-only plan evidence checks; no application import, credentials, or HTTP."""

import argparse
import ast
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
BASE = ROOT / "evaluations/results/diagnosis-v16-plan-feasibility-20261026-01"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(output):
    baseline = json.loads((BASE / "baseline-implementation.json").read_bytes())
    history = json.loads((BASE / "protected-history.json").read_bytes())
    snapshot = ROOT / baseline["source_snapshot"]
    # Evaluate only the pure manifest helpers, never evaluator imports/entrypoint.
    namespace = {"hashlib": hashlib, "json": json, "ROOT": ROOT}
    for name, source in (
        ("sha", ROOT / "evaluations/diagnosis_grading.py"),
        ("implementation_manifest", ROOT / "evaluations/diagnosis.py"),
    ):
        node = next(
            item
            for item in ast.parse(source.read_text()).body
            if isinstance(item, ast.FunctionDef) and item.name == name
        )
        module = ast.Module(body=[node], type_ignores=[])
        exec(compile(module, str(source), "exec"), namespace)
    current, fingerprint = namespace["implementation_manifest"]()
    source_changed = [
        name
        for name, expected in baseline["files"].items()
        if not (ROOT / name).is_file() or digest(ROOT / name) != expected
    ]
    snapshot_changed = [
        name
        for name, expected in baseline["files"].items()
        if not (snapshot / name).is_file() or digest(snapshot / name) != expected
    ]
    history_changed = [
        name
        for name, expected in history.items()
        if not (ROOT / name).is_file() or digest(ROOT / name) != expected
    ]
    sdk = json.loads((HERE / "sdk-boundary-feasibility.json").read_bytes())
    report = json.loads((HERE / "report-capacity-feasibility.json").read_bytes())
    checks = {
        "implementation_fingerprint_and_file_set_unchanged": (
            current == baseline["files"]
            and fingerprint == baseline["implementation_sha256"]
            and not source_changed
        ),
        "snapshot_matches_235_frozen_files": (
            len(baseline["files"]) == 235
            and not snapshot_changed
            and {str(p.relative_to(snapshot)) for p in snapshot.rglob("*") if p.is_file()}
            == set(baseline["files"])
        ),
        "protected_199_historical_files_unchanged": len(history) == 199 and not history_changed,
        "snapshot_excludes_env_credentials": (
            baseline["credentials_included"] is False
            and not any(Path(p).name == ".env" for p in baseline["files"])
        ),
        "sdk_10_plan_checks_passed_without_network": (
            len(sdk["checks"]) == 10
            and all(sdk["checks"].values())
            and sdk["provider_requests"] == sdk["network_attempts"] == 0
            and sdk["credentials_read"] is False
        ),
        "report_5_plan_checks_passed_without_tasks": (
            len(report["checks"]) == 5
            and all(report["checks"].values())
            and report["provider_requests"] == report["task_executions"] == 0
        ),
    }
    value = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "status": "PLAN_EVIDENCE_ONLY_NOT_IMPLEMENTED_NOT_RELEASED",
        "checks": checks,
        "implementation_sha256": fingerprint,
        "implementation_files": len(current),
        "protected_history_files": len(history),
        "source_changed": source_changed,
        "snapshot_changed": snapshot_changed,
        "history_changed": history_changed,
        "plan_feasibility_checks_passed": 15,
        "planned_regression_categories": 35,
        "regression_categories_executed": 0,
        "new_provider_requests": 0,
        "new_task_executions": 0,
        "historical_reconciliation": "NOT_IMPLEMENTED_CANONICAL_AUDIT_REMAINS_BLOCKED",
        "business_fix_implemented": False,
        "g6_release_eligible": False,
        "g7_excluded": True,
    }
    with output.open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    assert all(checks.values()), value
    print(
        json.dumps(
            {
                "checks_passed": len(checks),
                "source_files": len(current),
                "history_files": len(history),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    verify(parser.parse_args().output)
