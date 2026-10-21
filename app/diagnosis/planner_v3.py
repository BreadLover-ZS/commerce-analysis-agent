"""Intent-scoped decisions using frozen v2 tools and evidence, not a new queue."""

import json

from app.diagnosis.intent_v3 import objectives, unsupported_question
from app.diagnosis.planner import prompt as sales_prompt
from app.diagnosis.planner_v2 import SYSTEM_PROMPT_V2
from app.diagnosis.schemas_v2 import StepProposalV2

SYSTEM_PROMPT_V3 = (
    SYSTEM_PROMPT_V2
    + """
对象名不等于分析目标。只满足当前问题的必要证据，不因提到商品就追加贡献分解，不因规则主题含退款就追加销售退款分析。
否定约束（例如不要计算利润）不等于要求计算该指标。允许的剩余工作仍可核对，工具权限仍只由服务端决定。
先读基线已有的覆盖和两期证据；inspect_dataset没有参数，不接受filters。只有明确单个SKU或单个品类才可按日下钻。
必要证据已覆盖时结束；有明确来源缺口时说明不足。不要靠重复基线、改变TopK或重新措辞制造新信息。
上次提议被拒后参照类型化字段错误及工具Schema修正，不要重复同一无效参数。规则检索未命中可换为原问题中的短主题词，在原清单内再查。
"""
)


def matching_payloads(evidence, scope):
    return [
        e["payload"]
        for e in evidence
        if sorted(e["payload"].get("scope", {}).get("skus", [])) == sorted(scope["skus"])
    ]


def rule_payloads(evidence):
    # Rule evidence is bound to the frozen manifest by the tool gateway. It has
    # no SKU scope: requiring one would discard every authorized quote.
    return [e["payload"] for e in evidence if e["payload"]["tool"] == "search_rule_documents"]


def daily_covered(evidence, scope):
    covered = set()
    for item in evidence:
        payload = item["payload"]
        skus = set(payload.get("scope", {}).get("skus", []))
        if (
            payload["tool"] == "drilldown_series"
            and payload.get("status") == "ok"
            and payload.get("granularity") == "day"
            and skus <= set(scope["skus"])
            and payload.get("series")
            and all(p["coverage"]["complete"] for p in payload["series"])
        ):
            covered.update(skus)
    return covered == set(scope["skus"])


def missing_coverage(query, evidence, scope):
    needed = {scope["objective"]} if scope.get("objective") else set(objectives(query))
    same = matching_payloads(evidence, scope)
    usable = [p for p in same if p.get("status") == "ok"]
    tools = {p["tool"] for p in usable}
    missing = []
    if "refunds" in needed and "analyze_refunds" not in tools:
        missing.append("退款金额、件数和分母核对")
    if "products" in needed and "rank_contributors" not in tools:
        missing.append("授权范围内的完整贡献分解")
    if "daily" in needed and not daily_covered(evidence, scope):
        missing.append("授权对象的日序列核对")
    if "coverage" in needed and not any(
        p.get("coverage", {}).get("current") is not None for p in same
    ):
        missing.append("授权对象的记录覆盖核对")
    dynamic = {"rank_contributors", "analyze_refunds", "drilldown_series"}
    if "net_revenue" in needed and not any(
        p["tool"] in dynamic and set(p["scope"]["skus"]) <= set(scope["skus"]) for p in usable
    ):
        missing.append("至少一项针对变化来源的动态核查")
    stock = [p for p in same if p["tool"] == "inventory_risk"]
    if "inventory" in needed and (not stock or not any(p["coverage"]["complete"] for p in stock)):
        missing.append("授权范围内的完整库存快照、完整销售窗口与提前期代理核对")
    if "rules" in needed and not any(
        p.get("status") == "ok" and p.get("quotes") for p in rule_payloads(evidence)
    ):
        missing.append("固定文档清单内的相关规则原文引用")
    return missing


def prompt(task, bundle, scope, steps, evidence, catalog):
    # Reuse the established bounded evidence/context compressor; replace only
    # the versioned intent and gap interpretation, never the original prompt.
    context = json.loads(sales_prompt(task, bundle, scope, steps, evidence, catalog)[1][1])
    context.update(
        prompt_version="diagnosis-v3",
        schema=StepProposalV2.model_json_schema(),
        objectives=objectives(task["query"]),
        required_evidence_gaps=missing_coverage(task["query"], evidence, scope),
        bound_sources=task["parameters"]["sources"],
    )
    if len(json.dumps(context, ensure_ascii=False).encode()) > 96 * 1024:
        for value in context["evidence"]:
            for key in ("quotes", "items", "fact_catalog", "series", "products", "contributors"):
                value.pop(key, None)
            value["summary_truncated"] = True
    return [("system", SYSTEM_PROMPT_V3), ("human", json.dumps(context, ensure_ascii=False))]


def demo_proposal(task, scope, evidence, steps):
    """Offline simulator only; real selection remains the model's responsibility."""
    if unsupported_question(task["query"]):
        return StepProposalV2(
            action="stop", question="保留可计算事实与不支持指标边界", stop_reason="evidence_gap"
        )
    if "那个商品" in task["query"] and len(scope["skus"]) > 1:
        return StepProposalV2(
            action="clarify", question="请选择已授权商品", clarification_scope="sku"
        )
    goals = {scope["objective"]} if scope.get("objective") else set(objectives(task["query"]))
    same = matching_payloads(evidence, scope)
    done = {p["tool"] for p in same}
    done.update(p["tool"] for p in rule_payloads(evidence))
    attempted = {s["proposal"].get("tool_name") for s in steps}
    filters = {"skus": scope["skus"]}
    choices = [
        ("inventory", "inventory_risk", "inventory_proxy", {"filters": filters}),
        ("rules", "search_rule_documents", "rule_applicability", {"query": task["query"][:300]}),
        ("refunds", "analyze_refunds", "refund_amount_change", {"filters": filters}),
        (
            "products",
            "rank_contributors",
            "sales_concentration",
            {"filters": filters, "dimension": "category" if "品类" in task["query"] else "sku"},
        ),
        ("daily", "drilldown_series", "daily_distribution", {"filters": filters}),
        ("net_revenue", "rank_contributors", "sales_concentration", {"filters": filters}),
    ]
    for goal, tool, code, args in choices:
        if goal not in goals or tool in done or tool in attempted:
            continue
        if goal == "net_revenue" and done & {
            "rank_contributors",
            "analyze_refunds",
            "drilldown_series",
        }:
            continue
        if tool == "drilldown_series" and len(scope["skus"]) != 1:
            return StepProposalV2(
                action="clarify", question="逐日核对须选择一个已授权商品", clarification_scope="sku"
            )
        return StepProposalV2(
            action="tool",
            question="核对所问范围的必要证据",
            hypothesis_code=code,
            tool_name=tool,
            arguments=args,
            expected_evidence="固定来源中的可核对事实或明确缺口",
        )
    return StepProposalV2(
        action="stop",
        question="保留已核对观察与明确资料边界",
        stop_reason="evidence_gap"
        if missing_coverage(task["query"], evidence, scope)
        else "target_covered",
    )
