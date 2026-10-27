"""One used-input Task per paid invocation; explicit holds, first-failure stop, no release claim."""

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import httpx as httpx

from evaluations.diagnosis import (
    implementation_manifest,
    live_configuration,
    live_model,
)
from evaluations.diagnosis_budget import valid_usage
from evaluations.diagnosis_cases import load_cases
from evaluations.diagnosis_probe_accounting import HOLD, LIMIT, file_hash, verify_hold
from evaluations.diagnosis_reconciliation import validate_record_accounting
from evaluations.diagnosis_runtime import (
    admit,
    collect,
    execute,
    export_artifacts,
    new_task,
)


def failure_reason(record, grading):
    if record["task"]["metrics"].get("usage_complete") is not True:
        return "unknown_usage"
    if any(c["status"] != "SUCCEEDED" for c in record.get("model_invocations", [])):
        return "model_call_failed_or_not_sent"
    if any(s["status"] in {"REJECTED", "FAILED"} for s in record.get("steps", [])):
        return "proposal_or_tool_rejected"
    if any(c["status"] in {"FAILED", "UNKNOWN"} for c in record.get("tool_invocations", [])):
        return "tool_call_failed"
    validation = record["task"].get("validation") or {}
    if validation.get("passed") is False:
        return "report_validation_failed"
    if validation.get("semantic_status") in {"unsupported", "failed", "rejected"}:
        return "semantic_review_rejected"
    if validation.get("semantic_status") == "unavailable":
        return "semantic_review_unavailable"
    if validation.get("draft_fallback_used") or validation.get("fallback_used"):
        return "model_report_fallback"
    if record["task"].get("status") == "failed":
        return "task_failed"
    if (record["task"].get("report") or {}).get("stop_reason") == "no_information":
        return "no_information_stop"
    if not grading.get("automatic_passed"):
        return "automatic_grade_failed"
    return None


MAX_PROBE_ANCESTRY = 256


def audit_probe(root, _seen=()):
    """Recompute every new charge; a new UNKNOWN cannot silently become another hold."""
    root = Path(root).resolve()
    if root in _seen or len(_seen) >= MAX_PROBE_ANCESTRY:
        raise ValueError("probe_parent_cycle_or_depth_limit")
    meta = json.loads((root / "probe.json").read_bytes())
    from evaluations.diagnosis_grading import sha

    if sha(meta["source_manifest"]) != meta["implementation_sha256"]:
        raise ValueError("probe_source_manifest_changed")
    for name, checksum in meta["source_manifest"].items():
        source = (root / "source-snapshot" / name).resolve()
        if not source.is_relative_to(root / "source-snapshot") or file_hash(source) != checksum:
            raise ValueError("probe_source_snapshot_changed")
    parent = load_parent(meta["parent"], (*_seen, root))
    if parent.get("ancestry_depth", 0) >= MAX_PROBE_ANCESTRY:
        raise ValueError("probe_parent_depth_limit")
    if parent != meta["parent_accounting"]:
        raise ValueError("probe_parent_changed")
    record = json.loads((root / "receipt.json").read_bytes())
    summary = json.loads((root / "summary.json").read_bytes())
    if record["task"]["parameters"].get("budget", {}).get("stop_after_model_failure") is not True:
        raise ValueError("probe_failure_policy_not_frozen")
    if summary["receipt_sha256"] != file_hash(root / "receipt.json"):
        raise ValueError("probe_receipt_changed")
    validate_record_accounting(record, root, meta["run_id"])
    traces = record["provider_trace"]
    spent, reserved, sent = parent["known_tokens"], 0, 0
    for line in (root / "budget-journal.jsonl").read_bytes().splitlines():
        event = json.loads(line)
        if event.get("frozen_unknown_tokens") != HOLD:
            raise ValueError("probe_legacy_hold_lost")
        if event["event"] == "reserved_before_send":
            sent += 1
            if reserved or sent > len(traces) or event["sequence"] != sent:
                raise ValueError("probe_reservation_sequence_invalid")
            reserved = traces[sent - 1]["token_reservation"]
            if event["reservation"] != reserved or reserved != HOLD:
                raise ValueError("probe_reservation_bound_invalid")
        elif event["event"] == "known_usage":
            if not reserved or event["sequence"] != sent:
                raise ValueError("probe_settlement_sequence_invalid")
            trace = traces[sent - 1]
            usage = (trace.get("response") or {}).get("usage")
            if (
                trace.get("sequence") != sent
                or trace.get("http_status") != 200
                or not valid_usage(usage)
                or event["usage_tokens"] != usage["total_tokens"]
                or trace.get("known_tokens") != usage["total_tokens"]
                or usage["total_tokens"] > HOLD
            ):
                raise ValueError("probe_usage_invalid")
            spent += usage["total_tokens"]
            reserved = 0
        elif event["event"] == "denied_before_send" and not reserved:
            pass
        else:
            raise ValueError("new_probe_unknown_or_incomplete_journal")
        if (
            event["spent_tokens"] != spent
            or event["reserved_tokens"] != reserved
            or spent + reserved + HOLD > LIMIT
        ):
            raise ValueError("probe_budget_totals_invalid")
    state = summary["budget"]
    if (
        reserved
        or sent != len(traces)
        or state["known_tokens"] != spent
        or state["unresolved_reserved_tokens"] != 0
        or state["frozen_unknown_tokens"] != HOLD
        or state["upper_bound_charged_tokens"] != spent + HOLD
        or state["max_total_tokens"] != LIMIT
        or summary["confirmed_http_requests"] != parent["confirmed_http_requests"] + sent
    ):
        raise ValueError("probe_summary_inconsistent")
    return {
        "known_tokens": spent,
        "frozen_unknown_tokens": HOLD,
        "confirmed_http_requests": parent["confirmed_http_requests"] + sent,
        "parent_manifest_sha256": file_hash(root / "probe.json"),
        "parent_summary_sha256": file_hash(root / "summary.json"),
        "parent_receipt_sha256": file_hash(root / "receipt.json"),
        "parent_journal_sha256": file_hash(root / "budget-journal.jsonl"),
        "parent_failed": summary["failure_reason"] is not None,
        "parent_implementation_sha256": meta["implementation_sha256"],
        "run_id": meta["run_id"],
        "directory": str(root),
        "legacy_root": parent["legacy_root"],
        "ancestry_depth": parent.get("ancestry_depth", 0) + 1,
    }


def load_parent(path, _seen=()):
    path = Path(path).resolve()
    if path.is_dir():
        return audit_probe(path, _seen)
    value = json.loads(path.read_bytes())
    verify_hold(value)
    return {
        "known_tokens": value["known_tokens"],
        "frozen_unknown_tokens": value["frozen_unknown_tokens"],
        "confirmed_http_requests": value["confirmed_http_requests"],
        "parent_certificate_sha256": file_hash(path),
        "parent_failed": False,
        "run_id": value["run_id"],
        "directory": value["directory"],
        "legacy_root": value["directory"],
        "ancestry_depth": value["ancestry_depth"],
    }


def validate_selection(cases_path, case_id, group, legacy_root):
    cases, _ = load_cases(cases_path, case_ids=[case_id])
    case = cases[0]
    legacy_root = Path(legacy_root).resolve()
    meta = json.loads((legacy_root / "run.json").read_bytes())
    if file_hash(cases_path) != meta["cases_file_sha256"]:
        raise ValueError("probe_requires_exact_previously_used_case_source")
    rows = [json.loads(line) for line in (legacy_root / "progress.jsonl").read_bytes().splitlines()]
    if not any(r["case_id"] == case_id and r["group"] == group for r in rows):
        raise ValueError("unexecuted_holdout_must_not_be_used_as_probe")
    if case.execution != "diagnostic" or group not in {"fixed", "agent", "additional"}:
        raise ValueError("probe_requires_business_task")
    if group == "fixed" and case.expected.baseline_capability != "common":
        raise ValueError("fixed_probe_outside_common_capability")
    return case


def require_prior_review(parent, fingerprint, repair_evidence=None):
    if "parent_receipt_sha256" not in parent:
        return
    root = Path(parent["directory"])
    review = json.loads((root / "review.json").read_bytes())
    if review.get("receipt_sha256") != parent["parent_receipt_sha256"]:
        raise ValueError("probe_review_not_bound_to_receipt")
    if parent["parent_failed"] or review.get("verdict") == "FAIL":
        if repair_evidence is None or fingerprint == parent["parent_implementation_sha256"]:
            raise ValueError(
                "failure_requires_root_cause_fix_and_local_verification_before_new_batch"
            )
        repair = json.loads(Path(repair_evidence).read_bytes())
        if (
            repair.get("failed_receipt_sha256") != parent["parent_receipt_sha256"]
            or repair.get("implementation_sha256") != fingerprint
            or repair.get("local_verification_passed") is not True
        ):
            raise ValueError("repair_evidence_not_bound")
    elif review.get("verdict") != "PASS":
        raise ValueError("probe_review_pending_no_further_paid_calls")


def validate_probe_http_budget(case, group, limit, *, answer_boundary_version=1):
    """A source-grounded lower bound, not a promise of model-plan sufficiency."""
    if type(limit) is not int or not 1 <= limit <= (2 if group == "fixed" else 12):
        raise ValueError("probe_http_cap_out_of_bounds")
    minimum = 2 if group == "fixed" else 0
    if group != "fixed":
        from app.commerce.answer_contract_v3 import entity_selection
        from app.commerce.answer_versions import contract_module

        request_contract = contract_module(
            {"answer_boundary_version": answer_boundary_version}
        ).request_contract
        from evaluations.diagnosis_cases import reference_answer

        c = request_contract(case.query)
        catalog = [{"sku": p.sku, "category": p.category} for p in case.sales.products]
        selected = entity_selection(case.query, case.scope_skus, catalog)["selected"]
        complete = (
            bool(selected)
            and reference_answer(case.model_copy(update={"scope_skus": selected}))["comparable"]
        )
        if complete and c["request_kind"] == "values":
            per_sku = c["per_entity"] and len(selected) > 1 and bool(c["value_metrics"])
            minimum = int(per_sku) + int(c["amount_identity_requested"])
            minimum = max(minimum, len(c["contribution_dimensions"]))
            if c["per_entity"] and "daily" in c["goals"] and c["dimension"] == "products":
                minimum += len(selected)
    if limit < minimum:
        raise ValueError("probe_http_budget_below_structural_minimum")
    return {"minimum_known_calls": minimum, "hard_http_cap": limit, "lower_bound_only": True}


async def run(args):
    # No key reads, output directory, or claim until all gates pass.
    parent = load_parent(args.parent)
    if parent.get("ancestry_depth", 0) >= MAX_PROBE_ANCESTRY:
        raise ValueError("probe_parent_depth_limit")
    if args.legacy_root.resolve() != Path(parent["legacy_root"]).resolve():
        raise ValueError("probe_legacy_source_not_bound_to_hold")
    case = validate_selection(args.cases, args.case_id, args.group, args.legacy_root)
    files, fingerprint = implementation_manifest()
    require_prior_review(parent, fingerprint, args.repair_evidence)
    if (
        args.max_total_tokens != LIMIT
        or parent["known_tokens"] + parent["frozen_unknown_tokens"] + HOLD > LIMIT
    ):
        raise ValueError("probe_budget_not_authorized_or_insufficient")
    budget_plan = validate_probe_http_budget(case, args.group, args.max_http)
    from evaluations.diagnosis_one import execute_one

    hooks = SimpleNamespace(
        live_configuration=live_configuration,
        admit=admit,
        new_task=new_task,
        execute=execute,
        collect=collect,
        export_artifacts=export_artifacts,
        live_model=live_model,
    )
    return await execute_one(args, parent, case, files, fingerprint, budget_plan, hooks)


def parser():
    p = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    p.add_argument("--parent", type=Path, required=True)
    p.add_argument("--legacy-root", type=Path, required=True)
    p.add_argument("--cases", type=Path, required=True)
    p.add_argument("--case-id", required=True)
    p.add_argument("--group", choices=["fixed", "agent", "additional"], required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--max-http", type=int, required=True)
    p.add_argument("--max-total-tokens", type=int, required=True)
    p.add_argument("--allow-paid", action="store_true")
    p.add_argument("--api-key-env", required=True)
    p.add_argument("--repair-evidence", type=Path)
    return p


if __name__ == "__main__":
    import asyncio

    raise SystemExit(1 if asyncio.run(run(parser().parse_args())) else 0)
