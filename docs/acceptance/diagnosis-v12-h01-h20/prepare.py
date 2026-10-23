"""Author fresh inputs and a scope contract; never execute a graph or a model."""

import copy
import hashlib
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from evaluations.diagnosis_cases import Case, load_cases, reference_answer  # noqa: E402
from evaluations.diagnosis_oracle import raw_totals  # noqa: E402
from evaluations.diagnosis_plan import input_hash, validate_cases  # noqa: E402


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main():
    directory = Path(__file__).resolve().parent
    if (directory / "cases.json").exists():
        raise FileExistsError("Fresh packet is immutable; use a new directory")
    source = directory.parent / "diagnosis-v11-h01-h20/cases.json"
    previous, _ = load_cases(source)
    cases, origins, rules, obligations = [], [], {}, []
    for number, old in enumerate(previous, 1):
        c = copy.deepcopy(old.model_dump(mode="json"))
        factor, days = number + 2, 11
        mapping = {p["sku"]: f"W{number:02}{i}" for i, p in enumerate(c["sales"]["products"], 1)}

        def shift(day):
            return str(date.fromisoformat(day) + timedelta(days=days))

        for p in c["sales"]["products"]:
            p["sku"], p["active_from"] = mapping[p["sku"]], shift(p["active_from"])
        for row in c["sales"]["daily_sales"]:
            row["sku"], row["day"] = mapping[row["sku"]], shift(row["day"])
            for field in ("paid_units", "refunded_units", "revenue_cents", "refund_cents"):
                row[field] *= factor
        for name in ("period", "comparison_period"):
            c[name] = {k: shift(v) for k, v in c[name].items()}
        c["scope_skus"] = [mapping[sku] for sku in old.scope_skus]
        for a, b in mapping.items():
            c["query"] = c["query"].replace(a, b)
        if c["harness"]["clarification_choice"]:
            c["harness"]["clarification_choice"] = mapping[c["harness"]["clarification_choice"]]
        if c["inventory"]:
            c["inventory"]["snapshot_day"] = shift(c["inventory"]["snapshot_day"])
            for row in c["inventory"]["items"]:
                row["sku"], row["available_units"] = (
                    mapping[row["sku"]],
                    row["available_units"] * factor,
                )
                if row.get("snapshot_day"):
                    row["snapshot_day"] = shift(row["snapshot_day"])
        if number == 13:
            # Intended multi-SKU daily scope is explicit, independent of the
            # development source's single-SKU scope and the production parser.
            c["scope_skus"] = list(mapping.values())
            c["expected"]["required_tools"] = ["drilldown_series"]
            c["expected"]["required_assertions"].append("multi_sku_daily_covered")
        if number in (19, 20):
            for doc in c["documents"]:
                doc["text"] = "本轮业务核对修订己。" + doc["text"]
        c["expected"].update(reference_answer(Case.model_validate(c)))
        case = Case.model_validate(c)
        cases.append(case.model_dump(mode="json"))
        origins.append(
            {
                "case_id": c["case_id"],
                "source_case": old.case_id,
                "scale_factor": factor,
                "date_shift_days": days,
                "sku_mapping": mapping,
                "input_hash": input_hash(case),
            }
        )
        rules[c["case_id"]] = {
            "dimension": "each_sku_daily" if number == 13 else "authorized_scope",
            "min_skus": 2 if number == 13 else len(c["scope_skus"]),
            "max_skus": len(c["scope_skus"]),
            "sources": c["sources"],
            "required_assertions": c["expected"]["required_assertions"],
        }
        matrix = []
        # Independent row arithmetic, not production request_contract().
        for sku in c["scope_skus"]:
            current, cc = raw_totals(c["sales"], [sku], c["period"])
            prior, pc = raw_totals(c["sales"], [sku], c["comparison_period"])
            if number == 1:
                for metric in ("gross_revenue", "refund_amount", "net_revenue"):
                    for part, value in (
                        ("current", current[metric]),
                        ("comparison", prior[metric]),
                        ("delta", current[metric] - prior[metric]),
                    ):
                        matrix.append(
                            {"sku": sku, "metric": metric, "part": part, "value_cents": value}
                        )
            if number == 13:
                for name in ("period", "comparison_period"):
                    day, last = (
                        date.fromisoformat(c[name]["start"]),
                        date.fromisoformat(c[name]["end"]),
                    )
                    while day <= last:
                        totals, coverage = raw_totals(
                            c["sales"], [sku], {"start": str(day), "end": str(day)}
                        )
                        matrix.append(
                            {
                                "sku": sku,
                                "period": name,
                                "day": str(day),
                                "net_cents": totals["net_revenue"],
                                "coverage": coverage,
                            }
                        )
                        day += timedelta(days=1)
        obligations.append(
            {
                "case_id": c["case_id"],
                "query": c["query"],
                "authorized_skus": c["scope_skus"],
                "final_skus": [c["harness"]["clarification_choice"]]
                if c["harness"]["clarification_choice"]
                else c["scope_skus"],
                "period": c["period"],
                "comparison_period": c["comparison_period"],
                "required_tools_or_equivalent": c["expected"]["required_tools"],
                "required_assertions": c["expected"]["required_assertions"],
                "raw_matrix": matrix,
                "aggregate_expected": reference_answer(case),
            }
        )
    save(
        directory / "semantic-contract.json",
        {
            "schema_version": 1,
            "derivation": "Author intended scope, independent of production parser",
            "cases": rules,
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
    save(
        directory / "answer-obligations.json",
        {
            "derivation": "Author literal obligations and independent raw rows; no production intent adapter",
            "model_or_graph_executions": 0,
            "cases": obligations,
        },
    )
    checked, contract = validate_cases(directory / "cases.json")
    # Exact data reuse check across EVERY locally executed receipt, without
    # importing or invoking any Worker/model/graph.
    seen, count = set(), 0
    for path in (ROOT / "evaluations/results").glob("**/business-input.json"):
        raw = json.loads(path.read_bytes())
        raw.pop("query", None)
        from app.diagnosis.inputs import digest

        seen.add(digest(raw))
        count += 1
    if set(contract["input_hashes"]) & seen:
        raise ValueError("New packet reuses executed business data")
    save(
        directory / "provenance.json",
        {
            "source": str(source.relative_to(ROOT)),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "author": "AI code author; not external blind acceptance",
            "model_or_graph_executions_during_authoring": 0,
            "origins": origins,
            "executed_receipts_checked": count,
            "distinct_executed_data_hashes": len(seen),
            "exact_reuse": False,
            "limitations": "Synthetic development-family variants. Different hashes do not establish semantic independence.",
        },
    )
    print(
        f"Prepared {len(checked)} fresh cases; graph/model executions=0; {count} executed inputs checked"
    )


if __name__ == "__main__":
    main()
