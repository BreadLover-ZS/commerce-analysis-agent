"""Append-only accounting explanations. Never repair raw receipts or quality scores."""

import argparse
import hashlib
import json
from pathlib import Path

from app.core.model_boundary import verify_proof

VERIFIER = "bound-unsent-v1"
IDENTITY = (
    "call_id",
    "task_id",
    "step_id",
    "phase",
    "attempt",
    "lease_token",
    "prompt_hash",
    "schema_hash",
    "model_version",
)


def validate_record_accounting(record, root, run_id, amended_call=None):
    """A v2 Task needs a complete invocation/HTTP/proof bijection, including zero calls."""
    calls, metrics = record.get("model_invocations", []), record["task"]["metrics"]
    if len(calls) != metrics.get("model_calls") or len({c["call_id"] for c in calls}) != len(calls):
        raise ValueError("v2_invocation_coverage_incomplete")
    known = []
    traces = record.get("provider_trace", [])
    paid = []
    for call in calls:
        if call["call_id"] == amended_call:
            continue
        if call["status"] not in {"SUCCEEDED", "FAILED", "NOT_SENT"} or not call["usage_complete"]:
            raise ValueError("v2_unknown_invocation_requires_explicit_certificate")
        usage = call.get("usage")
        if not isinstance(usage, dict) or any(
            type(usage.get(k)) is not int or usage[k] < 0 for k in ("input_tokens", "output_tokens")
        ):
            raise ValueError("v2_invocation_usage_invalid")
        known.append(call)
        identity = {k: call[k] for k in IDENTITY}
        if call["status"] == "NOT_SENT":
            proofs = [
                e["data"]["proof"]
                for e in record.get("events", [])
                if e.get("kind") == "model_send_denied"
                and e.get("data", {}).get("call_id") == call["call_id"]
            ]
            if (
                usage != {"input_tokens": 0, "output_tokens": 0}
                or call.get("request_id") is not None
                or call.get("validated_output") is not None
                or len(proofs) != 1
                or Path(proofs[0]["journal"]).resolve()
                != Path(root).resolve() / "budget-journal.jsonl"
                or proofs[0]["event"].get("run_id") != run_id
                or call.get("response_digest") != proofs[0]["event_sha256"]
                or not verify_proof(proofs[0], identity)
            ):
                raise ValueError("v2_not_sent_proof_invalid")
        else:
            bound = [
                t
                for t in traces
                if t.get("attempt_identity") == identity and t.get("run_id") == run_id
            ]
            if len(bound) != 1:
                raise ValueError("v2_http_attempt_identity_mismatch")
            u = (bound[0].get("response") or {}).get("usage", {})
            if (
                u.get("prompt_tokens") != usage["input_tokens"]
                or u.get("completion_tokens") != usage["output_tokens"]
            ):
                raise ValueError("v2_http_invocation_usage_mismatch")
            paid.append(bound[0])
    if len(paid) != len(traces) or any(
        sum(c["usage"][k] for c in known) != metrics[k] for k in ("input_tokens", "output_tokens")
    ):
        raise ValueError("v2_task_http_usage_not_bijective")


def candidate(directory):
    root = Path(directory).resolve()
    files = {}

    def read(name, lines=False):
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("reconciliation_evidence_path_invalid")
        data = path.read_bytes()
        files[name] = hashlib.sha256(data).hexdigest()
        return [json.loads(line) for line in data.splitlines()] if lines else json.loads(data)

    meta, summary = read("run.json"), read("summary.json")
    rows, events = read("progress.jsonl", True), read("budget-journal.jsonl", True)
    records = [(row["receipt"], read(row["receipt"])) for row in rows]
    unknown = [
        (name, record, c)
        for name, record in records
        for c in record.get("model_invocations", [])
        if c["status"] in {"UNKNOWN", "STARTED"}
    ]
    if len(unknown) != 1:
        raise ValueError("reconciliation_requires_one_identified_unknown")
    name, record, call = unknown[0]
    identity = {k: call[k] for k in IDENTITY}
    denials = [e for e in events if e.get("event") == "denied_before_send"]
    if len(denials) != 1 or denials[0].get("attempt_identity") != identity:
        # v15 has NO direct call/task/lease/prompt identity. Ordering and a missing
        # trace cannot rule out a submitted call. Keep it blocked deliberately.
        raise ValueError("historical_denial_has_no_unique_direct_attempt_binding")
    if (
        meta.get("accounting_protocol") != 2
        or record["task"]["parameters"].get("accounting_protocol") != 2
    ):
        raise ValueError("reconciliation_protocol_not_frozen")
    if denials[0].get("run_id") != meta["run_id"] or name != rows[-1]["receipt"]:
        raise ValueError("reconciliation_run_or_final_attempt_mismatch")
    proofs = [
        e["data"]["proof"]
        for e in record.get("events", [])
        if e.get("kind") == "model_send_denied"
        and e.get("data", {}).get("call_id") == call["call_id"]
    ]
    if (
        len(proofs) != 1
        or Path(proofs[0]["journal"]).resolve() != root / "budget-journal.jsonl"
        or not verify_proof(proofs[0], identity)
    ):
        raise ValueError("reconciliation_durable_proof_missing_or_invalid")
    for receipt, value in records:
        metrics = value["task"]["metrics"]
        if metrics.get("usage_complete") is not True and receipt != name:
            raise ValueError("reconciliation_other_unknown_task")
        if value["task"]["parameters"].get("accounting_protocol") == 2:
            calls = value.get("model_invocations", [])
            if len(calls) != metrics["model_calls"]:
                raise ValueError("reconciliation_invocation_coverage_incomplete")
            known = [c for c in calls if c["call_id"] != call["call_id"]]
            if any(
                c["status"] not in {"SUCCEEDED", "FAILED", "NOT_SENT"}
                or not c["usage_complete"]
                or not isinstance(c["usage"], dict)
                for c in known
            ):
                raise ValueError("reconciliation_other_unknown_invocation")
            if any(
                sum(c["usage"][key] for c in known) != metrics[key]
                for key in ("input_tokens", "output_tokens")
            ):
                raise ValueError("reconciliation_known_usage_mismatch")
    return {
        "schema_version": 1,
        "verifier": VERIFIER,
        "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "run_id": meta["run_id"],
        "implementation_sha256": meta["implementation_sha256"],
        "files_sha256": files,
        "receipt": name,
        "identity": identity,
        "original_status": call["status"],
        "effective_accounting": "NOT_SENT_KNOWN_ZERO",
        "proof_sha256": proofs[0]["event_sha256"],
        "known_tokens": summary["token_budget"]["known_tokens"],
        "provider_requests": summary["provider_requests"],
        "original_quality_scores_preserved": True,
    }


def verify_certificate(directory, certificate):
    value = (
        json.loads(Path(certificate).read_bytes())
        if not isinstance(certificate, dict)
        else certificate
    )
    if value != candidate(directory):
        raise ValueError("reconciliation_certificate_or_evidence_changed")
    return value


def inspect(directory):
    """A blocked assessment is a valid deliverable; it is NEVER a certificate."""
    try:
        value = candidate(directory)
        from evaluations.diagnosis_budget import prior_spend

        root = Path(directory)
        meta = json.loads((root / "run.json").read_bytes())
        checked = prior_spend(
            root,
            provider=meta["provider"],
            endpoint=meta["endpoint"],
            model=meta["model"],
            token_limit=meta["max_total_tokens"],
            reconciliation=value,
        )
        return {
            "status": "RECONCILIATION_VERIFIED",
            "certificate": value,
            "known_tokens": checked["known_tokens"],
            "provider_requests": checked["provider_requests"],
        }
    except (ValueError, KeyError, TypeError, OSError) as error:
        # No exception text from provider data, credentials or arbitrary file content.
        reason = str(error) if isinstance(error, ValueError) else "evidence_missing_or_invalid"
        return {
            "status": "RECONCILIATION_BLOCKED",
            "reason": reason,
            "certificate": None,
            "raw_records_modified": False,
            "provider_requests": 0,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--certificate-output", type=Path, help="仅在完整验证通过时追加写入独立更正证书"
    )
    args = parser.parse_args()
    value = inspect(args.directory)
    with args.output.open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    if args.certificate_output is not None and value["status"] == "RECONCILIATION_VERIFIED":
        with args.certificate_output.open("x") as stream:
            json.dump(value["certificate"], stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    print(json.dumps({"status": value["status"], "provider_requests": 0}))


if __name__ == "__main__":
    main()
