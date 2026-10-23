"""Registry 6 seeded raw-row checks, including typed per-SKU unit counts."""

import argparse
import json
import random
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from app.diagnosis.inputs import get_bundle
from app.diagnosis.metrics import DiagnosticMetrics
from app.diagnosis.schemas import Filters
from app.diagnosis.tools import DiagnosisToolGateway
from app.storage.models import CommerceDataset
from app.storage.repository import NotFound
from evaluations.diagnosis_cases import load_cases
from evaluations.diagnosis_runtime import admit, new_task, sandbox

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True)
OUT = parser.parse_args().output
OUT.mkdir(parents=True, exist_ok=False)
FIELDS = {
    "net_revenue": None,
    "gross_revenue": "revenue_cents",
    "refund_amount": "refund_cents",
    "paid_units": "paid_units",
    "refunded_units": "refunded_units",
}
SEED = 20261023
rng = random.Random(SEED)
checks = 0


def check(value, note):
    global checks
    checks += 1
    if not value:
        raise AssertionError(note)


def totals(raw, skus, span):
    rows = [
        r
        for r in raw["daily_sales"]
        if r["sku"] in skus and span["start"] <= r["day"] <= span["end"]
    ]
    expected = sum(
        max(
            0,
            (
                date.fromisoformat(span["end"])
                - max(date.fromisoformat(span["start"]), date.fromisoformat(p["active_from"]))
            ).days
            + 1,
        )
        for p in raw["products"]
        if p["sku"] in skus
    )
    sums = {m: sum(r[f] for r in rows) for m, f in FIELDS.items() if f}
    sums["net_revenue"] = sums["gross_revenue"] - sums["refund_amount"]
    return sums, {
        "expected_records": expected,
        "observed_records": len(rows),
        "missing_records": expected - len(rows),
        "complete": expected == len(rows),
    }


numeric = []
for number in range(240):
    base = date(2026, 6, 1)
    length = rng.randint(1, 5)
    periods = {
        "comparison": {"start": str(base), "end": str(base + timedelta(days=length - 1))},
        "current": {
            "start": str(base + timedelta(days=10)),
            "end": str(base + timedelta(days=9 + length)),
        },
    }
    products = [
        {
            "sku": f"S{i}",
            "name": f"商品{i}",
            "category": f"C{i % 2}",
            "active_from": str(base + timedelta(days=rng.choice([-5, 0, 1, 10]))),
        }
        for i in range(rng.randint(1, 5))
    ]
    rows = []
    for p in products:
        for span in periods.values():
            for offset in range(length):
                day = str(date.fromisoformat(span["start"]) + timedelta(days=offset))
                if day < p["active_from"] or rng.random() < 0.08:
                    continue
                zero = rng.random() < 0.16
                rows.append(
                    {
                        "sku": p["sku"],
                        "day": day,
                        "revenue_cents": 0 if zero else rng.randrange(0, 200000),
                        "refund_cents": 0 if zero else rng.randrange(0, 150000),
                        "paid_units": 0 if zero else rng.randrange(0, 300),
                        "refunded_units": 0 if zero else rng.randrange(0, 150),
                    }
                )
    if not rows:
        continue
    raw = {"schema_version": 1, "products": products, "daily_sales": rows}
    bundle = {
        "allowed_skus": [p["sku"] for p in products],
        "allowed_categories": sorted({p["category"] for p in products}),
        "period": periods["current"],
        "comparison_period": periods["comparison"],
        "bundle_hash": "audit",
        "metric_version": "1",
        "sample": True,
    }
    for selected in [bundle["allowed_skus"], bundle["allowed_skus"][:1]]:
        expected = {part: totals(raw, selected, span) for part, span in periods.items()}
        comparable = all(c["complete"] and c["expected_records"] > 0 for _, c in expected.values())
        for metric in FIELDS:
            p = DiagnosticMetrics(raw, bundle, Filters(skus=selected)).compare(metric)
            check(p["comparable"] == comparable, (number, "comparable"))
            for part, (values, coverage) in expected.items():
                check(p["coverage"][part] == coverage, (number, "coverage", part))
                f = p["fact_catalog"][f"{metric}.{part}"]
                rawvalue = Decimal(f["value"]) * 100 if f["unit"] == "CNY" else Decimal(f["value"])
                check(rawvalue == values[metric], (number, "metric", metric, part))
            check(
                (f"{metric}.delta" in p["fact_catalog"]) == comparable,
                (number, "delta applicability"),
            )
            if comparable:
                f = p["fact_catalog"][f"{metric}.delta"]
                check(
                    Decimal(f["value"]) * (100 if f["unit"] == "CNY" else 1)
                    == expected["current"][0][metric] - expected["comparison"][0][metric],
                    (number, "delta"),
                )
        for dimension in ["sku", "category"]:
            p = DiagnosticMetrics(raw, bundle, Filters(skus=selected)).rank_components(
                dimension, 1, component_metrics=tuple(FIELDS)
            )
            for sku in selected:
                for part, span in periods.items():
                    values, cov = totals(raw, [sku], span)
                    for metric in FIELDS:
                        key = f"component.{sku}.{metric}.{part}"
                        check(
                            (key in p["fact_catalog"]) == bool(cov["observed_records"]),
                            (number, "component applicability", sku, metric, part),
                        )
                        if key in p["fact_catalog"]:
                            f = p["fact_catalog"][key]
                            unit = "count" if metric.endswith("units") else "CNY"
                            check(
                                f["unit"] == unit and f["entity_id"] == sku,
                                (number, "typed component", key),
                            )
                            check(
                                Decimal(f["value"]) * (1 if unit == "count" else 100)
                                == values[metric],
                                (number, "component raw value", key),
                            )
            if comparable:
                wanted = (
                    expected["current"][0]["net_revenue"] - expected["comparison"][0]["net_revenue"]
                )
                check(
                    sum(r["delta_cents"] for r in p["all_contributors"]) == wanted,
                    (number, "full rank balance"),
                )
                check(
                    sum(r["delta_cents"] for r in p["contributors"])
                    + p["reconciliation"]["other_delta_cents"]
                    == wanted,
                    (number, "top1 balance"),
                )
                for sku in selected:
                    a = totals(raw, [sku], periods["current"])[0]
                    b = totals(raw, [sku], periods["comparison"])[0]
                    for metric in FIELDS:
                        check(
                            p["fact_catalog"][f"component.{sku}.{metric}.delta"]["raw_value"]
                            == a[metric] - b[metric],
                            (number, "component", sku, metric),
                        )
            else:
                check(p["reconciliation"] is None, (number, "incomparable no attribution"))
        p = DiagnosticMetrics(raw, bundle, Filters(skus=selected)).refunds()
        for part, (v, _) in expected.items():
            key = f"refund_ratio.{part}"
            check(
                (key in p["fact_catalog"]) == (v["paid_units"] > 0), (number, "ratio applicability")
            )
            if v["paid_units"]:
                ratio = (Decimal(v["refunded_units"]) * 100 / v["paid_units"]).quantize(
                    Decimal(".01"), rounding=ROUND_HALF_UP
                )
                check(
                    Decimal(p["fact_catalog"][key]["value"]) == ratio,
                    (number, "refund denominator"),
                )
    numeric.append(number)

security = []
case = next(c for c in load_cases()[0] if c.case_id == "D01")
with sandbox(diagnosis_runtime="v13") as (settings, repo, user, other):
    conv, bundle, _ = admit(repo, user, other, case)
    task = new_task(repo, user, conv, bundle, case.query, "agent")
    ctx = repo.claim()
    gw = DiagnosisToolGateway(repo, ctx)
    a = gw.invoke("compare_periods", {"filters": {"skus": ["A"]}}, "audit-a")
    b = gw.invoke("compare_periods", {"filters": {"skus": ["B"]}}, "audit-b")
    alias = gw.invoke("compare_periods", {"filters": {"categories": ["饮具"]}}, "audit-alias")
    security.append(
        {
            "check": "scope_specific_cache",
            "passed": a["query_spec_hash"] != b["query_spec_hash"]
            and a["call_id"] != b["call_id"]
            and alias.get("reused") is True
            and alias["call_id"] == a["call_id"],
        }
    )
    before = repo.get_task(task["id"])["metrics"]["tool_calls"]
    for name, args in [
        ("execute_sql", {}),
        ("compare_periods", {"user_id": other.id}),
        ("compare_periods", {"filters": {"skus": ["FOREIGN"]}}),
        ("compare_periods", {"metric": "profit"}),
        ("compare_periods", {"period": {"start": "1900-01-01"}}),
    ]:
        result = gw.invoke(name, args, "audit-reject-" + str(len(security)))
        security.append(
            {
                "check": "reject_" + name + "_" + str(len(security)),
                "passed": result["status"]
                in ["unavailable", "invalid_argument", "unsupported_metric"],
            }
        )
    security.append(
        {
            "check": "rejected_arguments_no_execution",
            "passed": repo.get_task(task["id"])["metrics"]["tool_calls"] == before,
        }
    )
    try:
        get_bundle(repo, other, bundle["bundle_id"])
        denied = False
    except NotFound:
        denied = True
    security.append({"check": "foreign_bundle_read", "passed": denied})
    with repo.session() as db:
        db.get(CommerceDataset, bundle["sales_dataset_id"]).committed = False
        db.commit()
    for label, call in [
        (
            "revoke_cached_read",
            lambda: gw.invoke("compare_periods", {"filters": {"skus": ["A"]}}, "revoked"),
        ),
        ("revoke_catalog", gw.catalog),
    ]:
        try:
            call()
            denied = False
        except NotFound:
            denied = True
        security.append({"check": label, "passed": denied})
check(all(r["passed"] for r in security), "security")
result = {
    "seed": SEED,
    "generated_datasets": len(numeric),
    "scopes_per_dataset": 2,
    "assertions": checks,
    "arithmetic_assertions": checks - 1,
    "arithmetic_failures": 0,
    "security_checks": security,
    "new_provider_requests": 0,
    "scope": "Pure integer-row properties + isolated SQLite gateway controls; not real model quality or production security certification",
}
(OUT / "invariants.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({k: v for k, v in result.items() if k != "security_checks"}, ensure_ascii=False))
