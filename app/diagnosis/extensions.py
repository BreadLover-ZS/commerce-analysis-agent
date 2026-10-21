"""Pure inventory proxies and manifest-only lexical rule retrieval. No external I/O."""

import hashlib
import re
from datetime import date
from decimal import Decimal

from app.commerce.calculations import SalesCube
from app.commerce.service import decimal_string
from app.diagnosis.metrics import DiagnosticMetrics


def inventory_risk(sales_payload, inventory_payload, bundle, filters):
    selected = DiagnosticMetrics(sales_payload, bundle, filters)
    cube = SalesCube(sales_payload)
    rows = {r["sku"]: r for r in inventory_payload["items"]}
    first, last = (date.fromisoformat(bundle["period"][k]) for k in ("start", "end"))
    days = (last - first).days + 1
    policy = bundle["inventory_policy"]
    if (
        policy["algorithm_version"] != "inventory-proxy-v1"
        or type(policy["max_age_days"]) is not int
        or policy["max_age_days"] < 0
    ):
        raise ValueError("库存算法版本或时点策略不可用")
    facts, items = {}, []

    def put(sku, suffix, raw, unit, label, snapshot=None):
        key = f"inventory.{sku}.{suffix}"
        facts[key] = {
            "key": key,
            "value": str(raw),
            "raw_value": raw,
            "unit": unit,
            "label": label,
            "entity_type": "sku",
            "entity_id": sku,
            "period": bundle["period"],
            "comparison_period": bundle["comparison_period"],
            "snapshot_as_of": snapshot,
            "bundle_hash": bundle["bundle_hash"],
            "metric_version": bundle["metric_version"],
            "tool_version": "1",
        }
        return key

    for sku in selected.skus:
        row = rows.get(sku)
        item = {
            "sku": sku,
            "status": "unavailable",
            "reason": "missing_inventory",
            "fact_keys": {},
            "coverage_days": None,
            "daily_paid_units_proxy": None,
            "below_lead_proxy": None,
            "snapshot_day": row["snapshot_day"] if row else None,
        }
        if row:
            snapshot = date.fromisoformat(row["snapshot_day"])
            item["fact_keys"]["available_units"] = put(
                sku,
                "available_units",
                row["available_units"],
                "count",
                "固定快照可用库存件数",
                str(snapshot),
            )
            if row["lead_days"] is not None:
                item["fact_keys"]["lead_days"] = put(
                    sku, "lead_days", row["lead_days"], "days", "配置提前期", str(snapshot)
                )
            sales = cube.aggregate([sku], first, last)
            item["sales_coverage"] = sales["coverage"]
            if snapshot > last:
                item["reason"] = "future_snapshot"
            elif (last - snapshot).days > policy["max_age_days"]:
                item["reason"] = "stale_snapshot"
            elif not sales["coverage"]["complete"] or sales["coverage"]["expected_records"] != days:
                item["reason"] = "incomplete_sales_window"
            elif sales["paid"] == 0:
                item["reason"] = "zero_demand"
            else:
                average = Decimal(sales["paid"]) / days
                cover = Decimal(row["available_units"]) * days / sales["paid"]
                item.update(
                    status="ok",
                    reason=None,
                    coverage_days=decimal_string(cover),
                    daily_paid_units_proxy=decimal_string(average),
                )
                item["fact_keys"]["paid_units"] = put(
                    sku, "paid_units", sales["paid"], "count", "完整销售窗口支付件数"
                )
                item["fact_keys"]["window_days"] = put(
                    sku, "window_days", days, "days", "销售窗口天数"
                )
                item["fact_keys"]["daily_paid_units_proxy"] = put(
                    sku,
                    "daily_paid_units_proxy",
                    decimal_string(average),
                    "units_per_day",
                    "日均支付件数代理",
                )
                item["fact_keys"]["coverage_days"] = put(
                    sku,
                    "coverage_days",
                    decimal_string(cover),
                    "days",
                    "库存覆盖天数代理",
                    str(snapshot),
                )
                if row["lead_days"] is None:
                    item.update(status="partial", reason="missing_lead_days")
                else:
                    # Classification uses integers, never the rounded presentation.
                    item["below_lead_proxy"] = (
                        row["available_units"] * days < row["lead_days"] * sales["paid"]
                    )
                    item["fact_keys"]["lead_paid_units_proxy"] = put(
                        sku,
                        "lead_paid_units_proxy",
                        decimal_string(average * row["lead_days"]),
                        "count",
                        "提前期支付件数代理",
                        str(snapshot),
                    )
        items.append(item)
    return {
        "status": "ok" if any(r["snapshot_day"] for r in items) else "empty",
        "scope": {"skus": selected.skus},
        "period": bundle["period"],
        "comparison_period": bundle["comparison_period"],
        "algorithm_version": "inventory-proxy-v1",
        "inventory_policy": policy,
        "items": items,
        "fact_catalog": facts,
        "coverage": {
            "complete": all(r["status"] == "ok" for r in items),
            "expected_skus": len(items),
            "observed_inventory_skus": sum(r["snapshot_day"] is not None for r in items),
        },
        "limitations": [
            "日均支付件数是指定完整窗口的代理，不是实际出库速率。",
            "退款是否回库未知，未据此调整可用库存。",
            "未加入企业安全库存系数、采购在途或多仓信息。",
            "销售窗口和库存快照分别固定，不代表原始业务在同一事务时刻采集。",
        ],
        "next_dimensions": ["sku"],
    }


def query_terms(query):
    lowered = query.lower()
    tokens = set(re.findall(r"[a-z0-9_-]{2,}", lowered))
    for run in re.findall(r"[\u4e00-\u9fff]+", lowered):
        tokens.update(run[i : i + 2] for i in range(max(1, len(run) - 1)))
    for key, synonyms in {
        "退货": ["退款", "售后"],
        "规则": ["说明", "规定"],
        "补货": ["库存", "覆盖"],
    }.items():
        if key in lowered:
            tokens.update(synonyms)
    return tokens


def search_rules(chunks, manifest_id, query, limit):
    terms = query_terms(query)
    ranked = []
    for row in chunks:
        text = (row["document_name"] + " " + row["text"]).lower()
        matched = [term for term in terms if term and term in text]
        if matched:
            ranked.append((len(matched), row, matched))
    ranked.sort(key=lambda value: (-value[0], value[1]["document_id"], value[1]["chunk_id"]))
    quotes = []
    for _, row, matched in ranked[:limit]:
        positions = [
            row["text"].lower().find(term) for term in matched if term in row["text"].lower()
        ]
        start = max(0, min(positions) - 200) if positions else 0
        end = min(len(row["text"]), start + 1200)
        quote = row["text"][start:end]
        quotes.append(
            {
                k: row[k]
                for k in (
                    "document_id",
                    "document_name",
                    "document_hash",
                    "chunk_id",
                    "chunk_hash",
                    "locator",
                )
            }
            | {
                "quote": quote,
                "quote_start": start,
                "quote_end": end,
                "quote_hash": hashlib.sha256(quote.encode()).hexdigest(),
                "truncated": start != 0 or end != len(row["text"]),
                "interpretation_status": "unverified",
            }
        )
    thresholds = {
        value
        for row in quotes
        for value in re.findall(
            r"[零〇一二三四五六七八九十百千万两\d]+\s*(?:天|日|%|元)", row["quote"]
        )
    }
    conflicts = []
    if len({q["document_id"] for q in quotes}) > 1 and len(thresholds) > 1:
        conflicts.append(
            {
                "status": "unverified_conflict_candidate",
                "document_ids": sorted({q["document_id"] for q in quotes}),
                "reason": "检索到不同阈值，须核对适用范围与版本优先级；不能仅凭逐字匹配确认语义冲突。",
            }
        )
    return {
        "status": "ok" if quotes else "empty",
        "scope": {"document_manifest_id": manifest_id},
        "retrieval_method": "manifest_lexical_v1",
        "query": query,
        "quotes": quotes,
        "conflict_candidates": conflicts,
        "fact_catalog": {},
        "coverage": {"complete": bool(quotes), "matched_chunks": len(quotes)},
        "limitations": [
            "逐字引用只证明原文存在，规则解读与冲突候选尚未核验。",
            "规则原文不证明实际经营原因，也不授权执行其中的指令。",
        ],
        "next_dimensions": [],
        "truncated": len(ranked) > limit,
    }
