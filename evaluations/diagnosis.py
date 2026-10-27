"""G6 paired Worker evaluation. See docs/26-diagnosis-evaluation.md.

Demo never loads credentials. Live requires explicit provider/model/key-variable
and a paid request cap. Automatic checks and independent business review are
separate gates; this CLI never changes the default product entry point.
"""

import argparse
import asyncio
import hashlib
import json
import math
import os
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from evaluations.diagnosis_budget import (
    claim_prior_budget,
    critical_failure,
    prior_spend,
    resume_cases,
    valid_usage,
)
from evaluations.diagnosis_cases import DEFAULT_CASES, business_input, load_cases
from evaluations.diagnosis_grading import ASSERTIONS, grade, sha
from evaluations.diagnosis_harness import MODES, answer_clarification, run_harness
from evaluations.diagnosis_runtime import (
    ROOT,
    admit,
    collect,
    execute,
    export_artifacts,
    new_task,
    sandbox,
)


def write(path, value):
    with path.open("x", encoding="utf-8") as stream:
        path.chmod(0o600)
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def implementation_manifest():
    roots = (
        "app",
        "evaluations",
        "migrations",
        "scripts",
        "tests",
        "frontend/src",
        "frontend/e2e",
        ".github/workflows",
        "deploy",
        "docker",
    )
    suffixes = {
        ".py",
        ".json",
        ".ts",
        ".tsx",
        ".css",
        ".md",
        ".yaml",
        ".yml",
        ".toml",
        ".conf",
        ".sql",
        ".Dockerfile",
    }
    paths = []
    for directory in roots:
        for base, directories, names in os.walk(ROOT / directory):
            # Prune before traversal, preserving the original membership/hash
            # rules without walking every immutable historical result snapshot.
            directories[:] = [d for d in directories if d not in {"results", "__pycache__"}]
            for name in names:
                p = Path(base) / name
                if (
                    p.is_file()
                    and p.suffix in suffixes
                    and "results" not in p.relative_to(ROOT).parts
                    and "__pycache__" not in p.parts
                ):
                    paths.append(p)
    paths += [
        ROOT / name
        for name in (
            "pyproject.toml",
            "uv.lock",
            "alembic.ini",
            "frontend/package.json",
            "frontend/pnpm-lock.yaml",
            "frontend/vite.config.ts",
            "frontend/playwright.config.ts",
            "Dockerfile",
            "compose.yaml",
            ".env.example",
            "frontend/.env.example",
            ".dockerignore",
        )
        if (ROOT / name).is_file()
    ]
    manifest = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(set(paths))
    }
    return manifest, sha(manifest)


class RequestBudget:
    """Count SDK HTTP sends, including any attempted retry, before network I/O."""

    def __init__(
        self,
        limit,
        *,
        max_total_tokens=None,
        context_ceiling=None,
        journal=None,
        initial_spent_tokens=0,
        frozen_unknown_tokens=0,
        protocol=1,
        run_id=None,
    ):
        self.limit, self.sent, self.blocked = limit, 0, False
        self.traces = []
        self.token_limit, self.context_ceiling = max_total_tokens, context_ceiling
        if max_total_tokens is not None and (
            type(max_total_tokens) is not int
            or max_total_tokens < 1
            or type(context_ceiling) is not int
            or context_ceiling < 1
        ):
            raise ValueError("token预算须含正整数上限和已核验的模型上下文上限")
        if (
            type(initial_spent_tokens) is not int
            or initial_spent_tokens < 0
            or (
                initial_spent_tokens
                and (max_total_tokens is None or initial_spent_tokens > max_total_tokens)
            )
        ):
            raise ValueError("历史token扣费须为总预算内的非负整数")
        self.spent_tokens = initial_spent_tokens
        if (
            type(frozen_unknown_tokens) is not int
            or frozen_unknown_tokens < 0
            or (
                frozen_unknown_tokens
                and (
                    max_total_tokens is None
                    or initial_spent_tokens + frozen_unknown_tokens > max_total_tokens
                )
            )
        ):
            raise ValueError("历史未知冻结必须为累计预算内单列的非负整数")
        self.frozen_unknown_tokens = frozen_unknown_tokens
        self.reserved_tokens = 0
        self.denied_reason = None
        self.journal = journal
        self.protocol, self.run_id = protocol, run_id or str(uuid4())

    def event(self, kind, **fields):
        if self.journal is not None:
            with self.journal.open("a", encoding="utf-8") as stream:
                self.journal.chmod(0o600)
                stream.write(
                    json.dumps(
                        {
                            "event": kind,
                            "spent_tokens": self.spent_tokens,
                            "reserved_tokens": self.reserved_tokens,
                            **(
                                {"frozen_unknown_tokens": self.frozen_unknown_tokens}
                                if self.frozen_unknown_tokens
                                else {}
                            ),
                            **fields,
                        },
                        allow_nan=False,
                    )
                    + "\n"
                )
                stream.flush()
                os.fsync(stream.fileno())

    def state(self):
        return {
            "max_total_tokens": self.token_limit,
            "known_tokens": self.spent_tokens,
            "unresolved_reserved_tokens": self.reserved_tokens,
            "upper_bound_charged_tokens": self.spent_tokens
            + self.reserved_tokens
            + self.frozen_unknown_tokens,
            **(
                {"frozen_unknown_tokens": self.frozen_unknown_tokens}
                if self.frozen_unknown_tokens
                else {}
            ),
            "request_context_ceiling": self.context_ceiling,
            "denied_reason": self.denied_reason,
        }

    def deny_before_send(self, reason, *, next_reservation=0):
        from app.core.model_boundary import (
            CURRENT_ATTEMPT,
            BeforeProviderSendDenied,
            fingerprint,
        )

        self.denied_reason, self.blocked = reason, True
        attempt = CURRENT_ATTEMPT.get() if self.protocol == 2 else None
        fields = {
            "reason": reason,
            "next_reservation": next_reservation,
            **(
                {"frozen_unknown_tokens": self.frozen_unknown_tokens}
                if self.frozen_unknown_tokens
                else {}
            ),
        }
        if attempt is not None:
            fields.update(
                protocol=2,
                run_id=self.run_id,
                attempt_identity=attempt.identity,
                attempt_admissions=attempt.admissions,
                sequence=self.sent,
            )
        self.event("denied_before_send", **fields)
        if (
            attempt is not None
            and self.journal is not None
            and not attempt.admissions
            and not self.reserved_tokens
        ):
            event = {
                "event": "denied_before_send",
                "spent_tokens": self.spent_tokens,
                "reserved_tokens": self.reserved_tokens,
                **fields,
            }
            proof = {
                "event": event,
                "event_sha256": fingerprint(event),
                "journal": str(self.journal.resolve()),
            }
            attempt.denial = proof
            raise BeforeProviderSendDenied(proof, attempt.marker)
        raise RuntimeError(
            "evaluation_paid_request_budget_exhausted"
            if reason == "paid_request_limit_or_unknown_usage"
            else "evaluation_token_budget_capacity"
            if reason == "token_budget_capacity"
            else "evaluation_" + reason
        )

    async def before_send(self, request):
        if self.blocked or self.sent >= self.limit:
            self.deny_before_send(self.denied_reason or "paid_request_limit_or_unknown_usage")
        if request.method != "POST" or not request.url.path.endswith("/chat/completions"):
            raise RuntimeError("evaluation_unexpected_provider_route")
        body = json.loads(request.content)
        reservation = 0
        if self.token_limit is not None:
            output_limit = body.get("max_completion_tokens", body.get("max_tokens"))
            if type(output_limit) is not int or output_limit < 1 or body.get("stream"):
                raise RuntimeError(
                    "evaluation_token_reservation_requires_bounded_nonstream_request"
                )
            # A documented full-context ceiling is deliberately conservative. We
            # never pretend a character/token estimator is a hard billing bound.
            reservation = self.context_ceiling + output_limit
            if (
                self.spent_tokens + self.reserved_tokens + self.frozen_unknown_tokens + reservation
                > self.token_limit
            ):
                self.deny_before_send("token_budget_capacity", next_reservation=reservation)
        from app.core.model_boundary import CURRENT_ATTEMPT

        attempt = CURRENT_ATTEMPT.get() if self.protocol == 2 else None
        identity = (
            {"protocol": 2, "run_id": self.run_id, "attempt_identity": attempt.identity}
            if attempt
            else {}
        )
        self.reserved_tokens += reservation
        self.event(
            "reserved_before_send", sequence=self.sent + 1, reservation=reservation, **identity
        )
        if attempt:
            attempt.admissions += 1
        self.traces.append(
            {
                "sequence": self.sent + 1,
                "request": {
                    k: body[k]
                    for k in (
                        "model",
                        "messages",
                        "temperature",
                        "max_tokens",
                        "max_completion_tokens",
                        "response_format",
                        "tools",
                        "tool_choice",
                        "thinking",
                    )
                    if k in body
                },
                "response": None,
                "token_reservation": reservation,
                **identity,
            }
        )
        request.extensions["evaluation_sequence"] = self.sent
        self.sent += 1

    async def after_response(self, response):
        from app.core.model_boundary import CURRENT_ATTEMPT

        attempt = CURRENT_ATTEMPT.get() if self.protocol == 2 else None
        if attempt:
            attempt.provider_usage_valid = False
        # Read/JSON failures also leave usage unresolved; block before parsing.
        self.blocked = True
        entry = self.traces[response.request.extensions["evaluation_sequence"]]
        entry["http_status"] = response.status_code
        # Provider error bodies may echo authentication data; never retain them.
        if response.status_code == 200:
            body = await response.aread()
            if len(body) > 2 * 1024 * 1024:
                raise RuntimeError("evaluation_provider_response_too_large")
            value = json.loads(body)
            entry["response"] = {
                k: value[k] for k in ("id", "model", "choices", "usage") if k in value
            }
            usage = value.get("usage")
            valid = valid_usage(usage)
            valid = valid and (
                self.token_limit is None or usage["total_tokens"] <= entry["token_reservation"]
            )
            if valid:
                if attempt:
                    attempt.provider_usage_valid = True
                self.spent_tokens += usage["total_tokens"]
                self.reserved_tokens -= entry["token_reservation"]
                entry["known_tokens"] = usage["total_tokens"]
                self.event(
                    "known_usage", sequence=entry["sequence"], usage_tokens=usage["total_tokens"]
                )
                self.blocked = False
                return
        self.blocked = True
        self.event("unknown_usage_reservation_retained", sequence=entry["sequence"])


def live_configuration(args):
    if args.mode == "demo":
        if any(
            (
                args.allow_paid,
                args.provider,
                args.endpoint,
                args.model,
                args.api_key_env,
                args.max_paid_requests,
                args.max_total_tokens,
                args.prior_paid_run,
                args.charge_paid_run,
            )
        ):
            raise ValueError("demo不接受付费参数")
        return {"agent_mode": "demo"}
    if not args.allow_paid or not all((args.provider, args.endpoint, args.model, args.api_key_env)):
        raise ValueError("live须显式指定提供方、端点、模型、密钥环境变量及--allow-paid")
    if args.max_paid_requests is None and args.max_total_tokens is None:
        raise ValueError("live须显式指定全轮请求或token上限")
    if args.max_paid_requests is not None and not 1 <= args.max_paid_requests <= 10000:
        raise ValueError("付费请求上限必须在1至10000之间")
    if args.max_total_tokens is not None and not 1 <= args.max_total_tokens <= 100000000:
        raise ValueError("全轮token上限须为1至1亿的整数")
    url = urlsplit(args.endpoint)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("模型端点须为无凭据、查询或fragment的HTTPS URL")
    key = os.environ.get(args.api_key_env, "")
    if not key:
        raise ValueError("指定的密钥环境变量为空；不会扫描.env或其他凭据")
    prices = (args.input_price, args.output_price)
    if any(p is not None and (not math.isfinite(p) or p < 0) for p in prices) or (
        (prices[0] is None) != (prices[1] is None)
    ):
        raise ValueError("价格须同时提供有限非负数，单位为每百万token；未知则都省略")
    return {
        "agent_mode": "live",
        "openai_api_key": key,
        "openai_base_url": args.endpoint,
        "llm_model": args.model,
        "input_price_per_million": args.input_price,
        "output_price_per_million": args.output_price,
    }


def live_model(settings, client):
    # Same SDK settings as app.research.agents.model_from_settings; only add the
    # shared counting HTTP client. A fresh model avoids callback cross-task state.
    extra = (
        {"thinking": {"type": "disabled"}}
        if urlsplit(settings.openai_base_url).hostname == "api.deepseek.com"
        else None
    )
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.openai_api_key.get_secret_value(),
        base_url=settings.openai_base_url,
        temperature=0,
        max_tokens=3000,
        max_retries=0,
        timeout=httpx.Timeout(
            read=settings.model_read_timeout_seconds,
            connect=settings.model_connect_timeout_seconds,
            write=10,
            pool=5,
        ),
        extra_body=extra,
        http_async_client=client,
    )


def cost(metrics, configuration, mode):
    if mode != "live":
        return {"estimated_cost": None, "cost_status": "not_billable_local_protocol"}
    prices = [configuration.get(k) for k in ("input_price_per_million", "output_price_per_million")]
    complete = metrics.get("usage_complete") is True and all(p is not None for p in prices)
    amount = (
        (metrics["input_tokens"] * prices[0] + metrics["output_tokens"] * prices[1]) / 1_000_000
        if complete
        else None
    )
    return {
        "estimated_cost": amount,
        "cost_status": "estimate_not_invoice_cache_pricing_unknown"
        if complete
        else "unknown_usage_or_price",
    }


def rate(rows, field="automatic_passed"):
    return sum(r[field] is True for r in rows) / len(rows) if rows else None


def summarize(rows, planned, *, stopped=None):
    groups = {}
    for group in sorted({r["group"] for r in rows}):
        selected = [r for r in rows if r["group"] == group]
        latencies = sorted(r["elapsed_seconds"] for r in selected)
        cases = {}
        for row in selected:
            cases.setdefault(row["case_id"], []).append(row)
        groups[group] = {
            "runs": len(selected),
            "independent_cases": len(cases),
            "automatic_contract_rate": rate(selected),
            "positive_automatic_completion_rate": rate([r for r in selected if r["positive"]]),
            "critical_automatic_rate": rate([r for r in selected if r["critical"]]),
            "all_planned_repeats_correct_case_rate": sum(
                len(v) == planned["repeats"] and all(r["automatic_passed"] for r in v)
                for v in cases.values()
            )
            / len(cases),
            "p95_seconds_nearest_rank": latencies[math.ceil(len(latencies) * 0.95) - 1],
            "tool_calls": sum(r["metrics"].get("tool_calls", 0) for r in selected),
            "model_calls": sum(r["metrics"].get("model_calls", 0) for r in selected),
            "input_tokens": sum(r["metrics"].get("input_tokens", 0) for r in selected),
            "output_tokens": sum(r["metrics"].get("output_tokens", 0) for r in selected),
            "known_usage_rate": sum(r["metrics"].get("usage_complete") is True for r in selected)
            / len(selected),
            "known_cost_rate": sum(r["estimated_cost"] is not None for r in selected)
            / len(selected),
            "total_estimated_cost": sum(r["estimated_cost"] for r in selected)
            if all(r["estimated_cost"] is not None for r in selected)
            else None,
            "facets": {
                facet: {
                    "runs": len(subset := [r for r in selected if r["facet"] == facet]),
                    "automatic_contract_rate": rate(subset),
                }
                for facet in sorted({r["facet"] for r in selected})
            },
        }
    paired = []
    for case_id, repeat in sorted(
        {
            (r["case_id"], r["repeat"])
            for r in rows
            if r["capability"] == "common" and r["execution"] == "diagnostic"
        }
    ):
        pair = {r["group"]: r for r in rows if r["case_id"] == case_id and r["repeat"] == repeat}
        if {"fixed", "agent"} <= pair.keys():
            paired.append(
                {
                    "case_id": case_id,
                    "repeat": repeat,
                    "same_bundle": pair["fixed"]["bundle_hash"] == pair["agent"]["bundle_hash"],
                    "same_budget": pair["fixed"]["budget"] == pair["agent"]["budget"],
                    "same_model_configuration": pair["fixed"].get("model_configuration_hash")
                    == pair["agent"].get("model_configuration_hash"),
                    "same_final_scope": pair["fixed"].get("final_scope")
                    == pair["agent"].get("final_scope"),
                    "fixed_automatic_passed": pair["fixed"]["automatic_passed"],
                    "agent_automatic_passed": pair["agent"]["automatic_passed"],
                }
            )
    return {
        "planned": planned,
        "executed_runs": len(rows),
        "stopped": stopped,
        "groups": groups,
        "common_paired_contracts": paired,
        "all_automatic_passed": bool(rows) and all(r["automatic_passed"] for r in rows),
        "business_quality_rate": None,
        "independent_review_status": "PENDING",
        "release_eligible": False,
        "default_entrypoint": "commerce_review",
        "scope": "Automatic contracts/raw-record facts only. Gates are separate from model quality. Repeats are not independent cases; no Agent benefit is inferred.",
    }


async def evaluate(args):
    if (
        (getattr(args, "final_holdout", False) or getattr(args, "split", None) == "holdout")
        and getattr(args, "mode", None) == "live"
        and getattr(args, "frozen_plan", None) is None
    ):
        raise ValueError("保留集正式模型测试必须提供冻结计划，禁止提前读取凭据或发送HTTP")
    if getattr(args, "frozen_plan", None) is not None:
        from evaluations.diagnosis_plan import guard_run

        guard_run(args)
    if getattr(args, "prior_reconciliation", None) is not None and not args.charge_paid_run:
        raise ValueError("更正证书仅允许显式扣费承接，不允许拼接质量成绩")
    configuration = live_configuration(args)
    accounting_only = bool(args.charge_paid_run)
    prior_directory = args.charge_paid_run or args.prior_paid_run
    if prior_directory and args.max_total_tokens is None:
        raise ValueError("承接历史扣费须显式指定相同全轮token上限")
    prior_limit = args.max_total_tokens
    if accounting_only:
        prior_limit = json.loads((prior_directory / "run.json").read_bytes()).get(
            "max_total_tokens"
        )
        if type(prior_limit) is not int or args.max_total_tokens < prior_limit:
            raise ValueError("扣费承接须保留原上限，增加累计额度须显式给出新上限")
    previous = prior_spend(
        prior_directory,
        provider=args.provider,
        endpoint=args.endpoint,
        model=args.model,
        token_limit=prior_limit,
        require_stopped=not accounting_only,
        reconciliation=getattr(args, "prior_reconciliation", None),
    )
    cases, case_hash = load_cases(args.cases, split=args.split, case_ids=args.case_ids)
    if not 1 <= args.repeats <= 10:
        raise ValueError("重复次数须在1至10之间")
    if args.split == "holdout" and not (
        args.final_holdout and args.mode == "live" and args.repeats == 3 and len(cases) == 20
    ):
        raise ValueError("保留集仅允许显式最终live评估：全部20例、各3次、--final-holdout")
    for case in cases:
        if set(case.expected.required_assertions) - ASSERTIONS or case.harness.mode not in MODES:
            raise ValueError("存在未实现的断言或harness，拒绝执行")
    context_ceiling = None
    if args.max_total_tokens is not None:
        if urlsplit(args.endpoint).hostname != "api.deepseek.com" or args.model != "deepseek-flash":
            raise ValueError(
                "token硬预算仅支持已核验上下文上限的deepseek-flash；其他模型须先登记官方上限"
            )
        context_ceiling = 1048576
    request_limit = args.max_paid_requests or (
        12
        * args.repeats
        * sum(
            (2 if c.expected.baseline_capability == "common" else 1)
            for c in cases
            if c.execution == "diagnostic"
        )
        if args.mode == "live"
        else 0
    )
    if accounting_only and args.max_paid_requests is None:
        request_limit += previous["provider_requests"]
    manifest, fingerprint = implementation_manifest()
    planned = {
        "case_ids": [c.case_id for c in cases],
        "split": args.split,
        "repeats": args.repeats,
        "mode": args.mode,
    }
    signatures = {
        "cases_file_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        "selected_cases_sha256": case_hash,
        "expected_sha256": sha([c.expected.model_dump(mode="json") for c in cases]),
        "targets_sha256": hashlib.sha256(
            (ROOT / "evaluations/diagnosis_targets_v1.json").read_bytes()
        ).hexdigest(),
    }
    groups_by_case = {
        c.case_id: (
            ["gate"]
            if c.execution != "diagnostic"
            else ["fixed", "agent"]
            if c.expected.baseline_capability == "common"
            else ["additional"]
        )
        for c in cases
    }

    semantic_file = args.cases.with_name("semantic-contract.json")
    semantic_contract = json.loads(semantic_file.read_bytes()) if semantic_file.exists() else None

    def safety_regrade(row, record):
        case = next(c for c in cases if c.case_id == row["case_id"])
        directory = Path(previous["prior_run"]["directory"]) / Path(row["receipt"]).parent
        bundle = json.loads((directory / "bundle.json").read_text())
        return grade(case, bundle, record, semantic_contract=semantic_contract)

    inherited_cases = (
        set()
        if accounting_only
        else resume_cases(
            previous, planned, signatures, manifest, groups_by_case, safety_regrade=safety_regrade
        )
    )
    previous_public = {k: v for k, v in previous.items() if not k.startswith("_")}
    output = args.output.resolve()
    if previous["prior_run"]:
        prior_root = Path(previous["prior_run"]["directory"])
        if output.is_relative_to(prior_root) or prior_root.is_relative_to(output):
            raise ValueError("续评输出与原回执须为独立目录")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    run_id = str(uuid4())
    claim_prior_budget(previous, output, run_id)
    # Preserve the exact input file for reproducible audits of new case packs,
    # rather than implicitly substituting the repository's default corpus.
    with (output / "cases-source.json").open("xb") as stream:
        (output / "cases-source.json").chmod(0o600)
        stream.write(args.cases.read_bytes())
    write(
        output / "expectations-manifest.json",
        [{"case_id": c.case_id, "expected": c.expected.model_dump(mode="json")} for c in cases],
    )
    write(
        output / "run.json",
        {
            "run_id": run_id,
            "accounting_protocol": 2,
            "created_at": datetime.now(UTC).isoformat(),
            "planned": planned,
            "implementation_sha256": fingerprint,
            "source_manifest": manifest,
            **signatures,
            "git_head": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "provider": args.provider,
            "prior_paid_budget": previous_public,
            "budget_carry_mode": "accounting_only" if accounting_only else "resume",
            "budget_limit_change": {
                "previous": prior_limit,
                "current": args.max_total_tokens,
                "source": "explicit_cli_total_limit",
            }
            if accounting_only and prior_limit != args.max_total_tokens
            else None,
            "inherited_attempts": [{"case_id": c, "repeat": i} for c, i in sorted(inherited_cases)],
            "inherited_receipt_scope": "Prior charges only; no old Tasks, grades or results count toward this implementation."
            if accounting_only
            else "All earlier repetitions including FAILs retained byte-for-byte; original implementation and label caveat remain bound to prior_run.",
            "stop_policy": "critical_output_invariants_or_unknown_usage_v3",
            "endpoint": args.endpoint,
            "model": args.model,
            "paid_request_limit": request_limit,
            "request_limit_source": "explicit"
            if args.max_paid_requests is not None
            else "derived_task_hard_limits",
            "max_total_tokens": args.max_total_tokens,
            "token_reservation_policy": "documented_full_context_plus_requested_max_output_v1"
            if context_ceiling
            else None,
            "context_ceiling_tokens": context_ceiling,
            "context_ceiling_source": "https://api-docs.deepseek.com/quick_start/pricing/"
            if context_ceiling
            else None,
            "prices_per_million_tokens": {"input": args.input_price, "output": args.output_price},
            "database": "fresh_isolated_sqlite_per_case_repeat",
            "task_cache": "fresh_per_task",
            "provider_cache": "unknown",
            "resource_limits": {
                "max_steps": 8,
                "max_tool_calls": 10,
                "max_model_calls": 12,
                "max_tokens": 60000,
                "max_runtime_seconds": 180,
            },
        },
    )
    rows, stopped = ([] if accounting_only else list(previous["_rows"])), None
    if inherited_cases:
        for name in {str(Path(row["receipt"]).parent) for row in rows}:
            source = prior_root / name
            if any(p.is_symlink() for p in source.rglob("*")):
                raise ValueError("续评不复制符号链接")
            shutil.copytree(source, output / name)
        for row in rows:
            with (output / "progress.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    budget = RequestBudget(
        request_limit,
        protocol=2,
        run_id=run_id,
        max_total_tokens=args.max_total_tokens,
        context_ceiling=context_ceiling,
        journal=output / "budget-journal.jsonl",
        initial_spent_tokens=previous["known_tokens"],
    )
    budget.sent = previous["provider_requests"]
    budget.traces = list(previous["_traces"])
    if previous["prior_run"]:
        budget.event(
            "carried_prior_usage",
            provider_requests=budget.sent,
            prior_run_id=previous["prior_run"]["run_id"],
        )
    async with httpx.AsyncClient(
        event_hooks={"request": [budget.before_send], "response": [budget.after_response]},
        follow_redirects=False,
        trust_env=False,
    ) as client:
        for case in cases:
            if stopped:
                break
            for repeat in range(1, args.repeats + 1):
                if (case.case_id, repeat) in inherited_cases:
                    continue
                directory = output / f"{case.case_id}-{repeat}"
                directory.mkdir()
                write(directory / "business-input.json", business_input(case))
                write(directory / "expectations.json", case.expected.model_dump(mode="json"))
                is_gate = case.execution != "diagnostic"
                conf = {"agent_mode": "demo"} if is_gate else configuration
                with sandbox(**conf) as (settings, repo, user, other):
                    conversation, bundle, rejection = admit(repo, user, other, case)
                    if bundle:
                        write(directory / "bundle.json", bundle)
                    groups = groups_by_case[case.case_id]
                    for group in groups:
                        if stopped:
                            break
                        started, requests_before = time.monotonic(), budget.sent
                        task = None
                        try:
                            if rejection.get("preparation_error"):
                                raise RuntimeError("evaluation_fixture_preparation_failed")
                            if case.execution == "admission":
                                record = rejection
                            else:
                                task = new_task(
                                    repo,
                                    user,
                                    conversation,
                                    bundle,
                                    case.query,
                                    group,
                                    require_review=case.harness.mode == "stale_approval",
                                    sources=case.sources,
                                )
                                if case.execution == "fault":
                                    proof = await run_harness(repo, settings, user, task, case)
                                else:
                                    model = (
                                        live_model(settings, client)
                                        if args.mode == "live"
                                        else None
                                    )
                                    await execute(repo, settings, model=model)
                                    if case.harness.mode == "clarify":
                                        await answer_clarification(
                                            repo,
                                            settings,
                                            user,
                                            task["id"],
                                            case.harness.clarification_choice,
                                            model=live_model(settings, client)
                                            if args.mode == "live"
                                            else None,
                                        )
                                record = collect(repo, task["id"], group)
                                if case.execution == "fault":
                                    record["harness_proof"] = proof
                            grading = grade(
                                case, bundle, record, semantic_contract=semantic_contract
                            )
                        except Exception as exc:
                            # Error types only: SDK exceptions can contain secrets or provider text.
                            record = (
                                collect(repo, task["id"], group)
                                if task
                                else {"group": group, "task": None}
                            )
                            grading = {
                                "automatic_passed": False,
                                "assertions": {"evaluation_completed": False},
                                "errors": ["evaluation_error:" + type(exc).__name__],
                                "semantic_review": "PENDING",
                            }
                            if rejection.get("preparation_error"):
                                grading["errors"].append(
                                    "fixture_preparation:" + rejection["preparation_error"]
                                )
                        record["grading"] = grading
                        record["provider_trace"] = budget.traces[requests_before:]
                        metrics = (record.get("task") or {}).get("metrics", {})
                        row = {
                            "case_id": case.case_id,
                            "repeat": repeat,
                            "group": group,
                            "facet": case.facet,
                            "execution": case.execution,
                            "capability": case.expected.baseline_capability,
                            "positive": case.expected.positive,
                            "critical": case.expected.critical,
                            "bundle_hash": bundle["bundle_hash"] if bundle else None,
                            "budget": (record.get("task") or {})
                            .get("parameters", {})
                            .get("budget"),
                            "execution_mode": "local_fault_protocol" if is_gate else args.mode,
                            "model_configuration_hash": sha(
                                {
                                    "provider": args.provider,
                                    "endpoint": args.endpoint,
                                    "model": args.model,
                                    "temperature": 0,
                                    "max_tokens": 3000,
                                }
                            )
                            if not is_gate
                            else None,
                            "initial_scope": bundle["allowed_skus"] if bundle else None,
                            "final_scope": (
                                bundle["allowed_skus"]
                                if group == "fixed"
                                else ((record.get("task") or {}).get("report") or {})
                                .get("scope", {})
                                .get("skus")
                            )
                            if bundle
                            else None,
                            "automatic_passed": grading["automatic_passed"],
                            "metrics": metrics,
                            "elapsed_seconds": round(time.monotonic() - started, 4),
                            "provider_requests": budget.sent - requests_before,
                            "cumulative_token_budget": budget.state(),
                            **cost(metrics, conf, "demo" if is_gate else args.mode),
                            "receipt": f"{case.case_id}-{repeat}/{group}.json",
                        }
                        record["measurement"] = row
                        write(directory / f"{group}.json", record)
                        if task:
                            export_artifacts(repo, task["id"], directory / f"{group}-artifacts")
                        rows.append(row)
                        with (output / "progress.jsonl").open("a", encoding="utf-8") as stream:
                            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                        print(
                            json.dumps(
                                {
                                    "case_id": case.case_id,
                                    "repeat": repeat,
                                    "group": group,
                                    "automatic_passed": row["automatic_passed"],
                                    "provider_requests": row["provider_requests"],
                                }
                            ),
                            flush=True,
                        )
                        if args.mode == "live" and not is_gate:
                            if budget.denied_reason == "token_budget_capacity":
                                stopped = "token_budget_capacity_stop_before_next_http_send"
                                budget.blocked = True
                            elif metrics.get("usage_complete") is not True:
                                stopped = "unknown_usage_stop_all_paid_requests"
                                budget.blocked = True
                            elif critical_failure(grading, critical_case=case.expected.critical):
                                stopped = "critical_contract_failure_stop_release_run"
                                budget.blocked = True
                            elif budget.sent >= budget.limit:
                                stopped = "paid_request_limit_reached"
                                budget.blocked = True
                if stopped:
                    break
    summary = summarize(rows, planned, stopped=stopped)
    summary.update(
        run_id=run_id,
        implementation_sha256=fingerprint,
        provider_requests=budget.sent,
        provider_requests_this_run=budget.sent - previous["provider_requests"],
        token_budget=budget.state(),
        prior_paid_budget=previous_public,
    )
    write(output / "summary.json", summary)
    write(output / "review-template.json", review_template(output, rows))
    return summary


def review_template(output, rows):
    from evaluations.diagnosis_review import CRITERIA, conclusion_criteria

    return {
        "reviewer": "",
        "reviewer_kind": "human",
        "independent_of_task_model": True,
        "records": [
            {
                "receipt": row["receipt"],
                "receipt_sha256": hashlib.sha256(
                    (output / row["receipt"]).read_bytes()
                ).hexdigest(),
                "verdict": "PENDING",
                "checks": {
                    k: {"passed": None, "reason": "", "evidence_refs": []}
                    for k in sorted(
                        CRITERIA
                        | conclusion_criteria(
                            json.loads(
                                (output / row["receipt"])
                                .parent.joinpath("expectations.json")
                                .read_text()
                            )
                        )
                    )
                },
            }
            for row in rows
        ],
    }


def parser():
    root = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = root.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", allow_abbrev=False)
    run.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    run.add_argument("--split", choices=("development", "regression", "holdout"), required=True)
    run.add_argument("--case-id", action="append", dest="case_ids")
    run.add_argument("--repeats", type=int, default=1)
    run.add_argument("--mode", choices=("demo", "live"), default="demo")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--final-holdout", action="store_true")
    run.add_argument("--frozen-plan", type=Path, help="发送请求前校验已冻结题包、源码与预算链")
    run.add_argument("--allow-paid", action="store_true")
    run.add_argument("--provider")
    run.add_argument("--endpoint")
    run.add_argument("--model")
    run.add_argument("--api-key-env")
    run.add_argument("--max-paid-requests", type=int)
    run.add_argument("--max-total-tokens", type=int)
    prior = run.add_mutually_exclusive_group()
    prior.add_argument("--resume-from", dest="prior_paid_run", type=Path)
    prior.add_argument(
        "--charge-from",
        dest="charge_paid_run",
        type=Path,
        help="仅承接历史扣费；新实现/新计划不继承旧Task与质量结果",
    )
    run.add_argument(
        "--prior-reconciliation", type=Path, help="显式核验追加式未发送更正证书，原始回执不改"
    )
    run.add_argument("--input-price", type=float)
    run.add_argument("--output-price", type=float)
    review = commands.add_parser("review", allow_abbrev=False)
    review.add_argument("--run", type=Path, required=True)
    review.add_argument("--reviews", type=Path, required=True)
    review.add_argument("--grading-audit", type=Path)
    review.add_argument("--output", type=Path, required=True)
    audit = commands.add_parser("audit", allow_abbrev=False)
    audit.add_argument("--run", type=Path, required=True)
    audit.add_argument("--output", type=Path, required=True)
    audit.add_argument(
        "--reconciliation", type=Path, help="显式追加式更正证书；缺省仍严格阻断UNKNOWN"
    )
    return root


def main():
    args = parser().parse_args()
    try:
        if args.command == "review":
            from evaluations.diagnosis_review import review_run

            result = review_run(args.run, args.reviews, args.grading_audit)
            write(args.output, result)
            success = result["review_completed"]
        elif args.command == "audit":
            from evaluations.diagnosis_audit import audit_run

            result = audit_run(args.run, reconciliation=args.reconciliation)
            write(args.output, result)
            success = True
        else:
            result = asyncio.run(evaluate(args))
            success = result["all_automatic_passed"] and not result["stopped"]
        print(
            json.dumps(
                {k: result[k] for k in ("run_id", "release_eligible") if k in result},
                ensure_ascii=False,
            )
        )
    except ValidationError:
        raise SystemExit("评测输入结构校验失败；未输出原始输入") from None
    except (ValueError, FileExistsError) as exc:
        # These are our fixed validation messages, never SDK messages.
        raise SystemExit(str(exc)) from None
    raise SystemExit(0 if success else 1)


if __name__ == "__main__":
    main()
