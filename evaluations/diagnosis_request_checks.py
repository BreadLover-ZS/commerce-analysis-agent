"""Independent literal acceptance for v13. No production language adapter imports."""

import re

from evaluations.diagnosis_oracle import raw_totals


def expected_entities(query, allowed):
    include, exclude = set(), set()
    for clause in re.split(r"[，,；;。\n]", query):
        matches = [
            (sku, m)
            for sku in allowed
            for m in re.finditer(
                r"(?<![A-Za-z0-9_-])" + re.escape(sku) + r"(?![A-Za-z0-9_-])", clause
            )
        ]
        for sku, m in matches:
            before, after = clause[: m.start()], clause[m.end() :]
            negative = ("除" in before and re.search(r"以外|之外|外", after)) or re.search(
                r"不要|别看|不看|不分析|不核对|排除|剔除|除去|不包括|不包含", before
            )
            (exclude if negative else include).add(sku)
    return sorted((include or set(allowed)) - exclude)


def requested_dimension(query):
    if not re.search(r"贡献|排名|排行|排序|头部|抵消|哪些商品|哪个商品|定位.*(?:下降|增长)", query):
        return None
    return "category" if re.search(r"品类|类目", query) else "sku"


def requested_items(query, selected):
    if not re.search(
        r"逐(?:个)?(?:授权)?商品|每(?:一)?个(?:已)?(?:授权(?:的)?)?商品|各(?:个)?(?:授权)?商品|所有(?:已)?(?:授权(?:的)?)?商品|全部(?:授权)?商品|分商品|按商品|逐SKU|每个SKU|分别.*商品|商品.*分别",
        query,
        re.I,
    ):
        return []
    patterns = {
        "net_revenue": r"净销售额|净额|销售额",
        "gross_revenue": r"支付金额|支付额|付款金额",
        "refund_amount": r"退款金额|退款额",
        "paid_units": r"支付件数|支付数量|支付分母",
        "refunded_units": r"退款件数|退款数量",
    }
    metrics = []
    for metric, pattern in patterns.items():
        for clause in re.split(r"[，,；;。]", query):
            match = re.search(pattern, clause)
            if match and not re.search(
                r"(?:不|无需|不用|勿)(?:要|需要)?(?:计算|算|核算|列出)?$",
                clause[: match.start()].strip(),
            ):
                metrics.append(metric)
                break
    if re.search(r"差额|差值|变化|变动|增减|下降|增长", query):
        parts = ["current", "comparison", "delta"]
    elif "两期" in query or ("本期" in query and re.search(r"前期|比较期", query)):
        parts = ["current", "comparison"]
    elif re.search(r"比较期|前期", query):
        parts = ["comparison"]
    elif "本期" in query:
        parts = ["current"]
    else:
        parts = ["current", "comparison", "delta"]
    return [(sku, metric, part) for sku in selected for metric in metrics for part in parts]


def completion_checks(business, report, sources, selected):
    # Partial work is not relabelled complete; the expected outcome still decides
    # whether a budget stop is a success for the particular frozen case.
    complete = report.get("stop_reason") == "target_covered"
    dim = requested_dimension(business["query"])
    dimension_ok = (
        not complete
        or not dim
        or any(
            p.get("tool") == "rank_contributors"
            and p.get("dimension") == dim
            and p.get("scope", {}).get("skus") == selected
            and (p.get("reconciliation") or {}).get("balanced") is True
            for p in sources
        )
    )
    if complete and dim:
        dimension_ok = dimension_ok and any(
            f.get("entity_type") == dim
            for f in report.get("facts", [])
            if f.get("metric_key", "").startswith("contribution.")
        )
    matrix_ok = True
    missing = []
    for sku, metric, part in requested_items(business["query"], selected):
        parts = (
            ("period", "comparison_period")
            if part == "delta"
            else (("period",) if part == "current" else ("comparison_period",))
        )
        raw = [raw_totals(business["sales"], [sku], business[p]) for p in parts]
        if any(c["expected_records"] == 0 for _, c in raw):
            continue
        found = any(
            f.get("entity_id") == sku
            and f.get("metric_key") in [f"{metric}.{part}", f"component.{sku}.{metric}.{part}"]
            and f.get("unit") == ("count" if metric in ["paid_units", "refunded_units"] else "CNY")
            for f in report.get("facts", [])
        )
        if not found:
            missing.append((sku, metric, part))
            if complete:
                matrix_ok = False
    full = report.get("open_question_details", [])
    shown = report.get("open_questions", [])
    gap_ok = (
        isinstance(full, list)
        and shown == full[:20]
        and len(full) == len(set(full))
        and report.get("gap_summary")
        == {"total": len(full), "displayed": len(shown), "omitted": len(full) - len(shown)}
        and all(any("/".join(item) in q for q in full) for item in missing)
    )
    return {
        "requested_dimension_bound": bool(dimension_ok),
        "requested_per_entity_items": matrix_ok,
        "complete_gap_inventory": bool(gap_ok),
    }
