"""Bind narrowed net comparisons; all older planning/gap contracts stay frozen."""

import json

from app.diagnosis import planner_v3, planner_v4

NET_SCOPE_GAP = "当前对象范围的两期净销售额事实核对"
SYSTEM_PROMPT_V5 = (
    planner_v4.SYSTEM_PROMPT_V4
    + """
澄清缩小对象后，原较大范围基线不能代替新范围事实。若问题涉及销售额变化，应有compare_periods(metric=net_revenue, filters=当前范围)的程序事实。
退款分解不能代替当前范围的净额事实绑定，不能用自行相减的净额写入计划来宣称覆盖。
"""
)


def needs_net_comparison(query, scope):
    goals = {scope["objective"]} if scope.get("objective") else set(planner_v3.objectives(query))
    return "net_revenue" in goals


def has_net_comparison(evidence, scope):
    return any(
        p["tool"] == "compare_periods"
        and p.get("metric") == "net_revenue"
        and p.get("status") == "ok"
        and {"net_revenue.current", "net_revenue.comparison", "net_revenue.delta"}
        <= set(p.get("fact_catalog", {}))
        for p in planner_v3.matching_payloads(evidence, scope)
    )


def missing_coverage(query, evidence, scope):
    gaps = planner_v3.missing_coverage(query, evidence, scope)
    if needs_net_comparison(query, scope) and not has_net_comparison(evidence, scope):
        gaps.append(NET_SCOPE_GAP)
    return gaps


def computable_gap_tools(query, evidence, scope, catalog):
    tools = set(planner_v4.computable_gap_tools(query, evidence, scope, catalog))
    # A failed/incomparable exact comparison is a genuine source gap, not a
    # reason to keep requesting the same query. A parent scope does not count.
    compared = any(
        p["tool"] == "compare_periods" and p.get("metric") == "net_revenue"
        for p in planner_v3.matching_payloads(evidence, scope)
    )
    if needs_net_comparison(query, scope) and not compared and "compare_periods" in catalog:
        tools.add("compare_periods")
    return sorted(tools)


def prompt(task, bundle, scope, steps, evidence, catalog):
    context = json.loads(planner_v4.prompt(task, bundle, scope, steps, evidence, catalog)[1][1])
    context.update(
        prompt_version="diagnosis-v5",
        required_evidence_gaps=missing_coverage(task["query"], evidence, scope),
    )
    return [("system", SYSTEM_PROMPT_V5), ("human", json.dumps(context, ensure_ascii=False))]


def demo_proposal(task, scope, evidence, steps):
    from app.diagnosis.schemas_v2 import StepProposalV2

    proposal = planner_v3.demo_proposal(task, scope, evidence, steps)
    if (
        proposal.action == "stop"
        and proposal.stop_reason == "target_covered"
        and NET_SCOPE_GAP in missing_coverage(task["query"], evidence, scope)
    ):
        if "compare_periods" in computable_gap_tools(
            task["query"], evidence, scope, {"compare_periods": None}
        ):
            return StepProposalV2(
                action="tool",
                question="核对当前对象范围的两期净额",
                hypothesis_code="data_coverage",
                tool_name="compare_periods",
                arguments={"metric": "net_revenue", "filters": {"skus": scope["skus"]}},
            )
        proposal.stop_reason = "evidence_gap"
    return proposal
