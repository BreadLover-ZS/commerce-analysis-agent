"""Separate missing inputs from proxy eligibility and bound visible step text."""

import copy
import json

from app.diagnosis import planner_v9

INVENTORY_GAP = "授权范围内的完整库存快照、完整销售窗口与提前期代理核对"
REASONS = {
    "missing_inventory": "缺少授权商品的库存快照",
    "future_snapshot": "库存快照晚于分析期末",
    "stale_snapshot": "库存快照超过时点策略允许范围",
    "incomplete_sales_window": "销售窗口缺行或不适用",
    "zero_demand": "支付件数为零，覆盖天数代理不可计算",
    "missing_lead_days": "缺少配置提前期，不能作提前期阈值比较",
}
TOOL_TEXT = {
    "inspect_dataset": "核查授权数据覆盖",
    "compare_periods": "核对授权范围的两期指标",
    "rank_contributors": "核对变化贡献及总体对账",
    "drilldown_series": "核对授权对象的日序列",
    "analyze_refunds": "核对退款金额、件数及支付分母",
    "inventory_risk": "核对库存覆盖代理与计算条件",
    "search_rule_documents": "查阅授权规则原文",
}


def public_proposal(proposal):
    """Keep the original model output in its ledger, never as a verified claim."""
    result = copy.deepcopy(proposal)
    result["question"] = (
        TOOL_TEXT.get(result.get("tool_name"), "提出受限业务核查，结果以程序证据为准")
        if result["action"] == "tool"
        else "请选择本次核查的对象"
        if result["action"] == "clarify"
        else "结束本步核查；实际停止原因与未完成项以程序判定为准"
        if result["action"] == "stop"
        else "提议未通过校验"
    )
    if "expected_evidence" in result:
        result["expected_evidence"] = "返回值、覆盖状态与缺口以执行后的程序证据为准"
    return result


def separate_inventory_coverage(payload):
    result = copy.deepcopy(payload)
    items = result["items"]
    result["coverage"].update(
        snapshot_complete=all(i["snapshot_day"] is not None for i in items),
        sales_window_complete=all(
            i.get("sales_coverage", {}).get("complete") is True for i in items
        ),
        lead_days_complete=all("lead_days" in i["fact_keys"] for i in items),
        proxy_calculable=all(i["coverage_days"] is not None for i in items),
        threshold_comparable=all(i["below_lead_proxy"] is not None for i in items),
    )
    result["coverage"]["meaning"] = "complete仅表示全部代理比较条件齐备，不代表库存快照是否存在"
    return result


def missing_coverage(query, evidence, scope):
    gaps = planner_v9.missing_coverage(query, evidence, scope)
    if INVENTORY_GAP not in gaps:
        return gaps
    allowed = set(scope["skus"])
    items = {}
    for e in evidence:
        p = e["payload"]
        if p.get("tool") == "inventory_risk" and p.get("status") == "ok":
            for item in p.get("items", []):
                if item["sku"] in allowed:
                    items[item["sku"]] = item
    if set(items) != allowed:
        return gaps  # No full-scope inventory query yet, not proof a snapshot is absent.
    gaps.remove(INVENTORY_GAP)
    gaps.extend(
        f"{sku}：{REASONS.get(item['reason'], '库存代理比较条件未齐备')}"
        for sku, item in sorted(items.items())
        if item["reason"]
    )
    return gaps


def computable_gap_tools(query, evidence, scope, catalog):
    if not missing_coverage(query, evidence, scope):
        return []
    return planner_v9.computable_gap_tools(query, evidence, scope, catalog)


def prompt(task, bundle, scope, steps, evidence, catalog):
    context = json.loads(planner_v9.prompt(task, bundle, scope, steps, evidence, catalog)[1][1])
    context.update(
        prompt_version="diagnosis-v10",
        required_evidence_gaps=missing_coverage(
            query=task["query"], evidence=evidence, scope=scope
        ),
    )
    system = (
        planner_v9.SYSTEM_PROMPT_V9
        + "\n库存综合coverage.complete=false不表示缺库存快照。分别查看snapshot_complete、sales_window_complete、lead_days_complete、proxy_calculable、threshold_comparable及逐SKU reason；只可陈述实际缺口。用户可见步骤说明由程序生成，模型原提议只保留在调用账本。"
    )
    return [("system", system), ("human", json.dumps(context, ensure_ascii=False))]


def demo_proposal(task, scope, evidence, steps):
    # Preserve v9's bounded policy while using the new explicit gap vocabulary.
    return planner_v9.demo_proposal(task, scope, evidence, steps)
