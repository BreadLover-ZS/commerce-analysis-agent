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
from evaluations.diagnosis_cases import DEFAULT_CASES, business_input, load_cases
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
    semantic_path = Path(path).with_name("semantic-contract.json")
    semantic = json.loads(semantic_path.read_bytes()) if semantic_path.exists() else None
    if semantic is not None:
        if semantic.get("schema_version") not in {1, 2, 3, 4, 5} or set(
            semantic.get("cases", {})
        ) != {c.case_id for c in cases}:
            raise ValueError("题包语义范围契约必须逐题完整覆盖")
        if semantic["schema_version"] in {2, 3, 4, 5}:
            from evaluations.diagnosis_grading import VERSION_CONTRACTS

            runtime = (
                "diagnosis-v16" if semantic["schema_version"] in {3, 4, 5} else "diagnosis-v15"
            )
            fixed = (
                "commerce-review-bundle-v6"
                if semantic["schema_version"] in {4, 5}
                else "commerce-review-bundle-v5"
                if semantic["schema_version"] == 3
                else "commerce-review-bundle-v4"
            )
            if semantic["schema_version"] in {3, 4, 5} and semantic.get("accounting_protocol") != 2:
                raise ValueError("v3/v4语义契约必须冻结计量发送协议v2")
            if (
                semantic.get("expected_runtime") != VERSION_CONTRACTS[runtime]
                or semantic.get("expected_fixed_runtime") != VERSION_CONTRACTS[fixed]
            ):
                raise ValueError("语义契约必须冻结对应诊断与固定流程完整版本")
            if semantic.get("author_obligations") != json.loads(
                Path(path).with_name("answer-obligations.json").read_bytes()
            ):
                raise ValueError("作者必答项与独立侧表不一致")
        if semantic["schema_version"] == 5 and (
            semantic.get("expected_answer_contract_version") != "answer-contract-v5"
            or semantic.get("expected_answer_boundary_version") != 2
        ):
            raise ValueError("schema5必须冻结新的边界子契约")
        for case in cases:
            rule = semantic["cases"][case.case_id]
            if (
                not rule["min_skus"] <= len(set(case.scope_skus)) <= rule["max_skus"]
                or not set(rule["required_assertions"]) <= set(case.expected.required_assertions)
                or set(rule["sources"]) != set(case.sources)
            ):
                raise ValueError("题包语义范围、断言或数据源不满足作者冻结契约")
            if semantic["schema_version"] in {2, 3, 4, 5}:
                if (
                    rule.get("expected_stop_reasons") != case.expected.stop_reasons
                    or not rule.get("answer_statuses")
                    or rule.get("request_kind") not in {"values", "boundary", "unsupported"}
                ):
                    raise ValueError("停止/答复完成口径与逐题语义契约不一致")
                if rule["request_kind"] == "boundary" and (
                    case.expected.outcome != "gap"
                    or case.expected.stop_reasons != ["evidence_gap"]
                    or rule["answer_statuses"] != ["boundary_explained"]
                ):
                    raise ValueError("边界题必须保留来源缺口，不能批量改为target_covered")
                from evaluations.diagnosis_request_checks_v3 import (
                    boundary_request,
                    requested_items,
                )

                if semantic["schema_version"] == 5:
                    from evaluations.diagnosis_request_checks_v4 import (
                        boundary_request,
                        requested_items,
                    )
                author = next(
                    a
                    for a in semantic["author_obligations"]["cases"]
                    if a["case_id"] == case.case_id
                )
                literal = set(requested_items(case.query, author["final_skus"]))
                mandatory = {
                    (v["sku"], v["metric"], v["part"])
                    for v in author["raw_matrix"] + author["scope_matrix"]
                }
                if literal != mandatory or (
                    rule["request_kind"] == "boundary" and not boundary_request(case.query)
                ):
                    raise ValueError("题面字面义务与作者必答矩阵不一致")
            if rule["dimension"] == "each_sku_daily" and (
                len(set(case.scope_skus)) < 2
                or "multi_sku_daily_covered" not in case.expected.required_assertions
            ):
                raise ValueError("逐商品日序列契约不可降为单SKU或删除范围断言")
    for case in cases:
        if "multi_sku_daily_covered" in case.expected.required_assertions:
            if (
                len(set(case.scope_skus)) < 2
                or "drilldown_series" not in case.expected.required_tools
            ):
                raise ValueError("逐商品日序列验收要求至少两个授权SKU及日序列工具")
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
        "semantic_contract_sha256": file_hash(semantic_path) if semantic is not None else None,
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


def reconcile_history(directory, *, reconciliation=None):
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
        reconciliation=reconciliation,
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
        **(
            {"reconciliation": history["prior_run"]["reconciliation"]}
            if "reconciliation" in history["prior_run"]
            else {}
        ),
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


def historical_question_sources(directory, explicit=()):
    """Recover question/data provenance from the FULL paid ancestry, never labels alone."""
    supplied = {file_hash(p): Path(p).resolve() for p in [DEFAULT_CASES, *explicit]}
    roots, inputs, sources = set(), set(), {}
    root = Path(directory).resolve()
    while root is not None:
        if root in roots or len(roots) >= MAX_BUDGET_ANCESTRY:
            raise ValueError("历史题源承接链循环或过长")
        roots.add(root)
        meta = json.loads((root / "run.json").read_bytes())
        expected = meta.get("cases_file_sha256")
        path = root / "cases-source.json"
        if not path.is_file():
            path = supplied.get(expected)
        if path is None or file_hash(path) != expected:
            raise ValueError("历史题源缺失或hash不一致，不能证明新题包未复用")
        cases, _ = load_cases(path)
        inputs.update(input_hash(case) for case in cases)
        sources[str(path.resolve())] = file_hash(path)
        parent = meta.get("prior_paid_budget", {}).get("prior_run")
        root = Path(parent["directory"]).resolve() if parent else None
    return inputs, sources


def freeze(
    cases_path,
    directory,
    charge_from,
    proposed_limit,
    reference_round,
    used_cases=(),
    *,
    reconciliation=None,
):
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
    history = reconcile_history(charge_from, reconciliation=reconciliation)
    semantic_file = directory / "semantic-contract.json"
    semantic = json.loads(semantic_file.read_bytes()) if semantic_file.exists() else {}
    if semantic.get("schema_version") == 4:
        ancestor_inputs, ancestor_files = historical_question_sources(charge_from, used_cases)
        used_inputs.update(ancestor_inputs)
        prior_files = [
            {"file": name, "sha256": checksum}
            for name, checksum in sorted(
                {
                    **{str(Path(row["file"]).resolve()): row["sha256"] for row in prior_files},
                    **ancestor_files,
                }.items()
            )
        ]
        cases, contract = validate_cases(cases_path, used_inputs)
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


def check(directory, charge_from=None, *, reconciliation=None):
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
    semantic_file = directory / "semantic-contract.json"
    semantic = json.loads(semantic_file.read_bytes()) if semantic_file.exists() else {}
    if semantic.get("schema_version") in {2, 3, 4, 5}:
        from evaluations.diagnosis_grading import VERSION_CONTRACTS
        from evaluations.diagnosis_runtime import EvaluationSettings

        current = EvaluationSettings(_env_file=None)
        actual = VERSION_CONTRACTS.get("diagnosis-" + current.diagnosis_runtime)
        fixed = VERSION_CONTRACTS.get(
            "commerce-review-bundle-v" + str(current.commerce_review_answer_version)
        )
        if semantic["expected_runtime"] != actual or semantic["expected_fixed_runtime"] != fixed:
            raise ValueError("冻结运行版本与实际诊断/固定路由不一致，禁止读取凭据或HTTP")
    if semantic.get("schema_version") == 4:
        explicit = plan["prior_case_files"]
        if any(file_hash(row["file"]) != row["sha256"] for row in explicit):
            raise ValueError("冻结历史题源内容已变化")
        known, sources = historical_question_sources(
            charge_from or plan["charge_from"], [r["file"] for r in explicit]
        )
        for row in explicit:
            known.update(input_hash(case) for case in load_cases(row["file"])[0])
        declared = {str(Path(row["file"]).resolve()): row["sha256"] for row in explicit}
        if known != set(plan["prior_input_hashes"]) or any(
            declared.get(name) != value for name, value in sources.items()
        ):
            raise ValueError("冻结历史题源或新题去重证明不完整")
    if plan["implementation_sha256"] != implementation_manifest()[1]:
        raise ValueError("实现指纹已改变，须重新审查并冻结新计划")
    history = reconcile_history(
        charge_from or plan["charge_from"],
        reconciliation=reconciliation
        if reconciliation is not None
        else plan["history"].get("reconciliation"),
    )
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
    plan = json.loads((directory / "manifest.json").read_bytes())
    if plan.get("formal_entry_version") == 1 or (
        (directory / "semantic-contract.json").is_file()
        and json.loads((directory / "semantic-contract.json").read_bytes()).get("schema_version")
        == 5
    ):
        raise ValueError("schema5正式验证须使用逐Task正式入口，禁止旧整轮入口")
    supplied = getattr(args, "prior_reconciliation", None)
    frozen_certificate = plan.get("history", {}).get("reconciliation")
    if (supplied is None) != (frozen_certificate is None):
        raise ValueError("运行更正证书必须显式提供并与冻结计划一致")
    check(directory, args.charge_paid_run, reconciliation=supplied)
    if plan["budget"]["proposed_limit_estimated_sufficient"] is not True:
        raise ValueError("冻结预算估计不足以完成正式轮；须审查额度并重新冻结，禁止付费执行")
    from evaluations.diagnosis_runtime import EvaluationSettings

    current = EvaluationSettings(_env_file=None)
    if current.commerce_review_answer_version == 6:
        path = directory / "semantic-contract.json"
        if not path.is_file() or json.loads(path.read_bytes()).get("schema_version") != 4:
            raise ValueError("当前正式入口须使用schema4冻结语义侧表，禁止省略或降级")
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
    prepare.add_argument(
        "--reconciliation", type=Path, help="显式冻结经验证的追加更正证书；不改历史记录"
    )
    verify = commands.add_parser("check")
    verify.add_argument("--directory", type=Path, required=True)
    verify.add_argument("--charge-from", type=Path)
    verify.add_argument("--reconciliation", type=Path)
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
                reconciliation=args.reconciliation,
            )
            result = {
                k: plan[k] for k in ("status", "total_tasks", "implementation_sha256", "budget")
            }
        else:
            result = check(args.directory, args.charge_from, reconciliation=args.reconciliation)
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(2, f"预检失败：{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
