"""Shared one-Task execution. Callers must finish corpus/plan/review admission first."""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx

from evaluations.diagnosis import RequestBudget, implementation_manifest, write
from evaluations.diagnosis_budget import claim_prior_budget
from evaluations.diagnosis_cases import business_input
from evaluations.diagnosis_grading import grade
from evaluations.diagnosis_probe_accounting import HOLD, LIMIT, file_hash
from evaluations.diagnosis_runtime import ROOT, sandbox


async def execute_one(
    args,
    parent,
    case,
    files,
    fingerprint,
    budget_plan,
    hooks,
    *,
    extra_meta=None,
    sandbox_overrides=None,
    semantic_contract=None,
    reuse_inputs=None,
):
    from evaluations.diagnosis_probe import audit_probe, failure_reason

    if set(extra_meta or {}) - {
        "scope",
        "kind",
        "formal_plan_sha256",
        "formal_plan_directory",
        "slot_id",
        "slot_index",
    }:
        raise ValueError("unsupported_one_task_metadata")
    output = args.output.resolve()
    original = Path(parent["directory"])
    if output.is_relative_to(original) or original.is_relative_to(output):
        raise ValueError("probe_output_must_be_separate")
    conf = hooks.live_configuration(
        SimpleNamespace(
            mode="live",
            allow_paid=args.allow_paid,
            provider="DeepSeek",
            endpoint="https://api.deepseek.com",
            model="deepseek-flash",
            api_key_env=args.api_key_env,
            max_paid_requests=args.max_http,
            max_total_tokens=LIMIT,
            prior_paid_run=None,
            charge_paid_run=None,
            input_price=None,
            output_price=None,
        )
    )
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    run_id = str(uuid4())
    claim_prior_budget(
        {
            "prior_run": {"directory": parent["directory"], "run_id": parent["run_id"]},
            "known_tokens": parent["known_tokens"],
        },
        output,
        run_id,
    )
    write(
        output / "probe.json",
        {
            "schema_version": 1,
            "run_id": run_id,
            "parent": str(args.parent.resolve()),
            "parent_accounting": parent,
            "implementation_sha256": fingerprint,
            "source_manifest": files,
            "case_id": args.case_id,
            "group": args.group,
            "max_http": args.max_http,
            "http_budget_preflight": budget_plan,
            "max_total_tokens": LIMIT,
            "stop_after_model_failure": True,
            "cases_file": str(args.cases.resolve()),
            "cases_sha256": file_hash(args.cases),
            "legacy_root": str(args.legacy_root.resolve()),
            "scope": "USED_INPUT_PROBE_NOT_FORMAL_ACCEPTANCE",
            **(extra_meta or {}),
        },
    )
    for path in files:
        dst = output / "source-snapshot" / path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, dst)
    write(output / "business-input.json", business_input(case))
    budget = RequestBudget(
        args.max_http,
        protocol=2,
        run_id=run_id,
        max_total_tokens=LIMIT,
        context_ceiling=1048576,
        journal=output / "budget-journal.jsonl",
        initial_spent_tokens=parent["known_tokens"],
        frozen_unknown_tokens=HOLD,
    )
    budget.journal.touch(mode=0o600, exist_ok=False)
    with sandbox(
        workspace=output / "recovery-workspace",
        fork_from=reuse_inputs["workspace"] if reuse_inputs else None,
        **conf,
        stop_after_model_failure=True,
        max_model_calls=args.max_http,
        model_decision_reserve=0,
        **(sandbox_overrides or {}),
    ) as (settings, repo, user, other):
        if reuse_inputs:
            from app.diagnosis.inputs import bundle_info, owned_bundle

            if user.id != reuse_inputs["user_id"]:
                raise ValueError("paired_input_owner_changed")
            with repo.session() as db:
                item, _ = owned_bundle(db, reuse_inputs["bundle_id"], user.id, user.tenant_id)
                bundle = bundle_info(item)
            conv = reuse_inputs["conversation_id"]
            if bundle["bundle_hash"] != reuse_inputs["bundle_hash"]:
                raise ValueError("paired_input_bundle_changed")
        else:
            conv, bundle, rejection = hooks.admit(repo, user, other, case)
            if rejection.get("preparation_error") or not bundle:
                raise ValueError("probe_input_admission_failed")
        write(output / "bundle.json", bundle)
        task = hooks.new_task(repo, user, conv, bundle, case.query, args.group)

        async def before_send(request):
            if implementation_manifest()[1] != fingerprint:
                budget.deny_before_send("paid_request_limit_or_unknown_usage")
            await budget.before_send(request)
            write(output / f"http-request-{budget.sent}.json", budget.traces[-1])

        async def after_response(response):
            await budget.after_response(response)
            write(output / f"http-response-{budget.sent}.json", budget.traces[-1])

        execution_error = None
        try:
            async with httpx.AsyncClient(
                event_hooks={"request": [before_send], "response": [after_response]},
                follow_redirects=False,
                trust_env=False,
            ) as client:
                await hooks.execute(repo, settings, model=hooks.live_model(settings, client))
        except BaseException as error:
            execution_error = type(error).__name__
            write(
                output / "execution-error.json",
                {"exception_class": execution_error, "automatic_retry": False},
            )
        record = hooks.collect(repo, task["id"], args.group)
        grading = grade(case, bundle, record, semantic_contract=semantic_contract)
        record.update(grading=grading, provider_trace=budget.traces)
        failure = "unknown_usage" if budget.reserved_tokens else failure_reason(record, grading)
        if execution_error:
            failure = failure or "execution_exception"
        write(output / "receipt.json", record)
        hooks.export_artifacts(repo, task["id"], output / "artifacts")
    write(
        output / "summary.json",
        {
            "run_id": run_id,
            "failure_reason": failure,
            "status": "FAIL_STOPPED" if failure else "AUTOMATIC_PASS_REVIEW_PENDING",
            "receipt_sha256": file_hash(output / "receipt.json"),
            "budget": budget.state(),
            "confirmed_http_requests": parent["confirmed_http_requests"] + budget.sent,
            "new_http_requests": budget.sent,
            "new_known_tokens": budget.spent_tokens - parent["known_tokens"],
            "old_unknown_preserved": True,
            "real_model_formal_acceptance": "UNTESTED",
            "release_eligible": False,
        },
    )
    if record["task"]["metrics"]["usage_complete"] and not budget.reserved_tokens:
        audit_probe(output)
    print(json.dumps(json.loads((output / "summary.json").read_bytes()), ensure_ascii=False))
    return failure
