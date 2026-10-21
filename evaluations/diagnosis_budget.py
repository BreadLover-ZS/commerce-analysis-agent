"""Reconcile a completed prior paid receipt before carrying its spend forward.

This carries every completed attempt and its charges. An unresolved request
cannot be turned into a new, apparently free attempt by selecting another output.
"""

import hashlib
import json
import os
from pathlib import Path


def valid_usage(usage):
    return (
        isinstance(usage, dict)
        and all(
            type(usage.get(k)) is int and 0 <= usage[k] <= 2147483647
            for k in ("prompt_tokens", "completion_tokens", "total_tokens")
        )
        and usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
    )


def prior_spend(
    directory, *, provider, endpoint, model, token_limit, require_stopped=True, _seen=()
):
    """Reconcile the complete immutable ancestry, never just the most recent sends."""
    if directory is None:
        return {
            "known_tokens": 0,
            "provider_requests": 0,
            "prior_run": None,
            "_rows": [],
            "_traces": [],
        }
    root = Path(directory).resolve()
    if root in _seen or len(_seen) >= 16:
        raise ValueError("预算承接链循环或过长")
    files = {}

    def read(name, *, lines=False):
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("前轮付费回执路径非法或缺失")
        data = path.read_bytes()
        files[name] = hashlib.sha256(data).hexdigest()
        return [json.loads(line) for line in data.splitlines()] if lines else json.loads(data)

    meta, summary = read("run.json"), read("summary.json")
    if (
        meta.get("planned", {}).get("mode") != "live"
        or any(
            meta.get(k) != v
            for k, v in {
                "provider": provider,
                "endpoint": endpoint,
                "model": model,
                "max_total_tokens": token_limit,
            }.items()
        )
        or (require_stopped and not summary.get("stopped"))
    ):
        raise ValueError("前轮须为相同模型和总额度的已终止轮次")
    parent = meta.get("prior_paid_budget", {}).get("prior_run")
    carry_mode = meta.get("budget_carry_mode", "resume")
    if carry_mode not in {"resume", "accounting_only"} or (
        carry_mode == "accounting_only" and not parent
    ):
        raise ValueError("预算承接方式无效")
    base = {"known_tokens": 0, "provider_requests": 0, "_rows": [], "_traces": []}
    if parent:
        if (
            not isinstance(parent, dict)
            or not {"directory", "run_id", "files_sha256"} <= parent.keys()
        ):
            raise ValueError("历史预算链引用不完整")
        base = prior_spend(
            parent["directory"],
            provider=provider,
            endpoint=endpoint,
            model=model,
            token_limit=meta["prior_paid_budget"].get("max_total_tokens", token_limit),
            require_stopped=False,
            _seen=(*_seen, root),
        )
        if (
            type(base.get("max_total_tokens", token_limit)) is not int
            or base.get("max_total_tokens", token_limit) > token_limit
            or parent != base["prior_run"]
            or meta["prior_paid_budget"]["known_tokens"] != base["known_tokens"]
            or meta["prior_paid_budget"]["provider_requests"] != base["provider_requests"]
        ):
            raise ValueError("历史预算链引用或扣费已变化")
    rows = read("progress.jsonl", lines=True)
    # A new implementation/plan may carry charges, never old quality evidence.
    # Resuming that experiment later keeps its accounting prefix separate too.
    prefix_traces = (
        base["_traces"]
        if carry_mode == "accounting_only"
        else base.get("_accounting_prefix_traces", [])
    )
    prefix_tokens = (
        base["known_tokens"]
        if carry_mode == "accounting_only"
        else base.get("_accounting_prefix_tokens", 0)
    )
    traces, ledger_tokens = list(prefix_traces), prefix_tokens
    names = [row["receipt"] for row in rows]
    if len(names) != len(set(names)):
        raise ValueError("前轮回执重复")
    for row in rows:
        record = read(row["receipt"])
        metrics = record.get("task", {}).get("metrics", {})
        if record.get("measurement") != row or metrics.get("usage_complete") is not True:
            raise ValueError("前轮任务用量未知或进度与回执不一致，禁止付费重试")
        if row.get("provider_requests") != len(record.get("provider_trace", [])):
            raise ValueError("前轮逐任务HTTP请求数不一致")
        for key in ("input_tokens", "output_tokens"):
            if type(metrics.get(key)) is not int or metrics[key] < 0:
                raise ValueError("前轮Task用量非法")
            ledger_tokens += metrics[key]
        traces.extend(record.get("provider_trace", []))
    for sequence, trace in enumerate(traces, 1):
        usage = (trace.get("response") or {}).get("usage")
        if (
            trace.get("sequence") != sequence
            or trace.get("http_status") != 200
            or not valid_usage(usage)
            or trace.get("known_tokens") != usage["total_tokens"]
            or type(trace.get("token_reservation")) is not int
            or usage["total_tokens"] > trace["token_reservation"]
        ):
            raise ValueError("前轮HTTP用量无法完整核对，禁止付费重试")
    events = read("budget-journal.jsonl", lines=True)
    spent, reserved, sent, settled = (
        base["known_tokens"],
        0,
        base["provider_requests"],
        base["provider_requests"],
    )
    if parent:
        expected_carry = {
            "event": "carried_prior_usage",
            "spent_tokens": spent,
            "reserved_tokens": 0,
            "provider_requests": sent,
            "prior_run_id": parent["run_id"],
        }
        if (
            not events
            or events[0] != expected_carry
            or (carry_mode == "resume" and rows[: len(base["_rows"])] != base["_rows"])
            or traces[:sent] != base["_traces"]
        ):
            raise ValueError("历史运行或预算被丢弃、修改或未完整承接")
        events = events[1:]
    for event in events:
        kind = event.get("event")
        if kind == "reserved_before_send":
            sent += 1
            if reserved or sent > len(traces) or event.get("sequence") != sent:
                raise ValueError("前轮journal预占序列不一致")
            reserved = traces[sent - 1]["token_reservation"]
            if reserved < 1 or event.get("reservation") != reserved:
                raise ValueError("前轮journal预占非法")
        elif kind == "known_usage":
            if (
                not reserved
                or event.get("sequence") != sent
                or event.get("usage_tokens") != traces[sent - 1]["known_tokens"]
            ):
                raise ValueError("前轮journal结算与HTTP用量不一致")
            spent += event["usage_tokens"]
            reserved = 0
            settled += 1
        elif kind == "denied_before_send" and not reserved:
            pass
        else:
            raise ValueError("前轮存在未知用量或不支持的journal事件，禁止付费重试")
        if (
            event.get("spent_tokens") != spent
            or event.get("reserved_tokens") != reserved
            or spent + reserved > token_limit
        ):
            raise ValueError("前轮journal累计预算不一致或超限")
    state = summary.get("token_budget", {})
    if (
        not rows
        or len(rows) != summary.get("executed_runs")
        or summary.get("run_id") != meta.get("run_id")
        or sent != settled
        or sent != len(traces)
        or sent != summary.get("provider_requests")
        or reserved
        or spent != ledger_tokens
        or state.get("max_total_tokens") != token_limit
        or state.get("known_tokens") != spent
        or state.get("unresolved_reserved_tokens") != 0
        or state.get("upper_bound_charged_tokens") != spent
    ):
        raise ValueError("前轮汇总、Task、HTTP和journal用量未完整对账")
    return {
        "known_tokens": spent,
        "provider_requests": sent,
        "max_total_tokens": token_limit,
        "prior_run": {"directory": str(root), "run_id": meta["run_id"], "files_sha256": files},
        "_meta": meta,
        "_rows": rows,
        "_traces": traces,
        "_accounting_prefix_traces": prefix_traces,
        "_accounting_prefix_tokens": prefix_tokens,
    }


def resume_cases(previous, planned, signatures, manifest, groups, *, safety_regrade=None):
    """Only resume an exact plan at a complete case boundary, including all FAILs."""
    if not previous["prior_run"]:
        return set()
    meta, rows = previous["_meta"], previous["_rows"]
    if meta["planned"] != planned or any(meta.get(k) != v for k, v in signatures.items()):
        raise ValueError("续评须保持完整计划、问题集、预期及门槛不变")
    old = meta["source_manifest"]
    for name in set(old) | set(manifest):
        if not (
            name.startswith(("app/", "migrations/"))
            or name in {"uv.lock", "pyproject.toml", ".env.example"}
        ):
            continue
        if old.get(name) == manifest.get(name):
            continue
        # The earlier label-only correction is independently proven by reversing
        # that exact expression and comparing the entire original source hash.
        if name == "app/diagnosis/graph.py":
            source = Path(__file__).resolve().parents[1].joinpath(name).read_text()
            corrected = 'if model is not None and getattr(model, "diagnosis_protocol_stub", False)'
            original = source.replace(corrected, "if model is not None")
            if source.count(corrected) == 1 and hashlib.sha256(
                original.encode()
            ).hexdigest() == old.get(name):
                continue
        raise ValueError("业务实现已变化，不能把旧轨迹拼入新放行轮次")
    slots = [(r["case_id"], r["repeat"], r["group"]) for r in rows]
    expected = [
        (c, i, g)
        for c in planned["case_ids"]
        for i in range(1, planned["repeats"] + 1)
        for g in groups[c]
    ]
    done = {(r["case_id"], r["repeat"]) for r in rows}
    if slots != expected[: len(slots)] or any(
        [g for c, i, g in slots if (c, i) == pair] != groups[pair[0]] for pair in done
    ):
        raise ValueError("续评前重复须保留全部组；只能承接完整前缀，不得挑选最好结果")
    for row in rows:
        record = json.loads(
            Path(previous["prior_run"]["directory"]).joinpath(row["receipt"]).read_text()
        )
        if critical_failure(record["grading"], critical_case=row["critical"]):
            rechecked = safety_regrade(row, record) if safety_regrade else None
            if rechecked is None or critical_failure(rechecked, critical_case=row["critical"]):
                raise ValueError("前轮关键安全失败阻止继续放行轮次")
            previous.setdefault("safety_rechecks", []).append(
                {
                    "receipt": row["receipt"],
                    "original_sha256": previous["prior_run"]["files_sha256"][row["receipt"]],
                    "original_grading": record["grading"],
                    "rechecked_grading": rechecked,
                }
            )
    return done


def claim_prior_budget(previous, output, run_id):
    """An exclusive durable claim prevents two new rounds reusing the same charge base."""
    if previous["prior_run"] is None:
        return
    source = Path(previous["prior_run"]["directory"])
    path = source.with_name(source.name + ".budget-continuation.json")
    with path.open("x", encoding="utf-8") as stream:
        path.chmod(0o600)
        json.dump(
            {
                "prior_run_id": previous["prior_run"]["run_id"],
                "next_run_id": run_id,
                "next_directory": str(output),
                "carried_tokens": previous["known_tokens"],
            },
            stream,
        )
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def critical_failure(grading, *, critical_case=False):
    """Completion failures are retained; output safety failures stop the round."""
    # A critical scenario still needs 100% acceptance, but its label does not
    # make a conservative refusal an emitted fact/authorization failure.
    checks = grading.get("assertions", {})
    keys = {
        "immutable_source_versions",
        "facts_match_raw_records",
        "bounded_execution",
        "no_unsupported_report_fact",
    }
    return any(checks.get(key) is False for key in keys) or not checks
