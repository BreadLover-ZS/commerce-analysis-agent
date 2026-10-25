"""Zero-HTTP preflight: frozen source, author obligations, independent integer rows."""

import argparse
import hashlib
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from app.diagnosis.inputs import digest  # noqa: E402
from evaluations.diagnosis_cases import load_cases  # noqa: E402
from evaluations.diagnosis_grading import VERSION_CONTRACTS  # noqa: E402
from evaluations.diagnosis_plan import check, input_hash  # noqa: E402
from evaluations.diagnosis_request_checks import (  # noqa: E402
    expected_entities,
    requested_dimension,
    requested_items,
)

VERSIONS = {
    "graph_version": "diagnosis-v13",
    "schema_version": "3",
    "prompt_version": "diagnosis-v13",
    "tool_registry_version": "6",
}
METRICS = {
    "H04": ["net_revenue"],
    "H08": ["net_revenue"],
    "H13": ["net_revenue"],
    "H01": ["gross_revenue", "refund_amount", "net_revenue"],
    "H14": ["net_revenue", "paid_units", "refunded_units"],
    "H15": ["paid_units", "refunded_units"],
}
DIMENSIONS = {"H01": "sku", "H04": "sku", "H08": "sku", "H09": "category", "H12": "sku"}
MANDATORY = [
    "final_scope",
    "requested_dimension_bound",
    "requested_per_entity_items",
    "complete_gap_inventory",
]


def need(condition, message):
    if not condition:
        raise ValueError(message)


def rows(case, skus, span):
    """Direct sums and calendar counts, separate from app and authoring oracle."""
    values = [
        r
        for r in case["sales"]["daily_sales"]
        if r["sku"] in skus and span["start"] <= r["day"] <= span["end"]
    ]
    counts = sum(
        max(
            0,
            (
                date.fromisoformat(span["end"])
                - max(date.fromisoformat(span["start"]), date.fromisoformat(p["active_from"]))
            ).days
            + 1,
        )
        for p in case["sales"]["products"]
        if p["sku"] in skus
    )
    sums = {
        name: sum(r[field] for r in values)
        for name, field in (
            ("gross_revenue", "revenue_cents"),
            ("refund_amount", "refund_cents"),
            ("paid_units", "paid_units"),
            ("refunded_units", "refunded_units"),
        )
    }
    sums["net_revenue"] = sums["gross_revenue"] - sums["refund_amount"]
    return sums, {
        "expected_records": counts,
        "observed_records": len(values),
        "missing_records": counts - len(values),
        "complete": counts == len(values),
    }


def verify(directory):
    directory = Path(directory)
    frozen = check(directory)
    semantic = json.loads((directory / "semantic-contract.json").read_bytes())
    answer = json.loads((directory / "answer-obligations.json").read_bytes())
    provenance = json.loads((directory / "provenance.json").read_bytes())
    need(
        semantic["expected_runtime"] == VERSIONS == VERSION_CONTRACTS["diagnosis-v13"],
        "runtime version changed",
    )
    need(
        semantic["author_obligations"] == answer,
        "author obligations differ from frozen semantic contract",
    )
    need(answer["model_or_graph_executions"] == 0, "packet authoring execution claim changed")
    cases, _ = load_cases(directory / "cases.json")
    by_id = {row["case_id"]: row for row in answer["cases"]}
    need(len(by_id) == len(answer["cases"]) == len(cases) == 20, "missing or duplicate author case")
    totals = {"component_items": 0, "daily_items": 0, "contribution_groups": 0}
    for case in cases:
        raw = case.model_dump(mode="json")
        label = case.case_id
        row = by_id[label]
        # Author scope is literal and separate from both language adapters.
        selected = ["P142"] if label == "H14" else ["P161"] if label == "H16" else case.scope_skus
        need(
            row["authorized_skus"] == case.scope_skus and row["final_skus"] == selected,
            f"{label}: scope contract changed",
        )
        need(
            row["query"] == case.query
            and row["period"] == raw["period"]
            and row["comparison_period"] == raw["comparison_period"],
            f"{label}: question/period mismatch",
        )
        need(
            row["mandatory_v13_assertions"] == MANDATORY,
            f"{label}: mandatory v13 assertions changed",
        )
        need(
            row["required_assertions"] == case.expected.required_assertions,
            f"{label}: author assertion drift",
        )
        need(
            row["gap_inventory_policy"]
            == {
                "all_pending_items_required": True,
                "display_limit": 20,
                "if_more_than_20": "full unique details, exact total/displayed/omitted, visible omission disclosure",
                "force_budget_failure": False,
            },
            f"{label}: gap inventory policy changed",
        )
        if label != "H16":
            need(
                expected_entities(case.query, case.scope_skus) == selected,
                f"{label}: independent grader cannot bind author entities",
            )
        need(
            requested_dimension(case.query)
            == DIMENSIONS.get(label)
            == row["contribution_dimension"],
            f"{label}: wrong requested dimension",
        )
        need(
            row["expected_stop_reasons"] == case.expected.stop_reasons,
            f"{label}: stop contract mismatch",
        )
        need(
            not case.expected.positive or case.expected.stop_reasons == ["target_covered"],
            f"{label}: positive cases require full completion",
        )
        metrics = METRICS.get(label, [])
        need(row["component_metrics"] == metrics, f"{label}: component metrics changed")
        requested = [
            (sku, metric, part)
            for sku in selected
            for metric in metrics
            for part in (
                ("current", "comparison") if label == "H13" else ("current", "comparison", "delta")
            )
        ]
        need(
            set(requested_items(case.query, selected)) == set(requested),
            f"{label}: independent grader item coverage mismatch",
        )
        items = row["raw_matrix"]
        need(
            [(i["sku"], i["metric"], i["part"]) for i in items] == requested,
            f"{label}: missing/duplicate component identity",
        )
        for item in items:
            part = item["part"]
            periods = (
                ["period", "comparison_period"]
                if part == "delta"
                else ["period"]
                if part == "current"
                else ["comparison_period"]
            )
            raw_values = [rows(raw, [item["sku"]], raw[p]) for p in periods]
            applicable = all(c["expected_records"] > 0 for _, c in raw_values)
            complete = applicable and all(c["complete"] for _, c in raw_values)
            wanted = (
                (
                    raw_values[0][0][item["metric"]] - raw_values[1][0][item["metric"]]
                    if len(periods) == 2
                    else raw_values[0][0][item["metric"]]
                )
                if complete
                else None
            )
            count = item["metric"].endswith("units")
            need(
                item["raw_integer"] == wanted
                and item["unit"] == ("count" if count else "CNY")
                and item["scale"] == (1 if count else 100),
                f"{label}: integer value/unit mismatch",
            )
            need(
                item["status"]
                == (
                    "required"
                    if complete
                    else "not_applicable"
                    if not applicable
                    else "missing_source"
                ),
                f"{label}: source applicability mismatch",
            )
        for scope, key in (
            (selected, "selected_aggregate_expected"),
            (case.scope_skus, "authorized_aggregate_expected"),
        ):
            current, prior = [rows(raw, scope, raw[p]) for p in ("period", "comparison_period")]
            comparable = all(
                c["expected_records"] > 0 and c["complete"] for _, c in [current, prior]
            )
            expected = {
                "net_cents": current[0]["net_revenue"],
                "comparison_net_cents": prior[0]["net_revenue"],
                "comparable": comparable,
                "delta_cents": current[0]["net_revenue"] - prior[0]["net_revenue"]
                if comparable
                else None,
            }
            need(row[key] == expected, f"{label}: aggregate raw expectation mismatch")
        daily = []
        if label == "H13":
            for sku in selected:
                for period in ("period", "comparison_period"):
                    day, end = (
                        date.fromisoformat(raw[period]["start"]),
                        date.fromisoformat(raw[period]["end"]),
                    )
                    while day <= end:
                        value, coverage = rows(raw, [sku], {"start": str(day), "end": str(day)})
                        daily.append(
                            {
                                "sku": sku,
                                "period": period,
                                "day": str(day),
                                "net_cents": value["net_revenue"],
                                "coverage": coverage,
                            }
                        )
                        day += timedelta(days=1)
        need(row["daily_matrix"] == daily, f"{label}: daily identities or values missing")
        contributions = []
        if DIMENSIONS.get(label):
            dimension = DIMENSIONS[label]
            groups = {}
            for product in raw["sales"]["products"]:
                if product["sku"] in selected:
                    groups.setdefault(product[dimension], []).append(product["sku"])
            for entity, skus in sorted(groups.items()):
                current, prior = [rows(raw, skus, raw[p]) for p in ("period", "comparison_period")]
                contributions.append(
                    {
                        "entity": entity,
                        "dimension": dimension,
                        "skus": skus,
                        "delta_cents": current[0]["net_revenue"] - prior[0]["net_revenue"]
                        if current[1]["complete"] and prior[1]["complete"]
                        else None,
                    }
                )
        need(
            row["raw_contributions"] == contributions,
            f"{label}: contribution dimension/group/value mismatch",
        )
        totals["component_items"] += len(items)
        totals["daily_items"] += len(daily)
        totals["contribution_groups"] += len(contributions)
    need(
        len(by_id["H01"]["raw_matrix"]) == 27 and len(by_id["H15"]["raw_matrix"]) == 12,
        "repair coverage requires 27 monetary and 12 count items",
    )
    need(
        next(c for c in cases if c.case_id == "H14").expected.baseline_capability == "additional",
        "excluded scope must not enter unfair fixed pairing",
    )
    need(frozen["total_tasks"] == 96, "formal paired size changed")
    need(
        provenance["input_hashes"] == [input_hash(c) for c in cases]
        and len(provenance["origins"]) == 20,
        "provenance input hashes drifted",
    )
    source = ROOT / provenance["source"]
    need(
        hashlib.sha256(source.read_bytes()).hexdigest() == provenance["source_sha256"],
        "author source packet changed",
    )
    seen, count = set(), 0
    for path in (ROOT / "evaluations/results").rglob("business-input.json"):
        raw = json.loads(path.read_bytes())
        raw.pop("query", None)
        seen.add(digest(raw))
        count += 1
    need(
        not seen.intersection(input_hash(c) for c in cases),
        "formal packet already executed; no longer fresh",
    )
    sums = directory / "SHA256SUMS"
    if sums.exists():
        for line in sums.read_text().splitlines():
            value, filename = line.split("  ", 1)
            path = directory / filename
            need(
                path.parent == directory and hashlib.sha256(path.read_bytes()).hexdigest() == value,
                "packet checksum mismatch: " + filename,
            )
    return {
        **frozen,
        "author_obligations_verified": True,
        "independent_integer_rows_verified": True,
        "component_items": totals["component_items"],
        "daily_items": totals["daily_items"],
        "contribution_groups": totals["contribution_groups"],
        "executed_inputs_checked": count,
        "distinct_executed_data_hashes": len(seen),
        "fresh_input_overlap": 0,
        "formal_packet_executions": 0,
        "provider_requests_this_check": 0,
        "release_eligible": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    print(json.dumps(verify(args.directory), ensure_ascii=False, indent=2, allow_nan=False))
