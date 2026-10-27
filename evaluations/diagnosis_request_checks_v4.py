"""Independent boundary-v2 answer checks; uses raw rows and literal requests only."""

import re
from decimal import Decimal, DecimalException

from evaluations.diagnosis_oracle import raw_totals
from evaluations.diagnosis_request_checks_v2 import requested_dimensions
from evaluations.diagnosis_request_checks_v2 import requested_items as previous_items

PARTS = ("current", "comparison", "delta")
MONEY = ("net_revenue", "gross_revenue", "refund_amount")


def boundary_request(query):
    # Independent literal-token rules. Do not import the production detector or
    # derive truth from two detectors agreeing; author examples are the oracle.
    segments = re.split(r"[，,；;。！？!?]", query)
    usable = []
    for segment in segments:
        rejected = any(
            prefix + verb in segment
            for prefix in ["无需", "无须", "不用", "不必", "不要", "不需", "禁止", "别", "暂不"]
            for verb in ["说明", "解释", "讨论", "列明", "列出"]
        )
        if not rejected:
            usable.append(segment)
    unavailable = any(
        word in piece
        for piece in usable
        for word in ["不适用", "未启用", "启用前", "不能比较", "不能用于比较"]
    )
    for segment in usable:
        old_style = bool(
            re.search(
                r"说明.*分母问题|有什么限制|不可比较的边界|尚不能比较|尚不能计算|哪些.*(?:可算|不能算)|哪些.*(?:具备|缺少)|列明.*(?:资料|条件)|说明.*(?:缺行|缺数|缺失).*(?:限制|边界)",
                segment,
            )
        )
        action = any(
            word in segment
            for word in ["说明", "解释", "列明", "列出", "哪些", "为什么", "为何", "有什么"]
        )
        limit = any(
            word in segment for word in ["边界", "限制", "局限", "原因", "为什么", "为何", "哪些"]
        )
        local = any(
            word in segment
            for word in [
                "不适用",
                "未启用",
                "启用前",
                "不能比较",
                "不能用于比较",
                "不能计算",
                "无法比较",
                "无法计算",
                "不支持",
                "不成立",
                "局限",
            ]
        )
        if old_style or action and limit and (local or unavailable):
            return True
    return False


def requested_items(query, selected):
    if boundary_request(query) and not re.search(
        r"列出|给出|计算|核算|多少|差额|差值|核对(?:本期|两期|比较期|前期)?(?:的)?(?:退款金额|支付金额|净销售额|净额)",
        query,
    ):
        return []
    result = previous_items(query, selected) + previous_items(query, selected, scope=True)
    if re.search(r"各变化多少|变化了多少|变化金额|三个变化额|差额|差值", query) and not re.search(
        r"两期|本期|比较期|前期|汇总|逐日|每日", query
    ):
        result = [r for r in result if r[2] == "delta"]
    return result


def displayed_facts(report, sources):
    if "facts" in report:
        return report["facts"]
    result = []
    for section in report.get("sections", []):
        for claim in section["claims"]:
            for m in claim.get("metrics", []):
                result.append(
                    {
                        **m,
                        "metric_key": m["key"],
                        "period": {"start": m["period_start"], "end": m["period_end"]},
                    }
                )
    return result


def metric_part(key, payload):
    for metric in (*MONEY, "paid_units", "refunded_units", "refund_ratio"):
        base = "refund_rate_pct" if metric == "refund_ratio" else metric
        for part, fixed in (
            ("current", base),
            ("comparison", "comparison_" + base),
            ("delta", base + "_delta"),
        ):
            if key in (fixed, f"{metric}.{part}") or key.endswith(f".{metric}.{part}"):
                return metric, part
    return None, None


def amount_identity(business, report, sources, selected):
    """Nine typed source facts, exact spans/units/scope/raw cents, plus requested display."""
    raw = {
        part: raw_totals(business["sales"], selected, business[name])
        for part, name in (("current", "period"), ("comparison", "comparison_period"))
    }
    if not all(cov["complete"] and cov["expected_records"] > 0 for _, cov in raw.values()):
        return False
    entity = ",".join(sorted(selected))
    try:
        for metric in MONEY:
            for part in PARTS:
                expected = (
                    raw["current"][0][metric] - raw["comparison"][0][metric]
                    if part == "delta"
                    else raw[part][0][metric]
                )
                span = business["comparison_period"] if part == "comparison" else business["period"]
                if not any(
                    f.get("entity_id") == entity
                    and f.get("unit") == "CNY"
                    and metric_part(key, p) == (metric, part)
                    and {
                        "start": f.get("period", {}).get("start", f.get("period_start")),
                        "end": f.get("period", {}).get("end", f.get("period_end")),
                    }
                    == span
                    and Decimal(f["value"]) * 100 == expected
                    for p in sources
                    if p.get("scope", {}).get("skus") == selected
                    for key, f in p.get("fact_catalog", {}).items()
                ):
                    return False
        # The native source path qualifies only if every explicitly requested
        # amount is also present in the final answer (absolute values may be optional).
        shown = displayed_facts(report, sources)
        for identity, metric, part in requested_items(business["query"], selected):
            if metric not in MONEY:
                continue
            members = selected if identity == entity else [identity]
            now, _ = raw_totals(business["sales"], members, business["period"])
            prior, _ = raw_totals(business["sales"], members, business["comparison_period"])
            expected = (
                now[metric] - prior[metric]
                if part == "delta"
                else (now if part == "current" else prior)[metric]
            )
            if not any(
                f.get("entity_id") == identity
                and metric_part(f["metric_key"], {}) == (metric, part)
                and f.get("unit") == "CNY"
                and Decimal(f["value"]) * 100 == expected
                for f in shown
            ):
                return False
        return all(
            raw[p][0]["net_revenue"] == raw[p][0]["gross_revenue"] - raw[p][0]["refund_amount"]
            for p in raw
        )
    except (KeyError, ValueError, TypeError, DecimalException):
        return False


def reported_status(report):
    done = report.get("answer_completion", {}).get("status")
    if "sections" in report:
        text = " ".join(
            c["text"]
            for s in report["sections"]
            for c in s["claims"]
            if c["id"] == "review-answer-completion"
        )
        done = (
            "incomplete"
            if "尚未答齐" in text
            else "boundary_explained"
            if "边界与限制已答齐" in text or "缺口与限制已答齐" in text
            else "fulfilled"
            if "请求已答齐" in text
            else None
        )
    return done


def completion_checks(business, report, sources, selected):
    query = business["query"]
    displayed = displayed_facts(report, sources)
    missing, unavailable = [], []
    for entity, metric, part in requested_items(query, selected):
        members = selected if entity == ",".join(sorted(selected)) else [entity]
        periods = (
            ["period", "comparison_period"]
            if part == "delta"
            else ["period" if part == "current" else "comparison_period"]
        )
        raw = [raw_totals(business["sales"], members, business[p]) for p in periods]
        if (entity, metric, part) in set(previous_items(query, selected)) and any(
            cov["expected_records"] == 0 for _, cov in raw
        ):
            continue
        if not all(cov["complete"] and cov["expected_records"] > 0 for _, cov in raw):
            unavailable.append((entity, metric, part))
            continue
        expected = raw[0][0][metric] - raw[1][0][metric] if part == "delta" else raw[0][0][metric]
        unit = "count" if metric.endswith("units") else "CNY"
        if not any(
            f.get("entity_id") == entity
            and f.get("unit") == unit
            and metric_part(f["metric_key"], {}) == (metric, part)
            and Decimal(f["value"]) * (1 if unit == "count" else 100) == expected
            for f in displayed
        ):
            missing.append((entity, metric, part))
    if re.search(r"库存|补货", query):
        shown_keys = {f["metric_key"] for f in displayed}
        for p in sources:
            if p.get("tool") == "inventory_risk":
                missing.extend(
                    (i["sku"], "inventory", k)
                    for i in p.get("items", [])
                    for k in i.get("fact_keys", {}).values()
                    if k not in shown_keys
                )
    if re.search(r"逐日|每日|日序列", query):
        for p in sources:
            if p.get("tool") == "drilldown_series":
                missing.extend(
                    (f["entity_id"], "daily", key)
                    for key, f in p.get("fact_catalog", {}).items()
                    if key.startswith("series.")
                    and key.endswith(".net_revenue")
                    and not any(
                        v.get("metric_key") == key
                        and v.get("entity_id") == f["entity_id"]
                        and v["value"] == f["value"]
                        for v in displayed
                    )
                )
    done = reported_status(report)
    gaps = report.get("gap_records", [])
    if "sections" in report:
        gaps = next(
            (
                p.get("answer_completion", {}).get("data_gaps", [])
                for p in sources
                if p.get("answer_version") in {4, 5, 6}
            ),
            [],
        )
    complete = done in {"fulfilled", "boundary_explained"}
    stock = [
        p
        for p in sources
        if p.get("tool") == "inventory_risk" and p.get("scope", {}).get("skus") == selected
    ]
    source_bound = all(g.get("evidence_refs") for g in gaps)
    for g in gaps:
        code = g.get("code")
        if code in {"missing_sales_rows", "period_not_applicable"}:
            span = business["period" if g.get("period") == "current" else "comparison_period"]
            _, cov = raw_totals(business["sales"], selected, span)
            source_bound = source_bound and (
                not cov["complete"]
                if code == "missing_sales_rows"
                else cov["expected_records"] == 0
            )
        elif code == "zero_paid_denominator":
            span = business["period" if g.get("period") == "current" else "comparison_period"]
            totals, cov = raw_totals(business["sales"], selected, span)
            source_bound = source_bound and cov["complete"] and totals["paid_units"] == 0
        else:
            source_bound = source_bound and any(
                i.get("reason") == code and i["sku"] in g.get("scope", {}).get("skus", [])
                for p in stock
                for i in p.get("items", [])
            )
    boundary_ok = (
        boundary_request(query) and bool(gaps) and source_bound and not unavailable and not missing
    )
    if "库存" in query or "补货" in query:
        boundary_ok = (
            boundary_ok
            and bool(stock)
            and all(
                i.get("snapshot_day") and i.get("coverage_days") is not None
                for p in stock
                for i in p.get("items", [])
            )
        )
    dimensions_ok = all(
        any(
            (
                p.get("tool") == "rank_contributors"
                and p.get("dimension") == dim
                and p.get("reconciliation", {}).get("balanced")
                and p.get("scope", {}).get("skus") == selected
            )
            or (
                p.get("answer_version") in {4, 5, 6}
                and p.get("previous_period", {}).get("comparable")
                and p.get("dimensions", {}).get("products" if dim == "sku" else "categories")
            )
            for p in sources
        )
        for dim in requested_dimensions(query)
    )
    period_coverage = [
        raw_totals(business["sales"], selected, business[span])[1]
        for span in ["period", "comparison_period"]
    ]
    only_not_applicable = any(c["expected_records"] == 0 for c in period_coverage) and not any(
        c["missing_records"] for c in period_coverage
    )
    texts = " ".join(c["text"] for s in report.get("sections", []) for c in s["claims"])
    not_applicable_truth = not only_not_applicable or "源数据仍不完整" not in texts
    return {
        "v5_not_applicable_not_claimed_missing": not_applicable_truth,
        "v15_required_values_displayed": not missing,
        "v15_completion_not_self_certified": done
        in {"fulfilled", "boundary_explained", "incomplete"}
        and (
            not complete
            or not missing
            and not unavailable
            and dimensions_ok
            and (not gaps or done == "boundary_explained")
        ),
        "v15_boundary_requires_source_proof": done != "boundary_explained" or boundary_ok,
        "v15_execution_failure_not_complete": report.get("stop_reason")
        not in {
            "invalid_decision",
            "usage_unknown",
            "step_budget",
            "model_budget",
            "tool_budget",
            "no_information",
            "no_evidence",
        }
        or not complete,
        "v15_requested_dimensions": not complete or dimensions_ok,
    }
