"""Question/evidence equivalence without requiring one tool path or runtime intent."""

import re
from datetime import date, timedelta
from decimal import Decimal

from evaluations.diagnosis_oracle import raw_totals


def literal_net_comparison(business, sources, report):
    """A bound comparison answers literal amounts, not attribution or other goals.

    Conservative allowlist: explicit two-period amount comparison, complete raw
    rows, exact final scope, and all three program-bound amounts. Never replace
    requested breakdown, cause, ranking, rates, daily or source-specific work.
    """
    query = business["query"].lower()
    if not re.search(r"销售|净额|net.?revenue", query) or not re.search(
        r"前期|两期|比较|相比|变化|变动|对比", query
    ):
        return False
    if re.search(
        r"贡献|分解|分商品|各商品|按商品|哪些(?:商品|sku)|哪件|排名|排序|品类|类目|占比|比例|"
        r"原因|为什么|为何|如何|解释|归因|驱动|影响|来源|因素|涉及|退款|退货|件数|分母|"
        r"按日|每日|日序列|逐日|哪天|库存|规则|支付|利润|转化|成本|订单|"
        r"时间|日期|何时|波动|top|rank|category|refund|daily|cause|gross|profit|conversion|roi",
        query,
    ):
        return False
    skus = report.get("scope", {}).get("skus", [])
    if not skus or not set(skus) <= set(business["scope_skus"]):
        return False
    if "那个商品" in query and len(skus) != 1:
        return False
    computed = [
        raw_totals(business["sales"], skus, business[name])
        for name in ("period", "comparison_period")
    ]
    if not all(c["complete"] and c["expected_records"] > 0 for _, c in computed):
        return False
    current, prior = [value["net_revenue"] for value, _ in computed]
    wanted = {
        "net_revenue.current": current,
        "net_revenue.comparison": prior,
        "net_revenue.delta": current - prior,
    }
    identity = ",".join(sorted(skus))
    if not any(
        p.get("tool") == "compare_periods"
        and p.get("metric") == "net_revenue"
        and p.get("status") == "ok"
        and set(p.get("scope", {}).get("skus", [])) == set(skus)
        and wanted.keys() <= p.get("fact_catalog", {}).keys()
        for p in sources
    ):
        return False
    return all(
        any(
            f.get("metric_key") == key
            and f.get("entity_id") == identity
            and Decimal(f["value"]) * 100 == value
            for f in report.get("facts", [])
        )
        for key, value in wanted.items()
    )


def multi_sku_daily_covered(business, sources):
    """Independent scope/day obligations, never production intent or a complete flag alone."""
    skus = business["scope_skus"]
    if len(set(skus)) < 2:
        return False
    for sku in skus:
        good = False
        for p in sources:
            if (
                p.get("tool") != "drilldown_series"
                or p.get("status") != "ok"
                or p.get("scope", {}).get("skus") != [sku]
                or p.get("granularity") != "day"
            ):
                continue
            wanted = {}
            for name, period in (
                ("current", business["period"]),
                ("comparison", business["comparison_period"]),
            ):
                day, last = date.fromisoformat(period["start"]), date.fromisoformat(period["end"])
                while day <= last:
                    span = {"start": str(day), "end": str(day)}
                    totals, coverage = raw_totals(business["sales"], [sku], span)
                    wanted[name, str(day)] = (totals, coverage)
                    day += timedelta(days=1)
            points = p.get("series", [])
            if len(points) != len(wanted) or {
                (s.get("period_name"), s.get("day")) for s in points
            } != set(wanted):
                continue
            valid = True
            for point in points:
                totals, coverage = wanted[point["period_name"], point["day"]]
                if point.get("coverage") != coverage or not coverage["complete"]:
                    valid = False
                    break
                if coverage["expected_records"]:
                    key = point.get("fact_keys", {}).get("net_revenue")
                    fact = p.get("fact_catalog", {}).get(key, {})
                    if (
                        fact.get("entity_id") != sku
                        or fact.get("unit") != "CNY"
                        or fact.get("period") != {"start": point["day"], "end": point["day"]}
                        or Decimal(fact.get("value", "NaN")) * 100 != totals["net_revenue"]
                    ):
                        valid = False
                        break
            good = good or valid
        if not good:
            return False
    return True
