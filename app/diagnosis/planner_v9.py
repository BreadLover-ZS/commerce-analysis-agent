"""Complete per-entity evidence may collectively cover the authorized scope."""

import json

from app.diagnosis import planner_v3, planner_v5, planner_v7, planner_v8
from app.diagnosis.schemas_v2 import StepProposalV2

DYNAMIC_GAP = "至少一项针对变化来源的动态核查"
DYNAMIC_TOOLS = {"rank_contributors", "analyze_refunds", "drilldown_series"}
SYSTEM_PROMPT_V9 = (
    planner_v8.SYSTEM_PROMPT_V8
    + """
已成功取得的逐对象证据可以共同覆盖当前授权范围。若所有对象均有完整可比的动态核查，且required_evidence_gaps为空，应结束；不要为了凑一个整范围调用再做无关贡献排序。
仅覆盖部分对象、缺行、不可比或越界证据不能满足全范围。仍须满足用户另行要求的贡献分解、退款分母、库存或规则证据；不要用序列代替这些不同要求。
"""
)


def dynamic_scope_covered(evidence, scope):
    allowed = set(scope["skus"])
    covered = set()
    for item in evidence:
        payload = item["payload"]
        selected = set(payload.get("scope", {}).get("skus", []))
        coverage = payload.get("coverage", {})
        if (
            payload.get("tool") in DYNAMIC_TOOLS
            and payload.get("status") == "ok"
            and payload.get("comparable") is True
            and selected
            and selected <= allowed
            and all(
                coverage.get(period, {}).get("complete") is True
                for period in ("current", "comparison")
            )
        ):
            covered.update(selected)
    return bool(allowed) and covered == allowed


def missing_coverage(query, evidence, scope):
    gaps = planner_v7.missing_coverage(query, evidence, scope)
    if dynamic_scope_covered(evidence, scope):
        gaps = [gap for gap in gaps if gap != DYNAMIC_GAP]
    return gaps


def computable_gap_tools(query, evidence, scope, catalog):
    if not missing_coverage(query, evidence, scope):
        return []
    return planner_v5.computable_gap_tools(query, evidence, scope, catalog)


def prompt(task, bundle, scope, steps, evidence, catalog):
    context = json.loads(planner_v8.prompt(task, bundle, scope, steps, evidence, catalog)[1][1])
    context.update(
        prompt_version="diagnosis-v9",
        required_evidence_gaps=missing_coverage(task["query"], evidence, scope),
        dynamic_scope_covered=dynamic_scope_covered(evidence, scope),
    )
    return [("system", SYSTEM_PROMPT_V9), ("human", json.dumps(context, ensure_ascii=False))]


def demo_proposal(task, scope, evidence, steps):
    if "那个商品" in task["query"] and len(scope["skus"]) > 1:
        return planner_v8.demo_proposal(task, scope, evidence, steps)
    if not planner_v3.unsupported_question(task["query"]) and not missing_coverage(
        task["query"], evidence, scope
    ):
        return StepProposalV2(
            action="stop", question="所问范围的必要证据已齐备", stop_reason="target_covered"
        )
    return planner_v8.demo_proposal(task, scope, evidence, steps)
