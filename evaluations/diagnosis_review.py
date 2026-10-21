"""Hash-bound independent review import. Never overwrite automatic receipts."""

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from evaluations.diagnosis_budget import critical_failure
from evaluations.diagnosis_grading import sha

CRITERIA = {
    "business_conclusions",
    "forbidden_conclusions_absent",
    "all_facts_bound",
    "no_unsupported_causality",
    "required_sections_complete",
    "rule_applicability_not_certified",
}


def conclusion_criteria(expected):
    return {"accepted: " + text for text in expected["accepted_conclusions"]} | {
        "forbidden_absent: " + text for text in expected["forbidden_conclusions"]
    }


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Check(Strict):
    passed: StrictBool
    reason: str = Field(min_length=5, max_length=2000)
    evidence_refs: list[str] = Field(min_length=1, max_length=30)


class Review(Strict):
    receipt: str
    receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verdict: Literal["PASS", "FAIL"]
    checks: dict[str, Check]


class Reviews(Strict):
    reviewer: str = Field(min_length=2, max_length=200)
    reviewer_kind: Literal["human", "independent_external_reviewer"]
    independent_of_task_model: Literal[True]
    records: list[Review] = Field(min_length=1, max_length=2000)


def review_run(directory, reviews_path, grading_audit_path=None):
    directory = Path(directory).resolve()
    meta = json.loads((directory / "run.json").read_text())
    summary = json.loads((directory / "summary.json").read_text())
    template = json.loads((directory / "review-template.json").read_text())
    reviews = Reviews.model_validate_json(Path(reviews_path).read_bytes())
    audited_grades = None
    if grading_audit_path:
        from evaluations.diagnosis_audit import audit_run

        audited = json.loads(Path(grading_audit_path).read_bytes())
        if audited != audit_run(directory):
            raise ValueError("审计回执、源码或原始运行已变化，须重新核对")
        audited_grades = {r["receipt"]: r["current_grading"] for r in audited["records"]}
    expected = {r["receipt"]: r["receipt_sha256"] for r in template["records"]}
    # The editable template cannot define which executions require review.
    # Bind its coverage to the retained progress and actual receipt bytes.
    progress = [
        json.loads(line) for line in (directory / "progress.jsonl").read_bytes().splitlines()
    ]
    actual = {}
    for row in progress:
        path = (directory / row["receipt"]).resolve()
        if not path.is_relative_to(directory) or row["receipt"] in actual:
            raise ValueError("逐次审查进度路径非法或重复")
        data = path.read_bytes()
        if json.loads(data).get("measurement") != row:
            raise ValueError("逐次审查回执与进度不一致")
        actual[row["receipt"]] = hashlib.sha256(data).hexdigest()
    if len(progress) != summary["executed_runs"] or expected != actual:
        raise ValueError("审查模板须覆盖保留进度的全部运行，不能删除基线或失败记录")
    manifest_path = directory / "expectations-manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if [item["case_id"] for item in manifest] != meta["planned"]["case_ids"]:
            raise ValueError("独立预期清单与计划案例不一致")
        by_case = {item["case_id"]: item["expected"] for item in manifest}
        expected_cases = [item["expected"] for item in manifest]
        for receipt in expected:
            parent = Path(receipt).parent
            case_id, repeat = parent.name.rsplit("-", 1)
            if case_id not in by_case or not 1 <= int(repeat) <= meta["planned"]["repeats"]:
                raise ValueError("保留回执不属于计划运行")
            path = (directory / parent / "expectations.json").resolve()
            if (
                not path.is_relative_to(directory)
                or json.loads(path.read_text()) != by_case[case_id]
            ):
                raise ValueError("各次运行的业务预期版本不一致")
    else:
        # Backward compatibility with complete runs predating the root manifest.
        expected_cases = []
        for case_id in meta["planned"]["case_ids"]:
            first = json.loads((directory / f"{case_id}-1/expectations.json").read_text())
            expected_cases.append(first)
            for repeat in range(1, meta["planned"]["repeats"] + 1):
                if (
                    json.loads((directory / f"{case_id}-{repeat}/expectations.json").read_text())
                    != first
                ):
                    raise ValueError("各次运行的业务预期版本不一致")
    if sha(expected_cases) != meta["expected_sha256"]:
        raise ValueError("独立预期哈希与运行登记不一致")
    selected = [r.receipt for r in reviews.records]
    if len(selected) != len(set(selected)) or set(selected) != set(expected):
        raise ValueError("逐次审查须覆盖全部运行且无重复；不能挑选最好一次")
    reviewed, failures = [], []
    for review in reviews.records:
        path = (directory / review.receipt).resolve()
        if not path.is_relative_to(directory) or path.suffix != ".json":
            raise ValueError("审查回执路径非法")
        receipt_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if receipt_hash != expected[review.receipt] or receipt_hash != review.receipt_sha256:
            raise ValueError("审查对象回执哈希不匹配")
        business_expected = json.loads(path.parent.joinpath("expectations.json").read_text())
        if set(review.checks) != CRITERIA | conclusion_criteria(business_expected):
            raise ValueError("逐项语义审查门槛不完整")
        record = json.loads(path.read_text())
        # Evidence refs address retained receipt JSON fields; absolute external paths
        # or a model's own verdict are not independent evidence.
        for check in review.checks.values():
            for ref in check.evidence_refs:
                node = record
                try:
                    for part in ref.split("/"):
                        node = node[int(part)] if isinstance(node, list) else node[part]
                except (KeyError, ValueError, IndexError, TypeError):
                    raise ValueError("审查引用未指向保留的回执字段") from None
        passed = all(check.passed for check in review.checks.values())
        if (review.verdict == "PASS") != passed:
            raise ValueError("审查判定与逐项结果不一致")
        measured = record["measurement"]
        grading = audited_grades[review.receipt] if audited_grades else record["grading"]
        automatic = grading["automatic_passed"]
        good = passed and automatic
        reviewed.append(
            {
                **measured,
                "automatic_passed": automatic,
                "automatic_safety_passed": not critical_failure(grading),
                "semantic_passed": passed,
                "business_passed": good,
            }
        )
        if not good:
            failures.append(review.receipt)
    agents = [r for r in reviewed if r["group"] in {"agent", "additional"}]
    positive = [r for r in agents if r["positive"]]
    critical = [r for r in reviewed if r["critical"]]
    gates = [r for r in reviewed if r["group"] == "gate"]
    all_cases = set(meta["planned"]["case_ids"])
    full_holdout = (
        meta["planned"]["mode"] == "live"
        and meta["planned"]["split"] == "holdout"
        and len(all_cases) == 20
        and meta["planned"]["repeats"] == 3
        and not summary["stopped"]
    )
    complete_repeats = all(
        len(
            [
                r
                for r in reviewed
                if r["case_id"] == case_id and r["group"] in {"agent", "additional", "gate"}
            ]
        )
        == 3
        for case_id in all_cases
    )
    expected_by_case = dict(zip(meta["planned"]["case_ids"], expected_cases, strict=True))
    common_cases = {
        case_id
        for case_id, expected_case in expected_by_case.items()
        if expected_case["baseline_capability"] == "common"
    }
    paired_comparison_valid = bool(common_cases)
    for case_id in all_cases:
        for repeat in range(1, meta["planned"]["repeats"] + 1):
            fixed_rows = [
                r
                for r in reviewed
                if r["case_id"] == case_id and r["repeat"] == repeat and r["group"] == "fixed"
            ]
            if case_id not in common_cases:
                paired_comparison_valid &= not fixed_rows
                continue
            agent_rows = [
                r
                for r in reviewed
                if r["case_id"] == case_id and r["repeat"] == repeat and r["group"] == "agent"
            ]
            if len(fixed_rows) != 1 or len(agent_rows) != 1:
                paired_comparison_valid = False
                continue
            paired_comparison_valid &= all(
                fixed_rows[0].get(key) is not None
                and fixed_rows[0].get(key) == agent_rows[0].get(key)
                for key in ("bundle_hash", "budget", "model_configuration_hash", "final_scope")
            )

    def correct(rows):
        return sum(r["business_passed"] for r in rows) / len(rows) if rows else None

    behavior, completion, critical_rate = (
        correct(agents + gates),
        correct(positive),
        correct(critical),
    )
    targets = json.loads((Path(__file__).parent / "diagnosis_targets_v1.json").read_text())
    if (
        hashlib.sha256(
            (Path(__file__).parent / "diagnosis_targets_v1.json").read_bytes()
        ).hexdigest()
        != meta["targets_sha256"]
    ):
        raise ValueError("放行目标版本已变化，须显式重新评估")
    eligible = (
        full_holdout
        and complete_repeats
        and paired_comparison_valid
        and behavior is not None
        and completion is not None
        and behavior >= targets["expected_behavior_rate_min"]
        and completion >= targets["positive_task_completion_rate_min"]
        and critical_rate == 1.0
        and all(r["metrics"].get("usage_complete") is True for r in agents)
    )
    # Contract/fact errors cannot be averaged away even in noncritical labels.
    eligible = eligible and all(
        r["automatic_safety_passed"] for r in reviewed if r["execution"] == "diagnostic"
    )
    eligible = eligible and all(
        all(
            review.checks[key].passed
            for key in (
                "all_facts_bound",
                "no_unsupported_causality",
                "rule_applicability_not_certified",
            )
        )
        for review in reviews.records
    )
    return {
        "run_id": meta["run_id"],
        "review_completed": True,
        "automatic_safety_all_passed": all(r["automatic_safety_passed"] for r in reviewed),
        "paired_comparison_valid": paired_comparison_valid,
        "execution_source_sha256": meta["implementation_sha256"],
        "release_eligible": eligible,
        "reviewer": reviews.reviewer,
        "reviewer_kind": reviews.reviewer_kind,
        "reviews_sha256": hashlib.sha256(Path(reviews_path).read_bytes()).hexdigest(),
        "grading_audit_sha256": hashlib.sha256(Path(grading_audit_path).read_bytes()).hexdigest()
        if grading_audit_path
        else None,
        "automatic_summary_sha256": sha(summary),
        "failures": failures,
        "expected_behavior_rate": behavior,
        "positive_task_completion_rate": completion,
        "critical_rate": critical_rate,
        "independent_cases": len(all_cases),
        "groups": {
            g: {
                "runs": len(rows := [r for r in reviewed if r["group"] == g]),
                "business_pass_rate": correct(rows),
            }
            for g in sorted({r["group"] for r in reviewed})
        },
        "default_entrypoint": "commerce_review",
        "default_decision": "Retain workflow. This review does not automatically change product defaults; paired effects and costs must be assessed explicitly.",
    }
