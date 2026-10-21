"""Expose cross-field tool constraints without relaxing server validation."""

import json

from app.diagnosis import planner_v3, planner_v5, planner_v7
from app.diagnosis.graph_constraints import SERIES_CONSTRAINT

SYSTEM_PROMPT_V8 = (
    planner_v7.SYSTEM_PROMPT_V7
    + """
tool_constraints列出JSON字段类型之外的服务端组合约束。drilldown_series每次只能指定一个SKU或一个品类；不能把多个SKU放进同一次调用。若确需逐日核对多个对象，分别提议，每次仍计入原步骤和调用预算。
商品启用和缺行边界先核对已有inspect_dataset/compare_periods的覆盖证据；启用前不适用不等于缺行或零销量。不要为已经覆盖的边界重复下钻，也不能把单个对象的序列当作所有对象已核对。
参数被拒时读取correction.constraints并改变对应参数；不得重发同一无效组合。服务端不会代你选择对象、分拆调用或扩大授权范围。
"""
)


def prompt(task, bundle, scope, steps, evidence, catalog):
    context = json.loads(planner_v7.prompt(task, bundle, scope, steps, evidence, catalog)[1][1])
    context.update(
        prompt_version="diagnosis-v8",
        tool_constraints={"drilldown_series": SERIES_CONSTRAINT}
        if "drilldown_series" in catalog
        else {},
    )
    return [("system", SYSTEM_PROMPT_V8), ("human", json.dumps(context, ensure_ascii=False))]


def demo_proposal(task, scope, evidence, steps):
    """Offline simulator chooses one entity per step; live never uses this policy."""
    from app.diagnosis.schemas_v2 import StepProposalV2

    proposal = planner_v5.demo_proposal(task, scope, evidence, steps)
    goals = (
        {scope["objective"]}
        if scope.get("objective")
        else set(planner_v7.objectives(task["query"]))
    )
    if (
        "daily" not in goals
        or "那个商品" in task["query"]
        or len(scope["skus"]) < 2
        or planner_v3.unsupported_question(task["query"])
    ):
        return proposal
    covered = planner_v3.daily_covered(evidence, scope)
    if covered:
        if planner_v7.missing_coverage(task["query"], evidence, scope):
            return proposal
        return StepProposalV2(
            action="stop", question="已逐项核对授权对象日记录", stop_reason="target_covered"
        )
    attempted = {
        sku
        for step in steps
        if step["proposal"].get("tool_name") == "drilldown_series"
        for sku in step["proposal"].get("arguments", {}).get("filters", {}).get("skus", [])
    }
    for sku in scope["skus"]:
        if sku not in attempted:
            return StepProposalV2(
                action="tool",
                question="逐项核对授权对象的日记录",
                hypothesis_code="daily_distribution",
                tool_name="drilldown_series",
                arguments={"filters": {"skus": [sku]}},
            )
    return StepProposalV2(
        action="stop",
        question="保留逐对象日记录及仍存在的缺口",
        stop_reason="evidence_gap",
    )
