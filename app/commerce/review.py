"""Deterministic review calculations over a single immutable dataset version."""

from collections import defaultdict
from decimal import Decimal

from app.commerce.answer_contract import request_contract
from app.commerce.calculations import SalesCube, comparison_dates
from app.commerce.datasets import owned_dataset
from app.commerce.service import decimal_string, fact, money

DEFINITIONS = {
    "net_revenue": "本记账周期支付金额减本周期退款金额；未扣成本，不是利润",
    "refund_rate_pct": "本记账周期退款件数/支付件数，非同一批订单最终退款率；跨期可超过百分之百",
    "comparison": "前一同长度周期；缺失记录不补零；缺数时不做变化归因",
    "coverage": "商品启用日起每天须有显式记录；零销量需零行；首版不支持下架后停报",
    "contribution": "本期净销售额减前期净销售额；商品与品类分别可加总对账；描述变化来源，不证明因果",
}


def calculate_review(payload, start, end, *, dataset_id, content_hash, sample):
    return calculate_periods(
        payload, start, end, dataset_id=dataset_id, content_hash=content_hash, sample=sample
    )


def calculate_periods(
    payload,
    start,
    end,
    *,
    dataset_id,
    content_hash,
    sample,
    comparison_start=None,
    comparison_end=None,
    allowed_skus=None,
    answer_version=1,
    objective="",
):
    prior_start, prior_end = comparison_dates(start, end, comparison_start, comparison_end)
    cube = SalesCube(payload)
    products, rows, aggregate = cube.products, cube.rows, cube.aggregate
    if allowed_skus is not None:
        allowed = set(allowed_skus)
        if not allowed or allowed - products.keys():
            raise ValueError("复盘范围必须属于固定销售版本")
        products = {sku: p for sku, p in products.items() if sku in allowed}
        rows = [row for row in rows if row.sku in allowed]
    adjacent = (start - prior_end).days == 1

    current, prior = aggregate(products, start, end), aggregate(products, prior_start, prior_end)
    complete = (
        current["coverage"]["complete"]
        and prior["coverage"]["complete"]
        and current["coverage"]["expected_records"] > 0
        and prior["coverage"]["expected_records"] > 0
    )
    metrics, catalog = {}, {}

    def put(key, value, unit, label, entity_id="all", entity_name=None, period=None):
        entity_name = entity_name or (
            "经营汇总"
            if current["coverage"]["complete"] and current["coverage"]["observed_records"]
            else "已记录经营数据"
        )
        metrics[key] = str(value)
        catalog[key] = fact(
            key, value, unit, label, entity_id, entity_name, *(period or (start, end))
        )

    for key, value, unit, label in [
        ("gross_revenue", money(current["revenue"]), "CNY", "支付金额"),
        ("refund_amount", money(current["refund"]), "CNY", "退款金额"),
        ("net_revenue", money(current["net"]), "CNY", "净销售额"),
        ("paid_units", current["paid"], "count", "支付件数"),
        ("refunded_units", current["refunded"], "count", "退款件数"),
        ("net_units", current["paid"] - current["refunded"], "count", "净销售件数"),
    ]:
        put(key, value, unit, label)
    if current["paid"]:
        put(
            "refund_rate_pct",
            decimal_string(Decimal(current["refunded"]) * 100 / current["paid"]),
            "percent",
            "按记账周期计算的退款件数占比",
        )
    findings, limitations = [], []
    if not current["coverage"]["observed_records"]:
        limitations.append("本期无销售记录，不能据此认定真实销售为零")
    if not complete:
        limitations.append(
            "变化贡献：本期或前期记录不完整或无适用商品，前期比较与变化贡献不可用；缺失未补零"
        )
    else:
        for key, value, unit, label in (
            (
                "gross_revenue_delta",
                money(current["revenue"] - prior["revenue"]),
                "CNY",
                "相对前期支付金额变化",
            ),
            (
                "refund_amount_delta",
                money(current["refund"] - prior["refund"]),
                "CNY",
                "相对前期退款金额变化",
            ),
            (
                "refunded_units_delta",
                current["refunded"] - prior["refunded"],
                "count",
                "相对前期退款件数变化",
            ),
        ):
            put(key, value, unit, label)
        put(
            "net_revenue_delta",
            money(current["net"] - prior["net"]),
            "CNY",
            "相对前期净销售额变化金额",
        )
        if prior["net"] > 0:
            put(
                "net_revenue_change_pct",
                decimal_string(Decimal(current["net"] - prior["net"]) * 100 / prior["net"]),
                "percent",
                "相对前一同长度周期净销售额变化率"
                if adjacent
                else "相对指定等长对比期净销售额变化率",
            )
        else:
            limitations.append("前期净销售额非正，变化率不可用；保留变化金额")
        if current["net"] < prior["net"]:
            findings.append(
                {
                    "code": "net_decline",
                    "text": "本期净销售额较前期下降，需结合商品贡献核对变化来源。",
                }
            )
        if current["paid"] and prior["paid"]:
            if current["refunded"] * prior["paid"] > prior["refunded"] * current["paid"]:
                findings.append(
                    {
                        "code": "refund_increase",
                        "text": "本期按记账周期计算的退款件数占比较前期增加，建议核对退款发生时间与原因。",
                    }
                )
    if any(
        s.refunded_units > s.paid_units or s.refund_cents > s.revenue_cents
        for s in rows
        if start <= s.day <= end
    ):
        limitations.append(
            "存在日退款超过当日支付的记录；跨期记账允许此情况，需核对退款归属，不据此断定数据错误"
        )

    if answer_version >= 2:
        direct_answers = [
            {
                "code": "activation_boundary",
                "text": "商品启用前的日期不适用，不要求记录或补零；启用后每个适用日须有显式记录，零销量需零行，缺行不能当作零。",
            },
            {
                "code": "unsupported_metrics",
                "text": "本数据未提供成本、访客、曝光及订单关联：不能计算两期利润、转化率、广告ROI或同批订单最终退款率，也不能用净销售额变化率替代转化率。",
            },
            {
                "code": "refund_denominator",
                "text": "记账期退款件数占比以本周期退款件数为分子、支付件数为分母；两期分别计算，无订单关联不能当作最终订单退款率。",
            },
        ]
        if prior["coverage"]["observed_records"]:
            for key, value, unit, label in [
                ("net_revenue", money(prior["net"]), "CNY", "对比期已记录净销售额"),
                ("refund_amount", money(prior["refund"]), "CNY", "对比期已记录退款金额"),
                ("paid_units", prior["paid"], "count", "对比期已记录支付件数"),
                ("refunded_units", prior["refunded"], "count", "对比期已记录退款件数"),
            ]:
                put("comparison_" + key, value, unit, label, period=(prior_start, prior_end))
            if prior["paid"]:
                put(
                    "comparison_refund_rate_pct",
                    decimal_string(Decimal(prior["refunded"]) * 100 / prior["paid"]),
                    "percent",
                    "对比期记账退款件数占比",
                    period=(prior_start, prior_end),
                )
        if complete:
            put(
                "paid_units_delta", current["paid"] - prior["paid"], "count", "相对前期支付件数变化"
            )
            if (
                current["paid"]
                and prior["paid"]
                and current["refunded"] * prior["paid"] > prior["refunded"] * current["paid"]
                and current["refunded"] <= prior["refunded"]
                and current["paid"] < prior["paid"]
            ):
                direct_answers.append(
                    {
                        "code": "payment_denominator_effect",
                        "text": "记账退款件数占比上升，同时退款件数未增加、支付件数减少，存在支付分母收缩效应；这只说明记账关系，不证明退款业务原因。",
                    }
                )

    dimensions = {"products": [], "categories": []}
    groups = {"products": {k: [k] for k in products}, "categories": defaultdict(list)}
    for p in products.values():
        groups["categories"][p.category].append(p.sku)
    for kind, grouped in groups.items():
        for index, (entity, skus) in enumerate(sorted(grouped.items())):
            now, before = aggregate(skus, start, end), aggregate(skus, prior_start, prior_end)
            name = products[entity].name if kind == "products" else entity
            key = f"{kind}.{index}.net_revenue"
            put(key, money(now["net"]), "CNY", "已记录净销售额", entity, name)
            entry = {
                "entity": entity,
                "name": name,
                "net_revenue": money(now["net"]),
                "fact_key": key,
                "coverage": now["coverage"],
                "delta_key": None,
                "delta_cents": None,
            }
            if complete:
                delta = now["net"] - before["net"]
                delta_key = f"{kind}.{index}.delta"
                put(delta_key, money(delta), "CNY", "相对前期净销售额变化贡献", entity, name)
                entry.update(delta_key=delta_key, delta_cents=delta)
            if answer_version == 3:
                parts = {}
                for metric, field, label in (
                    ("net_revenue", "net", "净销售额"),
                    ("gross_revenue", "revenue", "支付金额"),
                    ("refund_amount", "refund", "退款金额"),
                ):
                    for part, value, period in (
                        ("current", now, (start, end)),
                        ("comparison", before, (prior_start, prior_end)),
                    ):
                        if value["coverage"]["observed_records"]:
                            component_key = f"{kind}.{index}.{metric}.{part}"
                            put(
                                component_key,
                                money(value[field]),
                                "CNY",
                                ("本期已记录" if part == "current" else "对比期已记录") + label,
                                entity,
                                name,
                                period=period,
                            )
                            parts[f"{metric}.{part}"] = component_key
                    if complete:
                        component_key = f"{kind}.{index}.{metric}.delta"
                        put(
                            component_key,
                            money(now[field] - before[field]),
                            "CNY",
                            label + "变化贡献",
                            entity,
                            name,
                        )
                        parts[f"{metric}.delta"] = component_key
                entry["component_fact_keys"] = parts
            dimensions[kind].append(entry)
        if complete and len(dimensions[kind]) > 4:
            limitations.append(
                f"变化贡献：{kind}按变化金额绝对值展示前4项，完整维度与加总对账见证据文件"
            )
        dimensions[kind].sort(
            key=lambda x: (
                -abs(x["delta_cents"]) if x["delta_cents"] is not None else 0,
                x["entity"],
            )
        )
    if sample:
        limitations.append("本报告使用可复现模拟经营数据，不代表真实业务效果")
    return {
        **(
            {"answer_version": answer_version, "direct_answers": direct_answers}
            if answer_version >= 2
            else {}
        ),
        "tool": "sales",
        "review_profile": "commerce_review",
        "sample": sample,
        "dataset_id": dataset_id,
        "dataset_hash": content_hash,
        "schema_version": 1,
        "period": {"start": str(start), "end": str(end), "timezone": "Asia/Shanghai"},
        "currency": "CNY",
        "metrics": metrics,
        "fact_catalog": catalog,
        "row_count": current["coverage"]["observed_records"],
        "coverage": current["coverage"],
        "previous_period": {
            "start": str(prior_start),
            "end": str(prior_end),
            "net_revenue": money(prior["net"]),
            "gross_revenue": money(prior["revenue"]),
            "refund_amount": money(prior["refund"]),
            "paid_units": str(prior["paid"]),
            "refunded_units": str(prior["refunded"]),
            "coverage": prior["coverage"],
            "comparable": complete,
        },
        "dimensions": dimensions,
        **({"answer_contract": request_contract(objective)} if answer_version == 3 else {}),
        "findings": findings,
        "definitions": DEFINITIONS
        if adjacent
        else {
            **DEFINITIONS,
            "comparison": "用户指定的较早等长对比期；缺失记录不补零；未消除季节影响",
        },
        "limitations": limitations,
    }


def dataset_review(
    repo,
    user_id,
    tenant_id,
    dataset_id,
    start,
    end,
    *,
    bundle_ctx=None,
    answer_version=1,
    objective="",
):
    if bundle_ctx is not None:
        from datetime import date

        from app.commerce.review_inputs import bound_review
        from app.storage.repository import Conflict

        info, payload = bound_review(repo, bundle_ctx)
        if (str(start), str(end), dataset_id) != (
            info["period"]["start"],
            info["period"]["end"],
            info["sales_dataset_id"],
        ):
            raise Conflict("固定复盘工具请求与绑定输入不一致")
        result = calculate_periods(
            payload,
            start,
            end,
            dataset_id=dataset_id,
            content_hash=info["sales_content_hash"],
            sample=info["sample"],
            comparison_start=date.fromisoformat(info["comparison_period"]["start"]),
            comparison_end=date.fromisoformat(info["comparison_period"]["end"]),
            allowed_skus=info["allowed_skus"],
            answer_version=answer_version,
            objective=objective,
        )
        result.update(
            bundle_id=info["bundle_id"],
            bundle_hash=info["bundle_hash"],
            scope={"skus": info["allowed_skus"]},
            source_hashes={"sales": info["sales_content_hash"]},
            input_binding_version=f"commerce-review-bundle-v{answer_version}",
            source_samples=info.get("source_samples", {"sales": info["sample"]}),
        )
        for fact in result["fact_catalog"].values():
            if fact["entity_id"] == "all":
                fact.update(entity_id=",".join(info["allowed_skus"]), entity_name="授权范围汇总")
        result["limitations"].append(
            f"本报告绑定输入组合 {info['bundle_id']}，只计算其中{len(info['allowed_skus'])}个授权SKU；完整范围见证据scope.skus。"
        )
        return result
    with repo.session() as db:
        item = owned_dataset(db, dataset_id, user_id, tenant_id)
        # Copy out of the short DB transaction; no model/network call holds a DB lock.
        payload, digest, sample = item.payload, item.content_hash, item.sample
    return calculate_periods(
        payload,
        start,
        end,
        dataset_id=dataset_id,
        content_hash=digest,
        sample=sample,
        answer_version=answer_version,
        objective=objective,
    )
