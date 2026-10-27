"""Source-only author-ledger checks. Never execute a fresh graph, tool or model."""

import json
from datetime import date, timedelta
from pathlib import Path

from evaluations.diagnosis_oracle import raw_totals
from evaluations.diagnosis_plan import validate_cases
from evaluations.diagnosis_request_checks_v2 import expected_entities, requested_dimensions


def require(value, message):
    if not value:
        raise ValueError(message)


def check_packet(path, used_inputs=()):
    path = Path(path)
    cases, contract = validate_cases(path, used_inputs)
    semantic = json.loads(path.with_name("semantic-contract.json").read_bytes())
    author = json.loads(path.with_name("answer-obligations.json").read_bytes())
    require(
        semantic["schema_version"] == 5 and semantic["author_obligations"] == author,
        "formal_schema5_author_binding_required",
    )
    require(contract["total_tasks"] == 96, "formal_requires_20_cases_96_slots")
    totals = {"raw_matrix": 0, "scope_matrix": 0, "daily_matrix": 0, "contribution_groups": 0}
    for c, a in zip(cases, author["cases"], strict=True):
        raw = c.model_dump(mode="json")
        selected = (
            [c.harness.clarification_choice]
            if c.harness.clarification_choice
            else expected_entities(c.query, c.scope_skus, raw["sales"]["products"])
        )
        require(
            a["case_id"] == c.case_id
            and a["query"] == c.query
            and a["final_skus"] == selected
            and a["authorized_skus"] == c.scope_skus,
            "formal_author_scope_or_query_mismatch",
        )
        require(
            requested_dimensions(c.query)
            == ([a["contribution_dimension"]] if a["contribution_dimension"] else []),
            "formal_author_dimension_mismatch",
        )
        for table in ["raw_matrix", "scope_matrix"]:
            keys = [(x["sku"], x["metric"], x["part"]) for x in a[table]]
            require(len(keys) == len(set(keys)), "duplicate_author_obligation")
            for x in a[table]:
                spans = (
                    ["period", "comparison_period"]
                    if x["part"] == "delta"
                    else ["period" if x["part"] == "current" else "comparison_period"]
                )
                members = selected if x["sku"] == ",".join(sorted(selected)) else [x["sku"]]
                parts = [raw_totals(raw["sales"], members, raw[p]) for p in spans]
                applicable = all(cov["expected_records"] > 0 for _, cov in parts)
                complete = applicable and all(cov["complete"] for _, cov in parts)
                value = (
                    parts[0][0][x["metric"]] - (parts[1][0][x["metric"]] if len(parts) == 2 else 0)
                    if complete
                    else None
                )
                count = x["metric"].endswith("units")
                require(
                    x["raw_integer"] == value
                    and x["unit"] == ("count" if count else "CNY")
                    and x["scale"] == (1 if count else 100),
                    "formal_author_integer_or_unit_mismatch",
                )
                require(
                    x["status"]
                    == (
                        "required"
                        if complete
                        else "not_applicable"
                        if not applicable
                        else "missing_source"
                    ),
                    "formal_author_availability_mismatch",
                )
            totals[table] += len(a[table])
        daily = set()
        if semantic["cases"][c.case_id]["dimension"] == "each_sku_daily":
            for sku in selected:
                for span in ["period", "comparison_period"]:
                    first, last = (date.fromisoformat(raw[span][k]) for k in ["start", "end"])
                    while first <= last:
                        daily.add((sku, span, str(first)))
                        first += timedelta(days=1)
        actual = [(x["sku"], x["period"], x["day"]) for x in a["daily_matrix"]]
        require(
            len(actual) == len(set(actual)) and set(actual) == daily,
            "formal_daily_inventory_incomplete",
        )
        for x in a["daily_matrix"]:
            values, cov = raw_totals(raw["sales"], [x["sku"]], {"start": x["day"], "end": x["day"]})
            require(
                x["net_cents"] == values["net_revenue"] and x["coverage"] == cov,
                "formal_daily_oracle_mismatch",
            )
        totals["daily_matrix"] += len(actual)
        groups = {}
        if a["contribution_dimension"]:
            for p in raw["sales"]["products"]:
                if p["sku"] in selected:
                    groups.setdefault(p[a["contribution_dimension"]], []).append(p["sku"])
        require(
            {x["entity"]: x["skus"] for x in a["raw_contributions"]}
            == dict(sorted(groups.items())),
            "formal_contribution_inventory_mismatch",
        )
        for x in a["raw_contributions"]:
            now, cc = raw_totals(raw["sales"], x["skus"], raw["period"])
            prior, pc = raw_totals(raw["sales"], x["skus"], raw["comparison_period"])
            require(
                x["delta_cents"]
                == (
                    now["net_revenue"] - prior["net_revenue"]
                    if cc["complete"] and pc["complete"]
                    else None
                ),
                "formal_contribution_oracle_mismatch",
            )
        totals["contribution_groups"] += len(groups)
        for members, key in [
            (selected, "selected_aggregate_expected"),
            (c.scope_skus, "authorized_aggregate_expected"),
        ]:
            now, cc = raw_totals(raw["sales"], members, raw["period"])
            prior, pc = raw_totals(raw["sales"], members, raw["comparison_period"])
            comparable = all(cov["complete"] and cov["expected_records"] > 0 for cov in [cc, pc])
            expected = {
                "net_cents": now["net_revenue"],
                "comparison_net_cents": prior["net_revenue"],
                "delta_cents": now["net_revenue"] - prior["net_revenue"] if comparable else None,
                "comparable": comparable,
            }
            require(a[key] == expected, "formal_aggregate_oracle_mismatch")
    return cases, semantic, {**contract, "author_oracle_items": totals}
