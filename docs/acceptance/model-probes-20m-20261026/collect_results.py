"""Read-only final probe-chain audit and evidence inventory; never executes a Task."""

import json
from pathlib import Path

from evaluations.diagnosis import implementation_manifest, write
from evaluations.diagnosis_probe import audit_probe
from evaluations.diagnosis_probe_accounting import file_hash, verify_hold
from evaluations.diagnosis_reconciliation import inspect
from evaluations.diagnosis_runtime import ROOT

BASE = Path(__file__).resolve().parent
RUNS = [
    "model-probe-H03-fixed-20261026-01",
    "model-probe-H13-agent-20261026-01",
    "model-probe-H06-boundary-20261026-01",
    "model-probe-H01-amounts-20261026-01",
    "model-probe-H01-amounts-20261026-02",
    "model-probe-H09-category-20261026-01",
    "model-probe-H10-profit-boundary-20261026-01",
    "model-probe-H07-prior-boundary-20261027-01",
]


def main():
    hold = verify_hold(json.loads((BASE / "conservative-hold.json").read_bytes()))
    rows = []
    for name in RUNS:
        root = ROOT / "evaluations/results" / name
        meta = json.loads((root / "probe.json").read_bytes())
        summary = json.loads((root / "summary.json").read_bytes())
        record = json.loads((root / "receipt.json").read_bytes())
        review = json.loads((root / "review.json").read_bytes())
        assert (
            review["receipt_sha256"]
            == summary["receipt_sha256"]
            == file_hash(root / "receipt.json")
        )
        assert record["task"]["metrics"]["usage_complete"] is True
        assert not any(x["status"] == "UNKNOWN" for x in record["model_invocations"])
        rows.append(
            {
                "directory": str(root.relative_to(ROOT)),
                "case_id": meta["case_id"],
                "group": meta["group"],
                "implementation_sha256": meta["implementation_sha256"],
                "automatic_passed": record["grading"]["automatic_passed"],
                "author_ai_review": review["verdict"],
                "failure_reason": summary["failure_reason"],
                "http_cap": meta["max_http"],
                "new_http_requests": summary["new_http_requests"],
                "new_known_tokens": summary["new_known_tokens"],
                "task_status": record["task"]["status"],
                "stop_reason": record["task"]["report"].get("stop_reason"),
                "answer_status": record["task"]["report"]["answer_completion"]["status"]
                if "answer_completion" in record["task"]["report"]
                else None,
                "new_unknown": 0,
                "receipt_sha256": summary["receipt_sha256"],
                "core_files_sha256": {
                    p: file_hash(root / p)
                    for p in [
                        "probe.json",
                        "receipt.json",
                        "review.json",
                        "summary.json",
                        "business-input.json",
                        "bundle.json",
                        "budget-journal.jsonl",
                    ]
                },
            }
        )
    leaf = ROOT / "evaluations/results" / RUNS[-1]
    audited = audit_probe(leaf)
    assert audited["known_tokens"] == hold["known_tokens"] + sum(
        r["new_known_tokens"] for r in rows
    )
    assert audited["known_tokens"] == 11049569 and sum(r["new_http_requests"] for r in rows) == 11
    assert sum(r["new_known_tokens"] for r in rows) == 94907
    strict = inspect(Path(hold["directory"]))
    write(BASE / "final-historical-reconciliation.json", strict)
    assert strict["status"] == "RECONCILIATION_BLOCKED"
    failed = [r for r in rows if not r["automatic_passed"] or r["author_ai_review"] != "PASS"]
    assert len(failed) == 1 and failed[0]["directory"].endswith("H01-amounts-20261026-01")
    assert implementation_manifest()[1] == rows[-1]["implementation_sha256"]
    value = {
        "scope": "USED_INPUT_SMALL_PROBES_NOT_G6_FORMAL_ACCEPTANCE",
        "started_client_date": "2026-10-26",
        "completed_client_date": "2026-10-27",
        "task_attempts": len(rows),
        "distinct_case_group_pairs": len({(r["case_id"], r["group"]) for r in rows}),
        "attempts_pass_both_checks": 7,
        "attempts_fail_preserved": 1,
        "real_model_task_attempts": sum(r["new_http_requests"] > 0 for r in rows),
        "zero_model_task_attempts": sum(r["new_http_requests"] == 0 for r in rows),
        "new_http_requests": 11,
        "new_known_tokens": 94907,
        "cumulative_confirmed_http_requests": audited["confirmed_http_requests"],
        "cumulative_known_tokens": audited["known_tokens"],
        "legacy_unknown_hold_tokens": hold["frozen_unknown_tokens"],
        "effective_occupied_tokens": audited["known_tokens"] + hold["frozen_unknown_tokens"],
        "remaining_tokens": 20000000 - audited["known_tokens"] - hold["frozen_unknown_tokens"],
        "cumulative_token_limit": 20000000,
        "new_unknown": 0,
        "historical_unknown_count": 1,
        "legacy_strict_reconciliation_passed": False,
        "new_chain_accounting_verified": True,
        "no_actual_model_calls_in_this_audit": True,
        "final_implementation_sha256": implementation_manifest()[1],
        "final_source_files": len(implementation_manifest()[0]),
        "rows": rows,
        "future_model_testing": "STOPPED_AFTER_PLANNED_SMALL_BATCH; one Task admission and review required for any further call",
        "G6": "NOT_RELEASED",
        "G7": "EXCLUDED",
        "publication": "UNCOMMITTED_UNPUSHED",
    }
    write(BASE / "results.json", value)
    print(json.dumps({k: v for k, v in value.items() if k != "rows"}))


if __name__ == "__main__":
    main()
