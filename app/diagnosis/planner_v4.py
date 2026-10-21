"""Bounded correction of premature completion; v3 prompts remain frozen."""

import json

from app.diagnosis import planner_v3

SYSTEM_PROMPT_V4 = (
    planner_v3.SYSTEM_PROMPT_V3
    + """
服务器会拒绝仍可由授权工具补齐证据的target_covered；应参照correction中的缺口选择下一步或说明真正缺口。
多次比较退款金额、支付件数及退款件数不能代替程序绑定的退款占比；analyze_refunds一次返回这些事实及其分母边界。
提议只陈述待核对事项；不得在计划问题中先声称已证明某经营原因。停止条件与现有证据一致。
"""
)


def prompt(task, bundle, scope, steps, evidence, catalog):
    context = json.loads(planner_v3.prompt(task, bundle, scope, steps, evidence, catalog)[1][1])
    context["prompt_version"] = "diagnosis-v4"
    return [("system", SYSTEM_PROMPT_V4), ("human", json.dumps(context, ensure_ascii=False))]


def computable_gap_tools(query, evidence, scope, catalog):
    """Hints only. No tool is executed and existing limits/auth remain authoritative."""
    goals = {scope["objective"]} if scope.get("objective") else set(planner_v3.objectives(query))
    same = planner_v3.matching_payloads(evidence, scope)
    done = {p["tool"] for p in same if p.get("status") == "ok"}
    sales_complete = any(
        p["tool"]
        in {
            "inspect_dataset",
            "compare_periods",
            "rank_contributors",
            "analyze_refunds",
            "drilldown_series",
        }
        and p.get("status") == "ok"
        and p.get("comparable") is True
        for p in same
    )
    choices = set()
    if sales_complete:
        if "refunds" in goals and "analyze_refunds" not in done:
            choices.add("analyze_refunds")
        if "products" in goals and "rank_contributors" not in done:
            choices.add("rank_contributors")
        if (
            "daily" in goals
            and len(scope["skus"]) == 1
            and not planner_v3.daily_covered(evidence, scope)
        ):
            choices.add("drilldown_series")
        if "net_revenue" in goals and not done & {
            "rank_contributors",
            "analyze_refunds",
            "drilldown_series",
        }:
            choices.add("rank_contributors")
    if "inventory" in goals and not any(p["tool"] == "inventory_risk" for p in same):
        choices.add("inventory_risk")
    if "rules" in goals and not planner_v3.rule_payloads(evidence):
        choices.add("search_rule_documents")
    return sorted(choices & set(catalog))
