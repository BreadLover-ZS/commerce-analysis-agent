"""Second implementation of numeric truth for evaluation only.

Uses raw fixture dictionaries and stdlib arithmetic. Does not import the production
calculation kernel, tools, report validator, or expected case labels.
"""

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, DecimalException

FIELDS = {
    "gross_revenue": "revenue_cents",
    "refund_amount": "refund_cents",
    "paid_units": "paid_units",
    "refunded_units": "refunded_units",
}


def fixed(value):
    return str(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def raw_totals(sales, skus, span):
    first, last = (date.fromisoformat(span[k]) for k in ("start", "end"))
    rows = [
        r
        for r in sales["daily_sales"]
        if r["sku"] in skus and first <= date.fromisoformat(r["day"]) <= last
    ]
    expected = sum(
        max(0, (last - max(first, date.fromisoformat(p["active_from"]))).days + 1)
        for p in sales["products"]
        if p["sku"] in skus
    )
    values = {name: sum(r[column] for r in rows) for name, column in FIELDS.items()}
    values["net_revenue"] = values["gross_revenue"] - values["refund_amount"]
    return values, {
        "expected_records": expected,
        "observed_records": len(rows),
        "missing_records": expected - len(rows),
        "complete": len(rows) == expected,
    }


def expected_numeric(sales, bundle, tool, arguments, result, inventory=None):
    """Return expected facts and structural invariants, derived from raw records."""
    selected = arguments.get("filters", {}).get("skus") or bundle["allowed_skus"]
    categories = arguments.get("filters", {}).get("categories", [])
    if set(selected) - set(bundle["allowed_skus"]) or set(categories) - set(
        bundle["allowed_categories"]
    ):
        raise ValueError("unauthorized raw filters")
    selected = sorted(
        p["sku"]
        for p in sales["products"]
        if p["sku"] in selected and (not categories or p["category"] in categories)
    )
    if not selected or set(selected) - set(bundle["allowed_skus"]):
        raise ValueError("unauthorized oracle scope")
    periods = {"current": bundle["period"], "comparison": bundle["comparison_period"]}
    totals, coverage = {}, {}
    for name, span in periods.items():
        totals[name], coverage[name] = raw_totals(sales, selected, span)
    comparable = all(c["complete"] and c["expected_records"] > 0 for c in coverage.values())
    facts, invariants = {}, {}

    def put(
        key,
        value,
        unit,
        *,
        skus=None,
        entity_type="scope",
        entity_id=None,
        span=None,
        snapshot=None,
    ):
        facts[key] = {
            "value": fixed(Decimal(value) / 100) if unit == "CNY" else str(value),
            "raw_value": value,
            "unit": unit,
            "entity_type": entity_type,
            "entity_id": entity_id or ",".join(skus or selected),
            "period": span or bundle["period"],
            "comparison_period": bundle["comparison_period"],
        }
        if tool == "inventory_risk":
            facts[key]["snapshot_as_of"] = snapshot

    def compare(metric):
        unit = "CNY" if metric in {"net_revenue", "gross_revenue", "refund_amount"} else "count"
        current, previous = (totals[name][metric] for name in periods)
        for name, span in periods.items():
            put(f"{metric}.{name}", totals[name][metric], unit, span=span)
        if comparable:
            put(f"{metric}.delta", current - previous, unit)
            if previous > 0:
                put(
                    f"{metric}.change_pct",
                    fixed(Decimal(current - previous) * 100 / previous),
                    "percent",
                )

    if tool in {
        "inspect_dataset",
        "compare_periods",
        "rank_contributors",
        "drilldown_series",
        "analyze_refunds",
    }:
        invariants.update(coverage=coverage, comparable=comparable, scope={"skus": selected})
    if tool == "inspect_dataset":
        pass
    elif tool == "compare_periods":
        compare(arguments.get("metric", "net_revenue"))
    elif tool == "analyze_refunds":
        for metric in FIELDS:
            compare(metric)
        for name, span in periods.items():
            denominator = totals[name]["paid_units"]
            if denominator > 0:
                put(
                    f"refund_ratio.{name}",
                    fixed(Decimal(totals[name]["refunded_units"]) * 100 / denominator),
                    "percent",
                    span=span,
                )
        current, previous = (totals[name] for name in periods)
        increasing = (
            (
                current["refunded_units"] * previous["paid_units"]
                > previous["refunded_units"] * current["paid_units"]
            )
            if comparable and current["paid_units"] and previous["paid_units"]
            else None
        )
        invariants.update(
            ratio_increased=increasing,
            denominator_effect=bool(
                increasing
                and current["refunded_units"] <= previous["refunded_units"]
                and current["paid_units"] < previous["paid_units"]
            ),
            refund_amount_delta_cents=current["refund_amount"] - previous["refund_amount"]
            if comparable
            else None,
        )
    elif tool == "rank_contributors":
        groups = {}
        dimension = arguments.get("dimension", "sku")
        for product in sales["products"]:
            if product["sku"] in selected:
                groups.setdefault(product[dimension], []).append(product["sku"])
        ordered = []
        if comparable:
            total = totals["current"]["net_revenue"] - totals["comparison"]["net_revenue"]
            for index, (entity, skus) in enumerate(sorted(groups.items())):
                current, previous = (
                    raw_totals(sales, skus, span)[0]["net_revenue"] for span in periods.values()
                )
                delta = current - previous
                key = f"contribution.{index}"
                put(key, delta, "CNY", entity_type=dimension, entity_id=entity)
                share = fixed(Decimal(delta) * 100 / total) if total else None
                if share is not None:
                    put(key + ".share", share, "percent", entity_type=dimension, entity_id=entity)
                ordered.append(
                    {
                        "entity": entity,
                        "delta_cents": delta,
                        "fact_key": key,
                        "share_of_total_pct": share,
                        "share_fact_key": key + ".share" if share is not None else None,
                    }
                )
            ordered.sort(key=lambda r: (-abs(r["delta_cents"]), r["entity"]))
            shown = ordered[: arguments.get("top_k", 5)]
            other = total - sum(r["delta_cents"] for r in shown)
            put("contribution.other", other, "CNY", entity_id="other")
            put("contribution.total", total, "CNY")
            invariants["contributors"] = shown
            invariants["reconciliation"] = {
                "total_delta_cents": total,
                "shown_delta_cents": total - other,
                "other_delta_cents": other,
                "full_delta_cents": sum(r["delta_cents"] for r in ordered),
                "balanced": sum(r["delta_cents"] for r in ordered) == total,
            }
        else:
            invariants.update(contributors=[], reconciliation=None)
        invariants["all_contributors"] = ordered
        if result.get("tool_version") in {"5", "6"}:
            component_keys, component_coverage = [], {}
            for sku in selected:
                values = {name: raw_totals(sales, [sku], span) for name, span in periods.items()}
                component_coverage[sku] = {name: pair[1] for name, pair in values.items()}
                for metric in (
                    (
                        "net_revenue",
                        "gross_revenue",
                        "refund_amount",
                        "paid_units",
                        "refunded_units",
                    )
                    if result.get("tool_version") == "6"
                    else ("net_revenue", "gross_revenue", "refund_amount")
                ):
                    for part, (value, covered) in values.items():
                        if covered["observed_records"]:
                            key = f"component.{sku}.{metric}.{part}"
                            put(
                                key,
                                value[metric],
                                "count" if metric in {"paid_units", "refunded_units"} else "CNY",
                                entity_type="sku",
                                entity_id=sku,
                                span=periods[part],
                            )
                            component_keys.append(key)
                    if comparable:
                        key = f"component.{sku}.{metric}.delta"
                        put(
                            key,
                            values["current"][0][metric] - values["comparison"][0][metric],
                            "count" if metric in {"paid_units", "refunded_units"} else "CNY",
                            entity_type="sku",
                            entity_id=sku,
                        )
                        component_keys.append(key)
            invariants.update(
                component_fact_keys=component_keys, component_coverage=component_coverage
            )
    elif tool == "drilldown_series":
        expected_points = []
        for name, span in periods.items():
            day, last = (date.fromisoformat(span[k]) for k in ("start", "end"))
            while day <= last:
                single = {"start": str(day), "end": str(day)}
                values, covered = raw_totals(sales, selected, single)
                status = (
                    "not_active"
                    if not covered["expected_records"]
                    else "missing"
                    if not covered["complete"]
                    else "zero"
                    if not any(values.values())
                    else "recorded"
                )
                keys = {}
                if covered["observed_records"]:
                    for metric in ("net_revenue", *FIELDS):
                        key = f"series.{name}.{day}.{metric}"
                        unit = (
                            "CNY"
                            if metric in {"net_revenue", "gross_revenue", "refund_amount"}
                            else "count"
                        )
                        put(key, values[metric], unit, span=single)
                        keys[metric] = key
                expected_points.append(
                    {
                        "day": str(day),
                        "period_name": name,
                        "coverage": covered,
                        "status": status,
                        "fact_keys": keys,
                    }
                )
                day += timedelta(days=1)
        invariants["series"] = expected_points
    elif tool == "inventory_risk":
        if inventory is None:
            raise ValueError("missing raw stock fixture")
        # Corpus may freeze one snapshot for all rows; the persisted CSV contract
        # repeats it per SKU. Normalize independently of the execution adapter.
        rows = {
            r["sku"]: {**r, "snapshot_day": r.get("snapshot_day") or inventory.get("snapshot_day")}
            for r in inventory["items"]
        }
        first, last = (date.fromisoformat(bundle["period"][k]) for k in ("start", "end"))
        days = (last - first).days + 1
        items = []
        for sku in selected:
            row = rows.get(sku)
            reason, below, status = "missing_inventory", None, "unavailable"
            rate_text = cover_text = None
            keys = {}
            snapshot = row["snapshot_day"] if row else None
            item_coverage = None
            if row:

                def stock(suffix, raw, unit, as_of=None):
                    keys[suffix] = f"inventory.{sku}.{suffix}"
                    put(
                        f"inventory.{sku}.{suffix}",
                        raw,
                        unit,
                        entity_type="sku",
                        entity_id=sku,
                        snapshot=as_of,
                    )

                stock("available_units", row["available_units"], "count", snapshot)
                if row["lead_days"] is not None:
                    stock("lead_days", row["lead_days"], "days", snapshot)
                values, covered = raw_totals(sales, [sku], bundle["period"])
                item_coverage = covered
                if date.fromisoformat(snapshot) > last:
                    reason = "future_snapshot"
                elif (last - date.fromisoformat(snapshot)).days > bundle["inventory_policy"][
                    "max_age_days"
                ]:
                    reason = "stale_snapshot"
                elif not covered["complete"] or covered["expected_records"] != days:
                    reason = "incomplete_sales_window"
                elif not values["paid_units"]:
                    reason = "zero_demand"
                else:
                    rate = Decimal(values["paid_units"]) / days
                    rate_text = fixed(rate)
                    cover_text = fixed(
                        Decimal(row["available_units"]) * days / values["paid_units"]
                    )
                    stock("paid_units", values["paid_units"], "count")
                    stock("window_days", days, "days")
                    stock("daily_paid_units_proxy", fixed(rate), "units_per_day")
                    stock(
                        "coverage_days",
                        fixed(Decimal(row["available_units"]) * days / values["paid_units"]),
                        "days",
                        snapshot,
                    )
                    reason = "missing_lead_days" if row["lead_days"] is None else None
                    status = "partial" if reason else "ok"
                    if reason is None:
                        below = (
                            row["available_units"] * days < row["lead_days"] * values["paid_units"]
                        )
                        stock(
                            "lead_paid_units_proxy",
                            fixed(rate * row["lead_days"]),
                            "count",
                            snapshot,
                        )
            item = {
                "sku": sku,
                "status": status,
                "reason": reason,
                "below_lead_proxy": below,
                "snapshot_day": snapshot,
                "daily_paid_units_proxy": rate_text,
                "coverage_days": cover_text,
                "fact_keys": keys,
            }
            if item_coverage is not None:
                item["sales_coverage"] = item_coverage
            items.append(item)
        invariants.update(
            items=items,
            scope={"skus": selected},
            coverage={
                "complete": all(r["status"] == "ok" for r in items),
                "expected_skus": len(items),
                "observed_inventory_skus": sum(r["snapshot_day"] is not None for r in items),
            },
        )
        if result.get("tool_version") in {"4", "5", "6"}:
            invariants["coverage"].update(
                snapshot_complete=all(i["snapshot_day"] is not None for i in items),
                sales_window_complete=all(
                    i.get("sales_coverage", {}).get("complete") is True for i in items
                ),
                lead_days_complete=all("lead_days" in i["fact_keys"] for i in items),
                proxy_calculable=all(i["coverage_days"] is not None for i in items),
                threshold_comparable=all(i["below_lead_proxy"] is not None for i in items),
                meaning="complete仅表示全部代理比较条件齐备，不代表库存快照是否存在",
            )
    elif tool == "search_rule_documents":
        pass  # Literal/semantic bindings are a separate evaluator assertion.
    else:
        raise ValueError("tool not covered by independent oracle")
    return facts, invariants


def numeric_errors(sales, bundle, tool, arguments, result, inventory=None):
    """Fail closed on missing/extra facts, bad units, values, scope or structure."""
    try:
        facts, structures = expected_numeric(sales, bundle, tool, arguments, result, inventory)
        errors = []
        actual = result.get("fact_catalog", {})
        if set(actual) != set(facts):
            errors.append("fact_key_set_mismatch")
        for key, value in facts.items():
            if key not in actual or any(
                actual[key].get(field) != expected for field, expected in value.items()
            ):
                errors.append("independent_fact_mismatch:" + key)
        for field, value in structures.items():
            checked = result.get(field)
            if checked != value:
                errors.append("independent_structure_mismatch:" + field)
        return sorted(set(errors))
    except (KeyError, TypeError, ValueError, DecimalException, ZeroDivisionError):
        return ["invalid_or_unknown_oracle_input"]


def fixed_numeric_errors(sales, bundle, result):
    """Check the legacy baseline independently using the same raw input contract."""
    try:
        skus = bundle["allowed_skus"]
        now, coverage = raw_totals(sales, skus, bundle["period"])
        before, prior_coverage = raw_totals(sales, skus, bundle["comparison_period"])
        comparable = all(
            c["complete"] and c["expected_records"] > 0 for c in (coverage, prior_coverage)
        )
        catalog, expected_dimensions = {}, {}

        def put(key, raw, unit, entity=None, period=None):
            catalog[key] = {
                "key": key,
                "value": fixed(Decimal(raw) / 100) if unit == "CNY" else str(raw),
                "unit": unit,
                "entity_id": entity or ",".join(skus),
                "period_start": (period or bundle["period"])["start"],
                "period_end": (period or bundle["period"])["end"],
                "snapshot_as_of": None,
            }

        for name, raw in now.items():
            put(
                name,
                raw,
                "CNY" if name in {"gross_revenue", "refund_amount", "net_revenue"} else "count",
            )
        put("net_units", now["paid_units"] - now["refunded_units"], "count")
        if now["paid_units"]:
            put(
                "refund_rate_pct",
                fixed(Decimal(now["refunded_units"]) * 100 / now["paid_units"]),
                "percent",
            )
        if comparable:
            for name in ("gross_revenue", "refund_amount", "refunded_units", "net_revenue"):
                put(
                    name + "_delta",
                    now[name] - before[name],
                    "count" if name == "refunded_units" else "CNY",
                )
            if before["net_revenue"] > 0:
                put(
                    "net_revenue_change_pct",
                    fixed(
                        Decimal(now["net_revenue"] - before["net_revenue"])
                        * 100
                        / before["net_revenue"]
                    ),
                    "percent",
                )
        if result.get("answer_version") in {2, 3}:
            if prior_coverage["observed_records"]:
                for key in ("net_revenue", "refund_amount", "paid_units", "refunded_units"):
                    put(
                        "comparison_" + key,
                        before[key],
                        "CNY" if key in {"net_revenue", "refund_amount"} else "count",
                        period=bundle["comparison_period"],
                    )
                if before["paid_units"]:
                    put(
                        "comparison_refund_rate_pct",
                        fixed(Decimal(before["refunded_units"]) * 100 / before["paid_units"]),
                        "percent",
                        period=bundle["comparison_period"],
                    )
            if comparable:
                put("paid_units_delta", now["paid_units"] - before["paid_units"], "count")
        for dimension, column in (("products", "sku"), ("categories", "category")):
            groups = {}
            for product in sales["products"]:
                if product["sku"] in skus:
                    groups.setdefault(product[column], []).append(product["sku"])
            values = []
            for index, (entity, members) in enumerate(sorted(groups.items())):
                current, covered = raw_totals(sales, members, bundle["period"])
                previous, _ = raw_totals(sales, members, bundle["comparison_period"])
                key = f"{dimension}.{index}.net_revenue"
                put(key, current["net_revenue"], "CNY", entity)
                delta = current["net_revenue"] - previous["net_revenue"] if comparable else None
                delta_key = f"{dimension}.{index}.delta" if comparable else None
                if comparable:
                    put(delta_key, delta, "CNY", entity)
                parts = {}
                if result.get("answer_version") == 3:
                    periods = {
                        "current": bundle["period"],
                        "comparison": bundle["comparison_period"],
                    }
                    for metric in ("net_revenue", "gross_revenue", "refund_amount"):
                        for part, span in periods.items():
                            value, cov = raw_totals(sales, members, span)
                            if cov["observed_records"]:
                                component_key = f"{dimension}.{index}.{metric}.{part}"
                                put(component_key, value[metric], "CNY", entity, period=span)
                                parts[f"{metric}.{part}"] = component_key
                        if comparable:
                            component_key = f"{dimension}.{index}.{metric}.delta"
                            put(component_key, current[metric] - previous[metric], "CNY", entity)
                            parts[f"{metric}.delta"] = component_key
                values.append(
                    {
                        **(
                            {"component_fact_keys": parts}
                            if result.get("answer_version") == 3
                            else {}
                        ),
                        "entity": entity,
                        "net_revenue": fixed(Decimal(current["net_revenue"]) / 100),
                        "fact_key": key,
                        "coverage": covered,
                        "delta_cents": delta,
                        "delta_key": delta_key,
                    }
                )
            values.sort(key=lambda row: (-abs(row["delta_cents"] or 0), row["entity"]))
            expected_dimensions[dimension] = values
        errors = []
        actual = result["fact_catalog"]
        if set(actual) != set(catalog) or result["metrics"] != {
            k: v["value"] for k, v in catalog.items()
        }:
            errors.append("fixed_fact_key_set_or_metrics_mismatch")
        for key, expected in catalog.items():
            if any(actual.get(key, {}).get(k) != v for k, v in expected.items()):
                errors.append("fixed_independent_fact_mismatch:" + key)
        prior = result["previous_period"]
        expected_prior = {
            **bundle["comparison_period"],
            **{
                k: fixed(Decimal(v) / 100)
                if k in {"gross_revenue", "refund_amount", "net_revenue"}
                else str(v)
                for k, v in before.items()
            },
            "coverage": prior_coverage,
            "comparable": comparable,
        }
        if prior != expected_prior or result["coverage"] != coverage:
            errors.append("fixed_coverage_or_previous_mismatch")
        for dimension, values in expected_dimensions.items():
            actual_values = result["dimensions"][dimension]
            if len(actual_values) != len(values) or any(
                any(a.get(k) != v for k, v in e.items()) for a, e in zip(actual_values, values)
            ):
                errors.append("fixed_dimensions_mismatch:" + dimension)
        return sorted(set(errors))
    except (KeyError, TypeError, ValueError, DecimalException, ZeroDivisionError):
        return ["invalid_fixed_oracle_input"]
