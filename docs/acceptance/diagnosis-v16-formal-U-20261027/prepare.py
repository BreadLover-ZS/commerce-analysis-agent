"""Prepare new U inputs and metadata only; never execute fresh cases or load keys."""

import copy
import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

from evaluations.diagnosis import implementation_manifest
from evaluations.diagnosis_cases import Case, load_cases, reference_answer
from evaluations.diagnosis_grading import VERSION_CONTRACTS
from evaluations.diagnosis_plan import input_hash, validate_cases
from evaluations.diagnosis_probe_accounting import file_hash

ROOT = Path(__file__).resolve().parents[3]
BASE = Path(__file__).resolve().parent
SOURCE = BASE.parent / "diagnosis-v15-h01-h20"
CAPS = {
    1: 2,
    2: 2,
    3: 2,
    4: 2,
    5: 1,
    6: 1,
    7: 1,
    8: 2,
    9: 2,
    10: 1,
    11: 1,
    12: 2,
    13: 4,
    14: 2,
    15: 3,
    16: 3,
    17: 2,
    18: 2,
    19: 3,
    20: 2,
}
BLOCKERS = [
    "legacy_strict_reconciliation_remains_blocked",
    "conservative_hold_authorization_covers_used_input_probes_only",
    "formal_cli_cannot_carry_probe_leaf_accounting",
    "formal_cli_lacks_each_task_manual_review_and_all_failure_stop",
    "new_formal_packet_external_send_not_authorized_in_this_preparation",
]


def read(path):
    return json.loads(Path(path).read_bytes())


def save(path, value):
    with Path(path).open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def slots(cases):
    result = []
    for c in cases:
        for repeat in range(1, 4):
            for group in (
                ["fixed", "agent"] if c.expected.baseline_capability == "common" else ["additional"]
            ):
                result.append(
                    {
                        "slot_id": f"{c.case_id}-{repeat}/{group}",
                        "case_id": c.case_id,
                        "repeat": repeat,
                        "group": group,
                        "max_http": 2 if group == "fixed" else CAPS[int(c.case_id[1:])],
                        "model_decision_reserve": 0,
                        "stop_after_model_failure": True,
                        "manual_review_required_before_next_slot": True,
                        "status": "UNEXECUTED",
                    }
                )
    return result


def main():
    if any(
        (BASE / p).exists()
        for p in [
            "cases.json",
            "semantic-contract.json",
            "answer-obligations.json",
            "manifest.json",
        ]
    ):
        raise FileExistsError("Never overwrite a frozen or partially prepared packet")
    author = module("u_literal_author", SOURCE / "prepare.py")
    old_rule = author.authored_rule

    def rule(number, c):
        selected, dimension, metrics = old_rule(number, c)
        if number == 14:
            selected = [s for s in c.scope_skus if s != "U141"]
        return selected, dimension, metrics

    author.authored_rule = rule
    old_cases = load_cases(SOURCE / "cases.json")[0]
    old_sem = read(SOURCE / "semantic-contract.json")
    cases = []
    origins = []
    answers = []

    def shift(v):
        return str(date.fromisoformat(v) + timedelta(days=73))

    for number, old in enumerate(old_cases, 1):
        raw = copy.deepcopy(old.model_dump(mode="json"))
        mapping = {p["sku"]: p["sku"].replace("T", "U", 1) for p in raw["sales"]["products"]}
        divisor = 3 + number % 3
        for p in raw["sales"]["products"]:
            p["sku"] = mapping[p["sku"]]
            p["active_from"] = shift(p["active_from"])
        for r in raw["sales"]["daily_sales"]:
            r["sku"] = mapping[r["sku"]]
            r["day"] = shift(r["day"])
            for key in ["paid_units", "refunded_units", "revenue_cents", "refund_cents"]:
                assert r[key] % divisor == 0
                r[key] = r[key] // divisor * 2
        for name in ["period", "comparison_period"]:
            raw[name] = {k: shift(v) for k, v in raw[name].items()}
        raw["scope_skus"] = [mapping[s] for s in raw["scope_skus"]]
        for before, after in mapping.items():
            raw["query"] = raw["query"].replace(before, after)
        if raw["harness"]["clarification_choice"]:
            raw["harness"]["clarification_choice"] = mapping[raw["harness"]["clarification_choice"]]
        if raw["inventory"]:
            raw["inventory"]["snapshot_day"] = shift(raw["inventory"]["snapshot_day"])
            for item in raw["inventory"]["items"]:
                item["sku"] = mapping[item["sku"]]
                assert item["available_units"] % divisor == 0
                item["available_units"] = item["available_units"] // divisor * 2
                if item.get("snapshot_day"):
                    item["snapshot_day"] = shift(item["snapshot_day"])
        for doc in raw["documents"]:
            doc["text"] = "U系列独立日期与数值核对版本。" + doc["text"]
        if number == 7:
            for p in raw["sales"]["products"]:
                p["active_from"] = raw["period"]["start"]
            raw["query"] = (
                "请给出授权范围本期已有净销售额，说明比较期（早于启用日）不可比较的边界，不补造比较期零值。"
            )
            raw["facet"] = "pre_activation_comparison_boundary"
        if number == 9:
            raw["query"] = "按品类核对两期净销售额变化贡献，列出正项和负项并对账总体差额。"
        raw["expected"].update(reference_answer(Case.model_validate(raw)))
        c = Case.model_validate(raw)
        answer = author.obligation(number, c)
        if number == 14:
            answer["excluded_skus"] = ["U141"]
        answers.append(answer)
        cases.append(c)
        origins.append(
            {
                "case_id": c.case_id,
                "source_case": old.case_id,
                "sku_mapping": mapping,
                "date_shift_days": 73,
                "numeric_scale_numerator": 2,
                "numeric_scale_denominator": divisor,
                "input_hash": input_hash(c),
                "semantic_family_reused": True,
                "new_pre_activation_boundary": number == 7,
            }
        )
    obligations = {
        "schema_version": 1,
        "author": "AI code author, not external blind acceptance",
        "derivation": "literal author choices and raw integer rows; not production intent",
        "model_or_graph_executions": 0,
        "cases": answers,
    }
    semantic = copy.deepcopy(old_sem)
    semantic.update(
        schema_version=4,
        accounting_protocol=2,
        expected_runtime=VERSION_CONTRACTS["diagnosis-v16"],
        expected_fixed_runtime=VERSION_CONTRACTS["commerce-review-bundle-v6"],
        author_obligations=obligations,
    )
    save(
        BASE / "cases.json",
        {
            "schema_version": 1,
            "oracle": "independent_raw_rows_v1",
            "splits": {"development": 0, "regression": 0, "holdout": 20},
            "cases": [c.model_dump(mode="json") for c in cases],
        },
    )
    save(BASE / "answer-obligations.json", obligations)
    save(BASE / "semantic-contract.json", semantic)
    _, contract = validate_cases(BASE / "cases.json")
    save(
        BASE / "provenance.json",
        {
            "source": str((SOURCE / "cases.json").relative_to(ROOT)),
            "source_sha256": file_hash(SOURCE / "cases.json"),
            "reused_author_module_sha256": file_hash(SOURCE / "prepare.py"),
            "origins": origins,
            "new_data_hashes_not_semantic_independence": True,
            "fresh_case_graph_tool_model_executions": 0,
        },
    )
    schedule = {
        "schema_version": 1,
        "slots": slots(cases),
        "total_slots": 96,
        "require_sequential_launch": True,
        "no_automatic_resume_or_retry": True,
        "no_sdk_retries": True,
        "no_fallback_extra_calls": True,
        "stop_reasons": [
            "protocol_error",
            "proposal_or_tool_rejected",
            "call_error",
            "automatic_grade_failed",
            "manual_review_failed",
            "usage_unknown",
            "budget_insufficient",
        ],
        "all_slots_unexecuted": True,
        "release_eligible": False,
    }
    save(BASE / "execution-plan.json", schedule)
    files, _ = implementation_manifest()
    for p in [
        "cases.json",
        "answer-obligations.json",
        "semantic-contract.json",
        "provenance.json",
        "execution-plan.json",
    ]:
        assert str((BASE / p).relative_to(ROOT)) not in files
    save(BASE / "packet-contract.json", contract)
    print(
        json.dumps(
            {
                "prepared": 20,
                "total_slots": 96,
                "fresh_case_executions": 0,
                "provider_requests": 0,
                "status": "PREPARED_NOT_PAID_ADMITTED",
            }
        )
    )


if __name__ == "__main__":
    main()
