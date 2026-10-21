"""Regrade retained executions without new Tasks, paid requests, or overwritten FAILs."""

import hashlib
import json
from pathlib import Path

from evaluations.diagnosis import implementation_manifest, summarize
from evaluations.diagnosis_budget import prior_spend
from evaluations.diagnosis_cases import DEFAULT_CASES, load_cases
from evaluations.diagnosis_grading import grade, sha


def audit_run(directory):
    root = Path(directory).resolve()
    meta = json.loads((root / "run.json").read_bytes())
    original = json.loads((root / "summary.json").read_bytes())
    cases_path = root / "cases-source.json"
    if not cases_path.exists():
        cases_path = DEFAULT_CASES  # Historical runs, still hash-checked below.
    cases, selected_hash = load_cases(
        cases_path, split=meta["planned"]["split"], case_ids=meta["planned"]["case_ids"]
    )
    if (
        hashlib.sha256(cases_path.read_bytes()).hexdigest() != meta["cases_file_sha256"]
        or selected_hash != meta["selected_cases_sha256"]
        or sha([c.expected.model_dump(mode="json") for c in cases]) != meta["expected_sha256"]
    ):
        raise ValueError("审计所用问题集或预期与模型运行时不一致")
    cases = {case.case_id: case for case in cases}
    rows = [json.loads(line) for line in (root / "progress.jsonl").read_bytes().splitlines()]
    if len(rows) != original["executed_runs"] or len({r["receipt"] for r in rows}) != len(rows):
        raise ValueError("审计进度缺失或重复")
    graded, checked = [], []
    for row in rows:
        path = (root / row["receipt"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("审计回执路径越界")
        raw = path.read_bytes()
        record = json.loads(raw)
        if record["measurement"] != row:
            raise ValueError("审计回执与原进度不一致")
        bundle_path = path.parent / "bundle.json"
        bundle = json.loads(bundle_path.read_bytes()) if bundle_path.exists() else None
        result = grade(cases[row["case_id"]], bundle, record)
        graded.append({**row, "automatic_passed": result["automatic_passed"]})
        checked.append(
            {
                "receipt": row["receipt"],
                "receipt_sha256": hashlib.sha256(raw).hexdigest(),
                "original_grading": record["grading"],
                "current_grading": result,
            }
        )
    _, fingerprint = implementation_manifest()
    accounting = None
    if meta["planned"]["mode"] == "live" and meta.get("max_total_tokens"):
        reconciled = prior_spend(
            root,
            provider=meta["provider"],
            endpoint=meta["endpoint"],
            model=meta["model"],
            token_limit=meta["max_total_tokens"],
            require_stopped=False,
        )
        accounting = {
            "known_tokens": reconciled["known_tokens"],
            "provider_requests": reconciled["provider_requests"],
            "ancestry_fully_reconciled": True,
        }
    return {
        "run_id": meta["run_id"],
        "model_execution_source_sha256": meta["implementation_sha256"],
        "grading_implementation_sha256": fingerprint,
        "original_summary_sha256": hashlib.sha256((root / "summary.json").read_bytes()).hexdigest(),
        "raw_receipts_modified": False,
        "new_model_requests": 0,
        "accounting": accounting,
        "records": checked,
        "corrected_automatic_summary": summarize(
            graded, meta["planned"], stopped=original["stopped"]
        ),
        "scope": "Grader corrections only; original FAILs remain bound by hash. Semantic review is separate and no business quality or Agent improvement is inferred.",
    }
