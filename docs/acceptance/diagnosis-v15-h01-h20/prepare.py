"""Author unexecuted v15 inputs and literal obligations; no Task, tool or model."""

import copy
import hashlib
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from app.diagnosis.inputs import digest  # noqa: E402
from evaluations.diagnosis_cases import Case, load_cases, reference_answer  # noqa: E402
from evaluations.diagnosis_oracle import raw_totals  # noqa: E402
from evaluations.diagnosis_plan import input_hash, validate_cases  # noqa: E402


def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def authored_rule(number, case):
    """Literal author choices. Never derive these from the production parser."""
    selected = list(case.scope_skus)
    dimension, metrics = None, []
    if number == 1:
        dimension, metrics = "sku", ["gross_revenue", "refund_amount", "net_revenue"]
    if number in (4, 8, 12):
        dimension = "sku"
    if number in (4, 8, 12, 13):
        metrics = ["net_revenue"]
    if number == 9:
        dimension = "category"
    if number == 14:
        selected = [sku for sku in selected if sku != "T141"]
        metrics = ["net_revenue", "paid_units", "refunded_units"]
    if number == 15:
        metrics = ["paid_units", "refunded_units"]
    if case.harness.clarification_choice:
        selected = [case.harness.clarification_choice]
    return selected, dimension, metrics


# Author-defined scope rows, independent of both production and grading intent.
SCOPE_RULES = {
    2: (["refund_amount", "refunded_units", "paid_units"], ["current", "comparison"]),
    3: (["net_revenue", "gross_revenue", "refund_amount"], ["current", "comparison", "delta"]),
    5: (["net_revenue"], ["current", "comparison", "delta"]),
    7: (["net_revenue"], ["current"]),
    9: (["net_revenue"], ["current", "comparison", "delta"]),
    16: (["net_revenue"], ["current", "comparison", "delta"]),
}


def obligation(number, case):
    selected, dimension, metrics = authored_rule(number, case)
    raw = case.model_dump(mode="json")
    matrix, daily = [], []
    for sku in selected:
        values = {
            name: raw_totals(raw["sales"], [sku], raw[name])
            for name in ("period", "comparison_period")
        }
        for metric in metrics:
            for part, periods in (
                ("current", ["period"]),
                ("comparison", ["comparison_period"]),
                ("delta", ["period", "comparison_period"]),
            ):
                if number == 13 and part == "delta":
                    continue
                applicable = all(values[p][1]["expected_records"] > 0 for p in periods)
                complete = applicable and all(values[p][1]["complete"] for p in periods)
                value = (
                    (
                        values[periods[0]][0][metric] - values[periods[1]][0][metric]
                        if len(periods) == 2
                        else values[periods[0]][0][metric]
                    )
                    if complete
                    else None
                )
                matrix.append(
                    {
                        "sku": sku,
                        "metric": metric,
                        "part": part,
                        "unit": "count" if metric.endswith("units") else "CNY",
                        "raw_integer": value,
                        "scale": 1 if metric.endswith("units") else 100,
                        "status": "required"
                        if complete
                        else "not_applicable"
                        if not applicable
                        else "missing_source",
                    }
                )
        if number == 12:
            raw["query"] = (
                "请逐商品列出两期净销售额及差额，核对净销售额变化最大商品的贡献，列明其余商品贡献并对账。"
            )
        if number == 13:
            for period in ("period", "comparison_period"):
                day, last = (
                    date.fromisoformat(raw[period]["start"]),
                    date.fromisoformat(raw[period]["end"]),
                )
                while day <= last:
                    amount, coverage = raw_totals(
                        raw["sales"], [sku], {"start": str(day), "end": str(day)}
                    )
                    daily.append(
                        {
                            "sku": sku,
                            "period": period,
                            "day": str(day),
                            "net_cents": amount["net_revenue"],
                            "coverage": coverage,
                        }
                    )
                    day += timedelta(days=1)
    contributions = []
    if dimension:
        groups = {}
        for product in raw["sales"]["products"]:
            if product["sku"] in selected:
                groups.setdefault(product[dimension], []).append(product["sku"])
        for entity, skus in sorted(groups.items()):
            current, cc = raw_totals(raw["sales"], skus, raw["period"])
            prior, pc = raw_totals(raw["sales"], skus, raw["comparison_period"])
            contributions.append(
                {
                    "entity": entity,
                    "dimension": dimension,
                    "skus": skus,
                    "delta_cents": current["net_revenue"] - prior["net_revenue"]
                    if cc["complete"] and pc["complete"]
                    else None,
                }
            )
    scope_matrix = []
    scope_values = {
        name: raw_totals(raw["sales"], selected, raw[name])
        for name in ("period", "comparison_period")
    }
    scope_metrics, scope_parts = SCOPE_RULES.get(number, ([], []))
    for metric in scope_metrics:
        for part in scope_parts:
            periods = (
                ["period", "comparison_period"]
                if part == "delta"
                else ["period"]
                if part == "current"
                else ["comparison_period"]
            )
            applicable = all(scope_values[p][1]["expected_records"] > 0 for p in periods)
            complete = applicable and all(scope_values[p][1]["complete"] for p in periods)
            value = (
                (
                    scope_values[periods[0]][0][metric] - scope_values[periods[1]][0][metric]
                    if len(periods) == 2
                    else scope_values[periods[0]][0][metric]
                )
                if complete
                else None
            )
            scope_matrix.append(
                {
                    "sku": ",".join(sorted(selected)),
                    "metric": metric,
                    "part": part,
                    "unit": "count" if metric.endswith("units") else "CNY",
                    "raw_integer": value,
                    "scale": 1 if metric.endswith("units") else 100,
                    "status": "required"
                    if complete
                    else "not_applicable"
                    if not applicable
                    else "missing_source",
                }
            )
    aggregate = reference_answer(case.model_copy(update={"scope_skus": selected}))
    return {
        "case_id": case.case_id,
        "query": case.query,
        "authorized_skus": case.scope_skus,
        "final_skus": selected,
        "excluded_skus": ["T141"] if number == 14 else [],
        "contribution_dimension": dimension,
        "period": raw["period"],
        "comparison_period": raw["comparison_period"],
        "component_metrics": metrics,
        "component_periods": (
            ["current", "comparison"] if number == 13 else ["current", "comparison", "delta"]
        )
        if metrics
        else [],
        "raw_matrix": matrix,
        "scope_matrix": scope_matrix,
        "daily_matrix": daily,
        "raw_contributions": contributions,
        "selected_aggregate_expected": aggregate,
        "authorized_aggregate_expected": reference_answer(case),
        "required_assertions": case.expected.required_assertions,
        "expected_stop_reasons": case.expected.stop_reasons,
        "mandatory_answer_assertions": [
            "v15_required_values_displayed",
            "v15_completion_not_self_certified",
            "v15_boundary_requires_source_proof",
            "v15_execution_failure_not_complete",
            "v15_requested_dimensions",
            "v15_expected_answer_status",
        ],
        "gap_inventory_policy": {
            "all_pending_items_required": True,
            "display_limit": 20,
            "if_more_than_20": "full unique details, exact total/displayed/omitted, visible omission disclosure",
            "force_budget_failure": False,
        },
        "repair_coverage": ["R1"]
        if number == 2
        else ["R3", "R4"]
        if number == 3
        else ["R2", "R1"]
        if number == 17
        else ["R2"]
        if number in (6, 7)
        else ["shared_required_items"],
    }


def main():
    directory = Path(__file__).resolve().parent
    outputs = ["cases.json", "semantic-contract.json", "answer-obligations.json", "provenance.json"]
    if any((directory / name).exists() for name in outputs):
        raise FileExistsError("Packet already exists; use a new directory, never overwrite it")
    source = directory.parent / "diagnosis-v12-h01-h20/cases.json"
    previous, _ = load_cases(source)
    cases, origins, rules, obligations = [], [], {}, []
    for number, old in enumerate(previous, 1):
        raw = copy.deepcopy(old.model_dump(mode="json"))
        factor, days = 3 + number % 3, 61
        mapping = {p["sku"]: f"T{number:02}{i}" for i, p in enumerate(raw["sales"]["products"], 1)}

        def shift(value):
            return str(date.fromisoformat(value) + timedelta(days=days))

        for product in raw["sales"]["products"]:
            product["sku"], product["active_from"] = (
                mapping[product["sku"]],
                shift(product["active_from"]),
            )
        for row in raw["sales"]["daily_sales"]:
            row["sku"], row["day"] = mapping[row["sku"]], shift(row["day"])
            for field in ("paid_units", "refunded_units", "revenue_cents", "refund_cents"):
                row[field] *= factor
        for name in ("period", "comparison_period"):
            raw[name] = {k: shift(value) for k, value in raw[name].items()}
        raw["scope_skus"] = [mapping[sku] for sku in old.scope_skus]
        for before, after in mapping.items():
            raw["query"] = raw["query"].replace(before, after)
        if raw["harness"]["clarification_choice"]:
            raw["harness"]["clarification_choice"] = mapping[raw["harness"]["clarification_choice"]]
        if raw["inventory"]:
            raw["inventory"]["snapshot_day"] = shift(raw["inventory"]["snapshot_day"])
            for row in raw["inventory"]["items"]:
                row["sku"], row["available_units"] = (
                    mapping[row["sku"]],
                    row["available_units"] * factor,
                )
                if row.get("snapshot_day"):
                    row["snapshot_day"] = shift(row["snapshot_day"])
        if number == 1:
            # Three literal objects create 27 monetary obligations. This does
            # not force a provider budget failure or script a model proposal.
            first = raw["sales"]["products"][0]
            raw["sales"]["products"].append({**first, "sku": "T013", "name": "核对商品三"})
            raw["sales"]["daily_sales"].extend(
                [
                    {
                        **row,
                        "sku": "T013",
                        **{
                            field: row[field] * 2
                            for field in (
                                "paid_units",
                                "refunded_units",
                                "revenue_cents",
                                "refund_cents",
                            )
                        },
                    }
                    for row in list(raw["sales"]["daily_sales"])
                    if row["sku"] == first["sku"]
                ]
            )
            raw["scope_skus"].append("T013")
            raw["query"] = (
                "请对每个授权商品分别列出支付金额、退款金额和净销售额的本期值、比较期值及差额；按商品核对净销售额变化贡献并对账。若本轮未完成，请完整保留未完成项，并注明总数、已展示数和省略数。"
            )
        if number == 12:
            raw["query"] = (
                "请逐商品列出两期净销售额及差额，核对净销售额变化最大商品的贡献，列明其余商品贡献并对账。"
            )
        if number == 13:
            raw["query"] = (
                "请分别列出每个授权商品两期净销售额汇总，并核对所有授权商品两期每日净销售额序列及每个适用日期的日覆盖。"
            )
        if number == 9:
            raw["query"] = "找出两期净销售额下降最多的两个品类，列出正项和负项并对账总体差额。"
        if number == 14:
            raw["scope_skus"] = list(mapping.values())
            raw["facet"] = "excluded_per_sku_counts"
            raw["query"] = (
                "商品 T141 除外，请分别列出其余授权商品两期净销售额、支付件数、退款件数及差额。"
            )
            raw["expected"]["required_tools"] = ["compare_periods"]
        if number == 15:
            raw["scope_skus"] = list(mapping.values())
            raw["facet"] = "per_sku_refund_counts"
            raw["query"] = (
                "请对每个授权商品分别列出本期与比较期的支付件数、退款件数及差额，并核对两期记账退款占比；不用计算利润。"
            )
        for doc in raw["documents"]:
            doc["text"] = "本轮核对修订辛。" + doc["text"]
        if number == 3:
            raw["query"] = (
                "请明确列出授权范围两期支付金额、退款金额、净销售额及各自差额，并核对记账恒等式。"
            )
        if number in (6, 7, 17):
            raw["expected"]["stop_reasons"] = ["evidence_gap"]
        elif raw["expected"]["positive"]:
            raw["expected"]["stop_reasons"] = ["target_covered"]
        raw["expected"].update(reference_answer(Case.model_validate(raw)))
        case = Case.model_validate(raw)
        cases.append(case.model_dump(mode="json"))
        obligations.append(obligation(number, case))
        rules[case.case_id] = {
            "dimension": "each_sku_daily"
            if number == 13
            else "category"
            if number == 9
            else "sku"
            if number in (1, 4, 8, 12)
            else "selected_scope",
            "min_skus": len(case.scope_skus),
            "max_skus": len(case.scope_skus),
            "sources": case.sources,
            "required_assertions": case.expected.required_assertions,
            "expected_stop_reasons": case.expected.stop_reasons,
            "request_kind": "boundary"
            if number in (6, 7, 17)
            else "unsupported"
            if number in (10, 11)
            else "values",
            "answer_statuses": ["boundary_explained"]
            if number in (6, 7, 17)
            else ["incomplete"]
            if number in (10, 11)
            else ["fulfilled"],
        }
        origins.append(
            {
                "case_id": case.case_id,
                "source_case": old.case_id,
                "scale_factor": factor,
                "date_shift_days": days,
                "sku_mapping": mapping,
                "added_skus": ["T013"] if number == 1 else [],
                "input_hash": input_hash(case),
            }
        )
    seen, count = set(), 0
    for path in (ROOT / "evaluations/results").rglob("business-input.json"):
        value = json.loads(path.read_bytes())
        value.pop("query", None)
        seen.add(digest(value))
        count += 1
    if {origin["input_hash"] for origin in origins} & seen:
        raise ValueError("Authoring would reuse executed business data")
    answer = {
        "schema_version": 1,
        "author": "AI code author, not external blind acceptance",
        "derivation": "literal author scope and integer raw rows; never production intent",
        "model_or_graph_executions": 0,
        "cases": obligations,
    }
    save(directory / "answer-obligations.json", answer)
    save(
        directory / "semantic-contract.json",
        {
            "schema_version": 2,
            "derivation": "literal author scope, independent of production parser",
            "cases": rules,
            "author_obligations": answer,
            "expected_fixed_runtime": {
                "graph_version": "commerce-review-bundle-v4",
                "schema_version": "1",
                "prompt_version": "commerce-review-v4",
                "tool_registry_version": "commerce-review-bundle-v4",
            },
            "expected_runtime": {
                "graph_version": "diagnosis-v15",
                "schema_version": "4",
                "prompt_version": "diagnosis-v15",
                "tool_registry_version": "6",
            },
        },
    )
    save(
        directory / "cases.json",
        {
            "schema_version": 1,
            "oracle": "independent_raw_rows_v1",
            "splits": {"development": 0, "regression": 0, "holdout": 20},
            "cases": cases,
        },
    )
    _, contract = validate_cases(directory / "cases.json")
    save(
        directory / "provenance.json",
        {
            "source": str(source.relative_to(ROOT)),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "author": answer["author"],
            "model_or_graph_executions_during_authoring": 0,
            "origins": origins,
            "executed_receipts_checked": count,
            "distinct_executed_data_hashes": len(seen),
            "exact_reuse": False,
            "input_hashes": contract["input_hashes"],
            "limitations": "Synthetic development-family variants. New hashes do not establish semantic independence. H14 replaces the flat-daily case with excluded-object/count coverage; H13 retains multi-SKU daily coverage.",
        },
    )
    print(
        json.dumps(
            {
                "prepared_cases": len(cases),
                "total_planned_tasks": contract["total_tasks"],
                "executed_inputs_checked": count,
                "model_or_graph_executions": 0,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
