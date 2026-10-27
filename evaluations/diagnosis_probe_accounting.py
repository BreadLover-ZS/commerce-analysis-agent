"""Explicit conservative carry for ONE frozen legacy UNKNOWN; never a zero certificate."""

import ast
import hashlib
import json
from pathlib import Path

from evaluations.diagnosis_budget import prior_spend, valid_usage
from evaluations.diagnosis_grading import sha

LEGACY_SOURCE = "1bb3bbb8302c4bc5c471dec6d1193636222734c384cb967d07ed74dcb5b85db7"
LEGACY_RUN = "41f75925-d572-428f-b0ad-2b5d451f65e4"
LEGACY_UNKNOWN = "2acf184f-1ec7-5147-9d07-617e35b2d087"
HOLD = 1051576
LIMIT = 20000000


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def legacy_hold(root, source, authorization):
    """Audit known spend independently; an unbound denial NEVER proves known zero."""
    root, source, authorization = map(lambda p: Path(p).resolve(), (root, source, authorization))
    hashes = {}

    def read(name, lines=False):
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError("legacy_evidence_path_invalid")
        raw = path.read_bytes()
        hashes[name] = hashlib.sha256(raw).hexdigest()
        return [json.loads(line) for line in raw.splitlines()] if lines else json.loads(raw)

    auth = json.loads(authorization.read_bytes())
    if (
        auth.get("authorized_cumulative_token_limit") != LIMIT
        or auth.get("authorized_legacy_unknown_hold_tokens") != HOLD
        or auth.get("legacy_unknown_remains_unknown") is not True
        or auth.get("new_unknown_automatically_authorized_for_conservative_carry") is not False
    ):
        raise ValueError("explicit_conservative_hold_authorization_required")
    meta, summary = read("run.json"), read("summary.json")
    if (
        meta.get("implementation_sha256") != LEGACY_SOURCE
        or meta.get("run_id") != LEGACY_RUN
        or sha(meta["source_manifest"]) != LEGACY_SOURCE
        or meta.get("context_ceiling_tokens") != 1048576
        or meta.get("model") != "deepseek-flash"
        or meta.get("provider") != "DeepSeek"
        or meta.get("endpoint") != "https://api.deepseek.com"
        or meta.get("max_total_tokens") != 12000000
    ):
        raise ValueError("unsupported_legacy_source_or_provider")
    read("cases-source.json")
    if hashes["cases-source.json"] != meta["cases_file_sha256"]:
        raise ValueError("legacy_case_source_changed")
    for name, expected in meta["source_manifest"].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source) or file_hash(path) != expected:
            raise ValueError("legacy_source_snapshot_changed")
    tree = ast.parse((source / "evaluations/diagnosis.py").read_bytes())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "live_model")
    call = next(n.value for n in fn.body if isinstance(n, ast.Return))
    keywords = {k.arg: k.value.value for k in call.keywords if isinstance(k.value, ast.Constant)}
    if keywords.get("max_tokens") != 3000 or keywords.get("max_retries") != 0:
        raise ValueError("single_attempt_hard_bound_not_proven")
    parent = meta["prior_paid_budget"]["prior_run"]
    base = prior_spend(
        parent["directory"],
        provider=meta["provider"],
        endpoint=meta["endpoint"],
        model=meta["model"],
        token_limit=meta["prior_paid_budget"]["max_total_tokens"],
        require_stopped=False,
    )
    if (
        base["prior_run"] != parent
        or meta["prior_paid_budget"]["known_tokens"] != base["known_tokens"]
        or meta["prior_paid_budget"]["provider_requests"] != base["provider_requests"]
        or meta.get("budget_carry_mode") != "accounting_only"
    ):
        raise ValueError("legacy_ancestry_changed")
    rows, journal = read("progress.jsonl", True), read("budget-journal.jsonl", True)
    if len(rows) != summary["executed_runs"] or len({r["receipt"] for r in rows}) != len(rows):
        raise ValueError("legacy_progress_incomplete")
    unknown, traces, task_known = [], list(base["_traces"]), base["known_tokens"]
    for row in rows:
        record = read(row["receipt"])
        metrics = record["task"]["metrics"]
        calls = record.get("model_invocations", [])
        if record["measurement"] != row or row["provider_requests"] != len(
            record["provider_trace"]
        ):
            raise ValueError("legacy_receipt_measurement_changed")
        bound_unknown = [c for c in calls if c["status"] in {"UNKNOWN", "STARTED"}]
        unknown.extend((row["receipt"], c) for c in bound_unknown)
        if (metrics.get("usage_complete") is not True) != bool(bound_unknown):
            raise ValueError("legacy_unknown_coverage_incomplete")
        if record["task"].get("graph_version") == "diagnosis-v15":
            known = [c for c in calls if c not in bound_unknown]
            if len(calls) != metrics["model_calls"] or any(
                c["status"] not in {"SUCCEEDED", "FAILED"} or c["usage_complete"] is not True
                for c in known
            ):
                raise ValueError("legacy_model_attempt_coverage_invalid")
            if any(
                sum(c["usage"][k] for c in known) != metrics[k]
                for k in ("input_tokens", "output_tokens")
            ):
                raise ValueError("legacy_known_invocation_usage_changed")
        if any(
            type(metrics[k]) is not int or metrics[k] < 0 for k in ("input_tokens", "output_tokens")
        ):
            raise ValueError("legacy_known_usage_invalid")
        current = record["provider_trace"]
        for trace in current:
            usage = (trace.get("response") or {}).get("usage")
            if trace.get("http_status") != 200 or not valid_usage(usage):
                raise ValueError("legacy_http_usage_unresolved")
        if any(
            sum(t["response"]["usage"][part] for t in current) != metrics[key]
            for part, key in (
                ("prompt_tokens", "input_tokens"),
                ("completion_tokens", "output_tokens"),
            )
        ):
            raise ValueError("legacy_task_http_known_usage_mismatch")
        task_known += metrics["input_tokens"] + metrics["output_tokens"]
        traces.extend(current)
    if (
        len(unknown) != 1
        or unknown[0][1]["call_id"] != LEGACY_UNKNOWN
        or unknown[0][0] != rows[-1]["receipt"]
        or unknown[0][1]["status"] != "UNKNOWN"
        or unknown[0][1].get("usage") is not None
        or unknown[0][1].get("usage_complete") is not False
    ):
        raise ValueError("exactly_one_final_legacy_unknown_required")
    for i, trace in enumerate(traces, 1):
        if (
            trace["sequence"] != i
            or trace["known_tokens"] != trace["response"]["usage"]["total_tokens"]
            or type(trace["token_reservation"]) is not int
            or trace["known_tokens"] > trace["token_reservation"]
        ):
            raise ValueError("legacy_http_sequence_or_usage_changed")
    spent, sent, reserved = base["known_tokens"], base["provider_requests"], 0
    if journal[0] != {
        "event": "carried_prior_usage",
        "spent_tokens": spent,
        "reserved_tokens": 0,
        "provider_requests": sent,
        "prior_run_id": parent["run_id"],
    }:
        raise ValueError("legacy_carry_event_invalid")
    denied = 0
    for event in journal[1:]:
        if event["event"] == "reserved_before_send":
            sent += 1
            if (
                reserved
                or sent > len(traces)
                or event["sequence"] != sent
                or event["reservation"] != traces[sent - 1]["token_reservation"]
            ):
                raise ValueError("legacy_reservation_invalid")
            reserved = event["reservation"]
        elif event["event"] == "known_usage":
            if (
                not reserved
                or event["sequence"] != sent
                or event["usage_tokens"] != traces[sent - 1]["known_tokens"]
            ):
                raise ValueError("legacy_settlement_invalid")
            spent += event["usage_tokens"]
            reserved = 0
        elif event["event"] == "denied_before_send" and not reserved:
            denied += 1
            if (
                event.get("reason") != "token_budget_capacity"
                or event.get("next_reservation") != HOLD
            ):
                raise ValueError("legacy_denial_bound_invalid")
        else:
            raise ValueError("legacy_journal_unresolved")
        if event["spent_tokens"] != spent or event["reserved_tokens"] != reserved:
            raise ValueError("legacy_journal_totals_changed")
    if (
        denied != 1
        or reserved
        or sent != len(traces)
        or spent != task_known
        or spent != summary["token_budget"]["known_tokens"]
        or sent != summary["provider_requests"]
        or summary["token_budget"]["unresolved_reserved_tokens"] != 0
        or spent != auth["historical_known_tokens"]
        or spent != 10954662
        or spent + HOLD != auth["effective_initial_occupied_tokens"]
    ):
        raise ValueError("legacy_summary_totals_invalid")
    return {
        "schema_version": 1,
        "kind": "CONSERVATIVE_HOLD_NOT_RECONCILIATION",
        "directory": str(root),
        "run_id": meta["run_id"],
        "files_sha256": hashes,
        "parent": parent,
        "legacy_source_directory": str(source),
        "legacy_source_sha256": LEGACY_SOURCE,
        "authorization_file": str(authorization),
        "authorization_sha256": file_hash(authorization),
        "verifier_sha256": file_hash(__file__),
        "known_tokens": spent,
        "confirmed_http_requests": sent,
        "ancestry_depth": base["ancestry_depth"] + 1,
        "frozen_unknown_tokens": HOLD,
        "unknown_call_id": unknown[0][1]["call_id"],
        "unknown_receipt": unknown[0][0],
        "original_unknown_preserved": True,
        "strict_reconciliation_passed": False,
        "effective_initial_occupied_tokens": spent + HOLD,
        "authorized_cumulative_token_limit": LIMIT,
        "original_quality_results_inherited": False,
    }


def verify_hold(value):
    if value != legacy_hold(
        value["directory"], value["legacy_source_directory"], value["authorization_file"]
    ):
        raise ValueError("conservative_hold_or_evidence_changed")
    return value
