"""Freeze and check a final evaluation plan without executing Tasks or HTTP.

This prepares evidence for a human decision. A frozen plan grants no paid-call
authorization and cannot turn a corpus check into a model-quality result.
"""

import argparse
import hashlib
import json
from pathlib import Path

from app.diagnosis.inputs import digest
from evaluations.diagnosis import ROOT, implementation_manifest, write
from evaluations.diagnosis_budget import MAX_BUDGET_ANCESTRY, prior_spend
from evaluations.diagnosis_cases import business_input, load_cases
from evaluations.diagnosis_grading import ASSERTIONS, TOOLS

CONTEXT_CEILING = 1048576
OUTPUT_CEILING = 3000
REQUEST_RESERVATION = CONTEXT_CEILING + OUTPUT_CEILING
TARGETS = ROOT / "evaluations/diagnosis_targets_v1.json"
LIMITATIONS = [
    "Author-prepared synthetic variants, not external business blind acceptance.",
    "Input hashes detect exact reuse, not semantic independence from development families.",
    "No Agent, Worker, graph or model execution is allowed during preparation.",
    "Budget estimates are observations, not a guarantee of completion or monetary cost.",
]


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def input_hash(case):
    value = business_input(case)
    value.pop("query")
    return digest(value)


def validate_cases(path, used_inputs=()):
    cases, selected_hash = load_cases(path)
    if len(cases) != 20 or any(
        c.split != "holdout"
        or c.execution != "diagnostic"
        or c.harness.mode not in {"none", "clarify"}
        for c in cases
    ):
        raise ValueError("正式题包须恰好20道保留集业务题，不得混入故障/准入替身")
    hashes = [input_hash(c) for c in cases]
    if len(hashes) != len(set(hashes)) or set(hashes) & set(used_inputs):
        raise ValueError("业务数据重复；只改问法不能成为未执行的新输入")
    for case in cases:
        if (
            set(case.expected.required_assertions) - ASSERTIONS
            or set(case.expected.required_tools) - TOOLS
        ):
            raise ValueError("题包含未实现的断言或工具")
        if case.harness.mode == "clarify" and (
            case.harness.clarification_choice not in case.scope_skus
        ):
            raise ValueError("澄清选择必须属于授权SKU")
    common = sum(c.expected.baseline_capability == "common" for c in cases)
    if not common or common == len(cases):
        raise ValueError("正式题包须同时覆盖共同能力与新增能力")
    return cases, {
        "cases_file_sha256": file_hash(path),
        "selected_cases_sha256": selected_hash,
        "expected_sha256": digest([c.expected.model_dump(mode="json") for c in cases]),
        "targets_sha256": file_hash(TARGETS),
        "input_hashes": hashes,
        "case_ids": [c.case_id for c in cases],
        "diagnosis_tasks": 60,
        "paired_fixed_tasks": common * 3,
        "total_tasks": 60 + common * 3,
    }


def reconcile_history(directory):
    meta = json.loads((Path(directory) / "run.json").read_bytes())
    if (
        meta.get("provider") != "DeepSeek"
        or meta.get("endpoint") != "https://api.deepseek.com"
        or meta.get("model") != "deepseek-flash"
    ):
        raise ValueError("预检仅支持当前已登记的DeepSeek正式评测配置")
    history = prior_spend(
        directory,
        provider=meta["provider"],
        endpoint=meta["endpoint"],
        model=meta["model"],
        token_limit=meta["max_total_tokens"],
        require_stopped=False,
    )
    if Path(directory).with_name(Path(directory).name + ".budget-continuation.json").exists():
        raise ValueError("该预算祖先已被其他轮次承接；请改用最新回执")
    if history["ancestry_depth"] + 1 > MAX_BUDGET_ANCESTRY:
        raise ValueError("加入新轮后的预算承接链过长；禁止开始付费运行")
    return {
        "provider": meta["provider"],
        "endpoint": meta["endpoint"],
        "model": meta["model"],
        "previous_cumulative_limit": history["max_total_tokens"],
        "known_tokens": history["known_tokens"],
        "provider_requests": history["provider_requests"],
        "run_id": history["prior_run"]["run_id"],
        "files_sha256": history["prior_run"]["files_sha256"],
    }


def budget_projection(history, proposed_limit, observed_round_tokens):
    if (
        type(proposed_limit) is not int
        or proposed_limit < history["previous_cumulative_limit"]
        or type(observed_round_tokens) is not int
        or observed_round_tokens <= 0
    ):
        raise ValueError("拟议累计上限不得降低历史上限，参考轮用量须为正整数")
    spent = history["known_tokens"]
    required = spent + observed_round_tokens + REQUEST_RESERVATION
    return {
        "proposed_cumulative_limit": proposed_limit,
        "request_reservation": REQUEST_RESERVATION,
        "reference_round_tokens": observed_round_tokens,
        "nominal_remaining_at_existing_limit": history["previous_cumulative_limit"] - spent,
        "estimated_cumulative_with_reservation": required,
        "existing_limit_estimated_sufficient": required <= history["previous_cumulative_limit"],
        "proposed_limit_estimated_sufficient": required <= proposed_limit,
        "proposed_margin_after_reference_round_and_reservation": proposed_limit - required,
        "authorization_required": True,
        "monetary_cost": None,
    }


def freeze(cases_path, directory, charge_from, proposed_limit, reference_round, used_cases=()):
    cases_path, directory, charge_from = map(Path, (cases_path, directory, charge_from))
    # Keep plan material outside the implementation fingerprint to avoid a
    # circular hash: manifest -> implementation -> manifest.
    if directory.resolve().is_relative_to(ROOT / "evaluations") and "results" not in (
        directory.resolve().relative_to(ROOT).parts
    ):
        raise ValueError("冻结目录请使用docs/acceptance或evaluations/results，避免循环指纹")
    if cases_path.resolve().parent != directory.resolve():
        raise ValueError("题包须和冻结manifest保存在同一目录")
    used_inputs, prior_files = set(), []
    for path in used_cases:
        old, _ = load_cases(path)
        used_inputs.update(input_hash(c) for c in old)
        prior_files.append({"file": str(path), "sha256": file_hash(path)})
    cases, contract = validate_cases(cases_path, used_inputs)
    history = reconcile_history(charge_from)
    reference_meta = json.loads((Path(reference_round) / "run.json").read_bytes())
    reference_summary = json.loads((Path(reference_round) / "summary.json").read_bytes())
    reference_history = prior_spend(
        reference_round,
        provider=history["provider"],
        endpoint=history["endpoint"],
        model=history["model"],
        token_limit=reference_meta["max_total_tokens"],
        require_stopped=False,
    )
    if (
        reference_meta.get("planned", {}).get("split") != "holdout"
        or reference_summary.get("executed_runs") != contract["total_tasks"]
        or reference_summary.get("stopped") is not None
    ):
        raise ValueError("预算参考须为同规模完整正式轮")
    before = reference_meta.get("prior_paid_budget", {}).get("known_tokens", 0)
    observed = reference_history["known_tokens"] - before
    plan = {
        "schema_version": 1,
        "status": "FROZEN_AWAITING_EXPLICIT_PAID_AUTHORIZATION",
        "cases_file": cases_path.name,
        "implementation_sha256": implementation_manifest()[1],
        **contract,
        "history": history,
        "charge_from": str(charge_from),
        "reference_round": {
            "directory": str(reference_round),
            "run_id": reference_meta["run_id"],
            "observed_tokens": observed,
        },
        "budget": budget_projection(history, proposed_limit, observed),
        "prior_case_files": prior_files,
        "prior_input_hashes": sorted(used_inputs),
        "model_or_graph_executions_at_freeze": 0,
        "limitations": LIMITATIONS,
    }
    write(directory / "manifest.json", plan)
    return plan, cases


def check(directory, charge_from=None):
    directory = Path(directory)
    plan = json.loads((directory / "manifest.json").read_bytes())
    if plan.get("schema_version") != 1 or plan.get("status") != (
        "FROZEN_AWAITING_EXPLICIT_PAID_AUTHORIZATION"
    ):
        raise ValueError("冻结计划版本或状态无效")
    case_file = directory / plan["cases_file"]
    if case_file.resolve().parent != directory.resolve():
        raise ValueError("题包路径越出冻结目录")
    _, contract = validate_cases(case_file, plan["prior_input_hashes"])
    if any(plan.get(k) != v for k, v in contract.items()):
        raise ValueError("题目、预期、门槛或配对计划已改变，禁止沿用原冻结计划")
    if plan["implementation_sha256"] != implementation_manifest()[1]:
        raise ValueError("实现指纹已改变，须重新审查并冻结新计划")
    history = reconcile_history(charge_from or plan["charge_from"])
    if history != plan["history"]:
        raise ValueError("历史扣费或回执已改变，须重新核对预算")
    if plan["budget"] != budget_projection(
        history,
        plan["budget"]["proposed_cumulative_limit"],
        plan["reference_round"]["observed_tokens"],
    ):
        raise ValueError("预算投影与冻结参数不一致")
    return {
        "validation_passed": True,
        "total_tasks": contract["total_tasks"],
        "implementation_sha256": plan["implementation_sha256"],
        "known_tokens": history["known_tokens"],
        "budget": plan["budget"],
        "provider_requests_this_check": 0,
        "release_eligible": False,
    }


def guard_run(args):
    """Bind the actual CLI to its frozen plan; --allow-paid remains mandatory."""
    directory = Path(args.frozen_plan)
    check(directory, args.charge_paid_run)
    plan = json.loads((directory / "manifest.json").read_bytes())
    if (
        args.mode != "live"
        or args.split != "holdout"
        or args.repeats != 3
        or not args.final_holdout
        or args.case_ids is not None
        or args.prior_paid_run is not None
        or args.charge_paid_run is None
        or args.max_paid_requests is not None
        or file_hash(args.cases) != plan["cases_file_sha256"]
        or args.max_total_tokens != plan["budget"]["proposed_cumulative_limit"]
        or any(getattr(args, k) != plan["history"][k] for k in ("provider", "endpoint", "model"))
    ):
        raise ValueError("运行参数与冻结的完整正式轮不一致，禁止付费执行")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("freeze")
    prepare.add_argument("--cases", type=Path, required=True)
    prepare.add_argument("--directory", type=Path, required=True)
    prepare.add_argument("--charge-from", type=Path, required=True)
    prepare.add_argument("--reference-round", type=Path, required=True)
    prepare.add_argument("--proposed-total-tokens", type=int, required=True)
    prepare.add_argument("--used-cases", type=Path, action="append", default=[])
    verify = commands.add_parser("check")
    verify.add_argument("--directory", type=Path, required=True)
    verify.add_argument("--charge-from", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "freeze":
            plan, _ = freeze(
                args.cases,
                args.directory,
                args.charge_from,
                args.proposed_total_tokens,
                args.reference_round,
                args.used_cases,
            )
            result = {
                k: plan[k] for k in ("status", "total_tasks", "implementation_sha256", "budget")
            }
        else:
            result = check(args.directory, args.charge_from)
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(2, f"预检失败：{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
