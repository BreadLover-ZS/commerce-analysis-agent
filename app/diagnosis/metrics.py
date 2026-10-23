"""Deterministic diagnosis results. Integer cents are the reconciliation authority."""

from datetime import date, timedelta
from decimal import Decimal

from app.commerce.calculations import SalesCube
from app.commerce.service import decimal_string, money

METRICS = {
    "net_revenue": ("net", "CNY", "净销售额"),
    "gross_revenue": ("revenue", "CNY", "支付金额"),
    "refund_amount": ("refund", "CNY", "退款金额"),
    "paid_units": ("paid", "count", "支付件数"),
    "refunded_units": ("refunded", "count", "退款件数"),
}
LIMITATIONS = [
    "金额按记账期计算，净销售额未扣成本，不是利润。",
    "退款件数/支付件数是记账期占比，没有订单关联，不能证明同批订单最终退款率或经营因果。",
    "缺行不补零；启用后停报按缺数处理，不能自行认定下架。",
]


class DiagnosticMetrics:
    def __init__(self, payload, bundle, filters):
        self.cube, self.bundle = SalesCube(payload), bundle
        allowed = set(bundle["allowed_skus"])
        categories = set(bundle["allowed_categories"])
        if set(filters.skus) - allowed or set(filters.categories) - categories:
            raise ValueError("筛选超出输入包授权范围")
        self.skus = sorted(
            sku
            for sku in allowed
            if (not filters.skus or sku in filters.skus)
            and (not filters.categories or self.cube.products[sku].category in filters.categories)
        )
        if not self.skus:
            raise ValueError("筛选交集为空")
        self.periods = {"current": bundle["period"], "comparison": bundle["comparison_period"]}
        self.values = {
            name: self.cube.aggregate(
                self.skus, date.fromisoformat(p["start"]), date.fromisoformat(p["end"])
            )
            for name, p in self.periods.items()
        }
        self.comparable = all(
            v["coverage"]["complete"] and v["coverage"]["expected_records"] > 0
            for v in self.values.values()
        )
        self.catalog = {}

    def fact(self, key, raw, unit, label, *, period=None, entity_type="scope", entity_id=None):
        self.catalog[key] = {
            "key": key,
            "value": money(raw) if unit == "CNY" else str(raw),
            "raw_value": raw,
            "unit": unit,
            "label": label,
            "entity_type": entity_type,
            "entity_id": entity_id or ",".join(self.skus),
            "period": period or self.bundle["period"],
            "comparison_period": self.bundle["comparison_period"],
            "bundle_hash": self.bundle["bundle_hash"],
            "metric_version": self.bundle["metric_version"],
            "tool_version": "1",
        }
        return key

    def result(self):
        observed = sum(v["coverage"]["observed_records"] for v in self.values.values())
        return {
            "status": "empty" if not observed else "ok" if self.comparable else "incomparable",
            "period": self.bundle["period"],
            "comparison_period": self.bundle["comparison_period"],
            "scope": {"skus": self.skus},
            "coverage": {name: v["coverage"] for name, v in self.values.items()},
            "comparable": self.comparable,
            "fact_catalog": self.catalog,
            "limitations": LIMITATIONS
            + (
                []
                if self.comparable
                else ["至少一期无适用商品或记录不完整，不计算变化率和贡献归因。"]
            )
            + (["本输入为模拟数据。"] if self.bundle["sample"] else [])
            + (
                ["对比期不相邻，未消除节假日与季节差异。"]
                if date.fromisoformat(self.bundle["comparison_period"]["end"]) + timedelta(days=1)
                != date.fromisoformat(self.bundle["period"]["start"])
                else []
            ),
            "next_dimensions": ["sku", "category", "day"],
        }

    def inspect(self):
        return {
            **self.result(),
            "products": [self.cube.products[k].model_dump(mode="json") for k in self.skus],
            "supported_metrics": list(METRICS),
            "unsupported_metrics": ["profit", "conversion_rate", "roi", "cohort_refund_rate"],
            "quality": {
                name: {**v["coverage"], "not_active": v["coverage"]["expected_records"] == 0}
                for name, v in self.values.items()
            },
        }

    def compare(self, metric):
        field, unit, label = METRICS[metric]
        facts = {}
        for name, v in self.values.items():
            facts[name] = self.fact(
                f"{metric}.{name}", v[field], unit, f"已记录{label}", period=self.periods[name]
            )
        a, b = self.values["current"][field], self.values["comparison"][field]
        reason = None
        if self.comparable:
            facts["delta"] = self.fact(f"{metric}.delta", a - b, unit, f"{label}变化")
            if b > 0:
                facts["change_pct"] = self.fact(
                    f"{metric}.change_pct",
                    decimal_string(Decimal(a - b) * 100 / b),
                    "percent",
                    f"{label}变化率",
                )
            else:
                reason = "non_positive_base"
        else:
            reason = "incomparable"
        return {
            **self.result(),
            "metric": metric,
            "fact_keys": facts,
            "change_rate_unavailable": reason,
        }

    def rank(self, dimension, top_k):
        entries = []
        if self.comparable:
            groups = {}
            for sku in self.skus:
                key = sku if dimension == "sku" else self.cube.products[sku].category
                groups.setdefault(key, []).append(sku)
            total = self.values["current"]["net"] - self.values["comparison"]["net"]
            for index, (entity, skus) in enumerate(sorted(groups.items())):
                a, b = (
                    self.cube.aggregate(
                        skus, date.fromisoformat(p["start"]), date.fromisoformat(p["end"])
                    )
                    for p in self.periods.values()
                )
                delta = a["net"] - b["net"]
                key = self.fact(
                    f"contribution.{index}",
                    delta,
                    "CNY",
                    "净销售额变化贡献",
                    entity_type=dimension,
                    entity_id=entity,
                )
                share = decimal_string(Decimal(delta) * 100 / total) if total else None
                share_key = None
                if share is not None:
                    share_key = self.fact(
                        f"contribution.{index}.share",
                        share,
                        "percent",
                        "相对总体变化的有符号贡献比例",
                        entity_type=dimension,
                        entity_id=entity,
                    )
                entries.append(
                    {
                        "entity": entity,
                        "delta_cents": delta,
                        "fact_key": key,
                        "share_of_total_pct": share,
                        "share_fact_key": share_key,
                    }
                )
            entries.sort(key=lambda e: (-abs(e["delta_cents"]), e["entity"]))
            shown = entries[:top_k]
            other = total - sum(e["delta_cents"] for e in shown)
            self.fact("contribution.other", other, "CNY", "其他项净变化贡献", entity_id="other")
            self.fact("contribution.total", total, "CNY", "总体净销售额变化")
            reconciliation = {
                "total_delta_cents": total,
                "shown_delta_cents": sum(e["delta_cents"] for e in shown),
                "other_delta_cents": other,
                "full_delta_cents": sum(e["delta_cents"] for e in entries),
                "balanced": sum(e["delta_cents"] for e in entries) == total,
            }
        else:
            shown, reconciliation = [], None
        return {
            **self.result(),
            "dimension": dimension,
            "contributors": shown,
            "all_contributors": entries,
            "reconciliation": reconciliation,
            "truncated": len(entries) > top_k,
            "truncation_note": "Top-K之外合并为其他；完整贡献在本证据all_contributors中。"
            if len(entries) > top_k
            else None,
        }

    def series(self):
        points = []
        for name, p in self.periods.items():
            day, end = date.fromisoformat(p["start"]), date.fromisoformat(p["end"])
            while day <= end:
                v = self.cube.aggregate(self.skus, day, day)
                coverage = v["coverage"]
                row = {
                    "day": str(day),
                    "period_name": name,
                    "coverage": coverage,
                    "status": "not_active"
                    if not coverage["expected_records"]
                    else "missing"
                    if not coverage["complete"]
                    else "zero"
                    if not any(v[k] for k in ("revenue", "refund", "paid", "refunded"))
                    else "recorded",
                    "fact_keys": {},
                }
                # Keep absent/not-yet-active distinct from recorded zero.
                if coverage["observed_records"]:
                    for metric, (field, unit, label) in METRICS.items():
                        key = f"series.{name}.{day}.{metric}"
                        row["fact_keys"][metric] = self.fact(
                            key,
                            v[field],
                            unit,
                            f"当日已记录{label}",
                            period={"start": str(day), "end": str(day)},
                        )
                points.append(row)
                day += timedelta(days=1)
        return {**self.result(), "granularity": "day", "series": points}

    def refunds(self):
        facts = {}
        for metric in ("gross_revenue", "refund_amount", "paid_units", "refunded_units"):
            self.compare(metric)
        for name, v in self.values.items():
            if v["paid"] > 0:
                facts[name] = self.fact(
                    f"refund_ratio.{name}",
                    decimal_string(Decimal(v["refunded"]) * 100 / v["paid"]),
                    "percent",
                    "已记录记账期退款件数占比",
                    period=self.periods[name],
                )
        a, b = self.values["current"], self.values["comparison"]
        ratio_up = (
            a["refunded"] * b["paid"] > b["refunded"] * a["paid"]
            if self.comparable and a["paid"] and b["paid"]
            else None
        )
        return {
            **self.result(),
            "ratio_fact_keys": facts,
            "ratio_unavailable": {
                name: "zero_denominator" for name, v in self.values.items() if not v["paid"]
            },
            "ratio_increased": ratio_up,
            "refund_amount_delta_cents": a["refund"] - b["refund"] if self.comparable else None,
            "denominator_effect": bool(
                ratio_up and a["refunded"] <= b["refunded"] and a["paid"] < b["paid"]
            ),
            "net_change_identity": {
                "net_delta_cents": a["net"] - b["net"],
                "revenue_delta_cents": a["revenue"] - b["revenue"],
                "refund_delta_cents": a["refund"] - b["refund"],
            }
            if self.comparable
            else None,
        }

    def rank_components(
        self,
        dimension,
        top_k,
        *,
        component_metrics=("net_revenue", "gross_revenue", "refund_amount"),
    ):
        result = self.rank(dimension, top_k)
        keys, coverage = [], {}
        for sku in self.skus:
            values = {
                name: self.cube.aggregate(
                    [sku], date.fromisoformat(span["start"]), date.fromisoformat(span["end"])
                )
                for name, span in self.periods.items()
            }
            coverage[sku] = {name: v["coverage"] for name, v in values.items()}
            for metric in component_metrics:
                field, unit, label = METRICS[metric]
                for part, value in values.items():
                    if value["coverage"]["observed_records"]:
                        keys.append(
                            self.fact(
                                f"component.{sku}.{metric}.{part}",
                                value[field],
                                unit,
                                f"已记录{label}",
                                period=self.periods[part],
                                entity_type="sku",
                                entity_id=sku,
                            )
                        )
                if self.comparable:
                    keys.append(
                        self.fact(
                            f"component.{sku}.{metric}.delta",
                            values["current"][field] - values["comparison"][field],
                            unit,
                            f"{label}变化贡献",
                            entity_type="sku",
                            entity_id=sku,
                        )
                    )
        result.update(component_fact_keys=keys, component_coverage=coverage)
        return result
