"""Exactly one fresh formal Task per authorized launch; no bulk run or automatic retry."""

import argparse
import asyncio
import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from evaluations import diagnosis_formal_plan as planning
from evaluations.diagnosis import implementation_manifest, live_configuration, live_model
from evaluations.diagnosis_cases import business_input, load_cases
from evaluations.diagnosis_grading import grade
from evaluations.diagnosis_one import execute_one
from evaluations.diagnosis_packet_checks import require
from evaluations.diagnosis_probe import (
    MAX_PROBE_ANCESTRY,
    failure_reason,
    load_parent,
    validate_probe_http_budget,
)
from evaluations.diagnosis_probe_accounting import HOLD, LIMIT, file_hash
from evaluations.diagnosis_runtime import ROOT, admit, collect, execute, export_artifacts, new_task


def review(root, receipt_sha256):
    path = Path(root) / "review.json"
    require(path.is_file(), "formal_review_pending_no_next_slot")
    value = planning.read(path)
    require(value.get("receipt_sha256") == receipt_sha256, "formal_review_not_bound_to_receipt")
    require(
        value.get("verdict") == "PASS"
        and bool(value.get("reviewer"))
        and bool(value.get("checks")),
        "formal_manual_review_failed_or_pending",
    )
    return value


def progress(directory, parent_path):
    directory = Path(directory).resolve()
    plan = planning.check(directory)
    seal = file_hash(directory / "manifest.json")
    anchor = Path(plan["parent_anchor"]).resolve()
    current = Path(parent_path).resolve()
    load_parent(parent_path)  # Audit the complete charge/source ancestry exactly once.
    seen = set()
    rows = []
    cases = {c.case_id: c for c in load_cases(directory / "cases.json")[0]}
    semantic = planning.read(directory / "semantic-contract.json")
    while current != anchor:
        require(
            current not in seen and len(seen) < MAX_PROBE_ANCESTRY,
            "formal_slot_ancestry_cycle_or_depth",
        )
        seen.add(current)
        meta = planning.read(current / "probe.json")
        require(
            meta.get("kind") == "FORMAL_SLOT"
            and meta.get("formal_plan_sha256") == seal
            and Path(meta.get("formal_plan_directory", "")).resolve() == directory,
            "formal_parent_not_part_of_this_frozen_round",
        )
        require(
            meta["implementation_sha256"] == plan["implementation_sha256"],
            "formal_cannot_splice_source_quality",
        )
        slot_index = meta.get("slot_index")
        require(
            type(slot_index) is int and 0 <= slot_index < len(plan["slots"]),
            "formal_invalid_slot_index",
        )
        slot = plan["slots"][slot_index]
        require(
            (meta["slot_id"], meta["case_id"], meta["group"], meta["max_http"])
            == (slot["slot_id"], slot["case_id"], slot["group"], slot["max_http"]),
            "formal_slot_metadata_mismatch",
        )
        summary = planning.read(current / "summary.json")
        require(
            summary["failure_reason"] is None, "formal_failed_round_stop_repair_refreeze_required"
        )
        record = planning.read(current / "receipt.json")
        require(
            planning.read(current / "business-input.json")
            == business_input(cases[slot["case_id"]]),
            "formal_executed_business_input_changed",
        )
        parameters = record["task"]["parameters"]
        budget = parameters["budget"]
        require(
            parameters.get("answer_boundary_version") == 2
            and parameters.get("accounting_protocol") == 2
            and budget.get("max_model_calls") == slot["max_http"]
            and budget.get("decision_call_reserve") == 0
            and budget.get("stop_after_model_failure") is True,
            "formal_task_policy_not_frozen",
        )
        bundle = planning.read(current / "bundle.json")
        recomputed = grade(cases[slot["case_id"]], bundle, record, semantic_contract=semantic)
        require(
            recomputed == record["grading"] and recomputed["automatic_passed"] is True,
            "formal_any_automatic_failure_stops",
        )
        require(
            failure_reason(record, recomputed) is None,
            "formal_execution_or_validation_failure_stops",
        )
        review(current, file_hash(current / "receipt.json"))
        prior = Path(meta["parent"]).resolve()
        claim = planning.read(prior.with_name(prior.name + ".budget-continuation.json"))
        require(
            Path(claim["next_directory"]).resolve() == current
            and claim["next_run_id"] == meta["run_id"]
            and claim["prior_run_id"] == meta["parent_accounting"]["run_id"]
            and claim["carried_tokens"] == meta["parent_accounting"]["known_tokens"],
            "formal_parent_claim_not_bound_to_child",
        )
        rows.append(
            {
                "slot_index": slot_index,
                "slot_id": slot["slot_id"],
                "directory": str(current),
                "receipt_sha256": file_hash(current / "receipt.json"),
                "automatic_passed": True,
                "manual_passed": True,
            }
        )
        current = prior
    rows.reverse()
    require(
        [r["slot_index"] for r in rows] == list(range(len(rows))),
        "formal_slots_skipped_reordered_or_duplicated",
    )
    for index, row in enumerate(rows):
        slot = plan["slots"][index]
        if slot["group"] == "agent":
            fixed = planning.read(Path(rows[index - 1]["directory"]) / "receipt.json")["task"]
            agent = planning.read(Path(row["directory"]) / "receipt.json")["task"]
            require(
                fixed["bundle_id"] == agent["bundle_id"]
                and fixed["parameters"]["bundle_hash"] == agent["parameters"]["bundle_hash"],
                "formal_fixed_agent_pair_input_mismatch",
            )
    return plan, rows


def authorization(path, directory, plan):
    path = Path(path)
    require(
        path.is_file() and path.stat().st_size <= 65536, "formal_explicit_authorization_required"
    )
    a = planning.read(path)
    require(
        a.get("granted") is True
        and a.get("kind") == "FORMAL_SINGLE_TASK_AUTHORIZATION"
        and a.get("authorized_by") == "HUMAN_USER_EXPLICIT_INSTRUCTION",
        "formal_explicit_authorization_required",
    )
    expected = {
        "plan_sha256": file_hash(Path(directory) / "manifest.json"),
        "cases_sha256": plan["packet_files_sha256"]["cases.json"],
        "implementation_sha256": plan["implementation_sha256"],
        "model": plan["model"],
        "cumulative_token_limit": LIMIT,
        "parent_anchor_receipt_sha256": plan["parent_accounting"]["parent_receipt_sha256"],
        "legacy_unknown_hold_tokens": plan["parent_accounting"]["frozen_unknown_tokens"],
    }
    require(
        all(a.get(k) == v for k, v in expected.items()),
        "formal_authorization_not_bound_to_plan_model_or_limit",
    )
    require(
        a.get("new_packet_transmission_granted") is True
        and a.get("formal_conservative_carry_granted") is True,
        "formal_probe_authorization_does_not_authorize_fresh_formal_carry",
    )
    return a


def paired_inputs(parent, case):
    """Read-only consistency proof before loading keys or forking a paired DB."""
    from app.commerce.datasets import canonical
    from app.diagnosis.inputs import dataset_digest, digest

    root = Path(parent).resolve()
    record = planning.read(root / "receipt.json")["task"]
    workspace = (root / "recovery-workspace").resolve()
    require(
        workspace.is_relative_to(root) and (workspace / "app.sqlite").is_file(),
        "formal_paired_workspace_missing",
    )
    try:
        with sqlite3.connect((workspace / "app.sqlite").as_uri() + "?mode=ro", uri=True) as db:
            task = db.execute(
                "SELECT user_id,tenant_id,conversation_id,bundle_id,parameters,status FROM tasks WHERE id=?",
                (record["id"],),
            ).fetchone()
            require(
                task is not None
                and task[2] == record["conversation_id"]
                and task[3] == record["bundle_id"]
                and json.loads(task[4]) == record["parameters"]
                and task[5] == record["status"],
                "formal_paired_task_snapshot_changed",
            )
            bundle = db.execute(
                "SELECT payload,bundle_hash,user_id,tenant_id FROM analysis_input_bundles WHERE id=?",
                (task[3],),
            ).fetchone()
            require(
                bundle is not None and bundle[2:] == task[:2], "formal_paired_bundle_owner_changed"
            )
            payload = json.loads(bundle[0])
            require(
                digest(payload) == bundle[1] == record["parameters"]["bundle_hash"],
                "formal_paired_bundle_hash_changed",
            )
            data = db.execute(
                "SELECT payload,content_hash FROM commerce_datasets WHERE id=?",
                (payload["sales_dataset_id"],),
            ).fetchone()
            require(
                data is not None
                and json.loads(data[0]) == canonical(case.sales)
                and dataset_digest(json.loads(data[0])) == data[1] == payload["sales_content_hash"],
                "formal_paired_source_changed",
            )
    except sqlite3.DatabaseError as error:
        raise ValueError("formal_paired_workspace_invalid") from error
    return {
        "workspace": workspace,
        "user_id": task[0],
        "conversation_id": task[2],
        "bundle_id": task[3],
        "bundle_hash": bundle[1],
    }


def validate_entry(args):
    output = Path(args.output).resolve()
    require(not output.exists(), "formal_output_already_exists")
    for protected in (Path(args.parent).resolve(), Path(args.plan).resolve()):
        require(
            not output.is_relative_to(protected) and not protected.is_relative_to(output),
            "formal_output_must_be_separate",
        )
    require(
        args.allow_paid is True and args.max_total_tokens == LIMIT,
        "formal_paid_flag_and_unchanged_limit_required",
    )
    plan, rows = progress(args.plan, args.parent)
    authorization(args.authorization, args.plan, plan)
    require(len(rows) < len(plan["slots"]), "formal_round_already_complete")
    slot = plan["slots"][len(rows)]
    require(args.slot_id == slot["slot_id"], "formal_only_next_unexecuted_slot_allowed")
    parent = load_parent(args.parent)
    if rows:
        review(args.parent, parent["parent_receipt_sha256"])
    require(
        not Path(args.parent)
        .with_name(Path(args.parent).name + ".budget-continuation.json")
        .exists(),
        "formal_parent_already_claimed",
    )
    require(
        parent["frozen_unknown_tokens"] == HOLD
        and parent["known_tokens"] + parent["frozen_unknown_tokens"] + HOLD <= LIMIT,
        "formal_budget_insufficient_or_hold_changed",
    )
    case = load_cases(Path(args.plan) / "cases.json", case_ids=[slot["case_id"]])[0][0]
    if slot["group"] == "agent":
        paired_inputs(args.parent, case)
    bound = validate_probe_http_budget(
        case, slot["group"], slot["max_http"], answer_boundary_version=2
    )
    return plan, parent, case, slot, bound


async def run(args):
    plan, parent, case, slot, bound = validate_entry(args)
    files, fingerprint = implementation_manifest()
    require(fingerprint == plan["implementation_sha256"], "formal_source_changed_before_execution")
    one = SimpleNamespace(
        parent=Path(args.parent),
        legacy_root=Path(parent["legacy_root"]),
        cases=Path(args.plan) / "cases.json",
        case_id=slot["case_id"],
        group=slot["group"],
        output=Path(args.output),
        max_http=slot["max_http"],
        max_total_tokens=LIMIT,
        allow_paid=args.allow_paid,
        api_key_env=args.api_key_env,
    )
    hooks = SimpleNamespace(
        live_configuration=live_configuration,
        live_model=live_model,
        admit=admit,
        new_task=new_task,
        execute=execute,
        collect=collect,
        export_artifacts=export_artifacts,
    )
    return await execute_one(
        one,
        parent,
        case,
        files,
        fingerprint,
        bound,
        hooks,
        extra_meta={
            "scope": "FRESH_FORMAL_SINGLE_SLOT_NOT_RELEASED",
            "kind": "FORMAL_SLOT",
            "formal_plan_sha256": file_hash(Path(args.plan) / "manifest.json"),
            "formal_plan_directory": str(Path(args.plan).resolve()),
            "slot_id": slot["slot_id"],
            "slot_index": plan["slots"].index(slot),
        },
        sandbox_overrides={
            "answer_boundary_version": 2,
            "diagnosis_runtime": "v16",
            "commerce_review_answer_version": 6,
        },
        semantic_contract=planning.read(Path(args.plan) / "semantic-contract.json"),
        reuse_inputs=paired_inputs(args.parent, case) if slot["group"] == "agent" else None,
    )


def audit_round(directory, parent):
    plan, rows = progress(directory, parent)
    accounting = load_parent(parent)
    return {
        "formal_round_complete": len(rows) == len(plan["slots"]),
        "executed_slots_reviewed": len(rows),
        "total_slots": len(plan["slots"]),
        "known_tokens": accounting["known_tokens"],
        "legacy_unknown_frozen_tokens": accounting["frozen_unknown_tokens"],
        "new_model_calls_in_this_audit": 0,
        "release_eligible": False,
        "scope": "Per-slot verification only; final G6 gate review remains separate.",
        "rows": rows,
    }


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--plan", type=Path, required=True)
    r.add_argument("--parent", type=Path, required=True)
    r.add_argument("--slot-id", required=True)
    r.add_argument("--output", type=Path, required=True)
    r.add_argument("--authorization", type=Path, required=True)
    r.add_argument("--allow-paid", action="store_true")
    r.add_argument("--max-total-tokens", type=int, required=True)
    r.add_argument("--api-key-env", default="COMMERCE_FORMAL_MODEL_KEY")
    r.add_argument("--use-project-key", action="store_true")
    a = sub.add_parser("audit-round")
    a.add_argument("--plan", type=Path, required=True)
    a.add_argument("--parent", type=Path, required=True)
    return p


def main():
    args = parser().parse_args()
    if args.command == "audit-round":
        print(json.dumps(audit_round(args.plan, args.parent)))
        return
    validate_entry(args)  # Every gate precedes even Settings/.env loading.
    previous = os.environ.get(args.api_key_env)
    if args.use_project_key:
        from app.core.config import Settings

        settings = Settings(_env_file=ROOT / ".env")
        require(
            settings.openai_base_url.rstrip("/") == "https://api.deepseek.com"
            and settings.llm_model == "deepseek-flash",
            "formal_project_key_destination_mismatch",
        )
        secret = settings.openai_api_key.get_secret_value()
        require(bool(secret), "formal_project_key_missing")
        os.environ[args.api_key_env] = secret
    try:
        failure = asyncio.run(run(args))
    finally:
        if args.use_project_key:
            if previous is None:
                os.environ.pop(args.api_key_env, None)
            else:
                os.environ[args.api_key_env] = previous
    raise SystemExit(1 if failure else 0)


if __name__ == "__main__":
    main()
