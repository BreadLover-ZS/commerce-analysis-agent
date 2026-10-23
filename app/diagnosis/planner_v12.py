"""Request-scoped completion and bounded capability-aware planning (registry 5).

Older checkpoints keep their planners. Numeric evidence and authorization remain
owned by the gateway; this adapter only describes remaining work to the model.
"""

import json
import re

from app.commerce.answer_contract import request_contract, unsupported_gaps
from app.diagnosis import planner_v3, planner_v5, planner_v9, planner_v11
from app.diagnosis.graph_constraints import SERIES_CONSTRAINT
from app.diagnosis.planner import SYSTEM_PROMPT
from app.diagnosis.schemas_v2 import StepProposalV2
from app.storage.repository import BudgetExceeded

objectives = planner_v11.objectives
unsupported_question = planner_v11.unsupported_question
component_obligations = planner_v11.component_obligations
NO_CURRENT = "本期没有可核对的已记录事实"
INCOMPLETE = "两期数据覆盖不足，需补充原始数据并新建任务"


def literal_comparison(query):
    """Conservative literal amount request; ambiguous analysis stays exploratory."""
    c = request_contract(query)
    clean = c["canonical_query"]
    requested = {i["metric"] for i in c["items"] if i["polarity"] == "requested"}
    return bool(
        requested == {"net_revenue"}
        and not c["unsupported_requests"]
        and set(c["goals"]) <= {"net_revenue", "coverage"}
        and re.search(r"本期|两期|比较期|前期|对比|相比", clean)
        and not re.search(
            r"原因|为什么|为何|怎么|如何|解释|归因|驱动|影响|来源|因素|分析|诊断|探索|"
            r"贡献|分解|排名|各商品|逐商品|每个|品类|按日|每日|逐日|哪天|波动|趋势|走势|"
            r"库存|规则|退款|退货|件数|分母|支付|占比|比例|cause|daily|rank|refund",
            clean,
            re.I,
        )
    )


def source_gaps(query, evidence, scope):
    """Whole requested sales scope only; a local pre-activation is not a global gap."""
    if not set(objectives(query)) & {"net_revenue", "refunds", "products", "daily", "coverage"}:
        return []
    comparisons = [
        p
        for p in planner_v3.matching_payloads(evidence, scope)
        if p.get("tool") == "compare_periods" and p.get("metric") == "net_revenue"
    ]
    if any(
        p.get("coverage", {}).get("current", {}).get("observed_records") == 0 for p in comparisons
    ):
        return [NO_CURRENT]
    if any(p.get("comparable") is False for p in comparisons):
        return [INCOMPLETE]
    return []


def missing_coverage(query, evidence, scope):
    gaps = planner_v11.missing_coverage(query, evidence, scope)
    if literal_comparison(query):
        gaps = [g for g in gaps if g != planner_v9.DYNAMIC_GAP]
    return list(dict.fromkeys(gaps + source_gaps(query, evidence, scope)))


def pending_computable(query, evidence, scope):
    return [g for g in missing_coverage(query, evidence, scope) if g not in unsupported_gaps(query)]


def computable_gap_tools(query, evidence, scope, catalog):
    if not missing_coverage(query, evidence, scope):
        return []
    return planner_v11.computable_gap_tools(query, evidence, scope, catalog)


def baseline_complete(query, evidence, scope):
    return (
        literal_comparison(query)
        and not ("那个商品" in query and len(scope["skus"]) > 1)
        and planner_v5.has_net_comparison(evidence, scope)
        and not missing_coverage(query, evidence, scope)
    )


CAPABILITIES = {
    "inspect_dataset": {"outputs": ["authorized_input_coverage"], "scope": "frozen_bundle"},
    "compare_periods": {
        "outputs": ["one_metric.current", "one_metric.comparison", "one_metric.delta"],
        "scope": "selected_skus",
    },
    "rank_contributors": {
        "outputs": [
            "net_contribution_reconciliation",
            "all_skus.net_revenue.current/comparison/delta",
            "all_skus.gross_revenue.current/comparison/delta",
            "all_skus.refund_amount.current/comparison/delta",
        ],
        "scope": "all_selected_skus",
        "top_k_affects": "ranking_display_only; component facts cover ALL selected SKUs",
    },
    "analyze_refunds": {
        "outputs": [
            "refund_amount",
            "refunded_units",
            "paid_units_denominator",
            "book_period_refund_ratio",
        ],
        "scope": "selected_skus",
    },
    "drilldown_series": {
        "outputs": ["one_entity.daily_series", "both_periods"],
        "scope": "exactly_one_sku_or_category",
    },
    "inventory_risk": {
        "outputs": ["snapshot_coverage", "sales_window_coverage", "lead_days", "proxy_conditions"],
        "scope": "selected_skus",
    },
    "search_rule_documents": {
        "outputs": ["authorized_rule_quotes", "applicability_metadata"],
        "scope": "frozen_document_manifest",
    },
}


def capability_frontier(query, evidence, scope, catalog):
    pending = [
        i for i in component_obligations(query, evidence, scope) if i["status"] == "not_queried"
    ]
    gaps = missing_coverage(query, evidence, scope)
    result = {name: dict(CAPABILITIES[name]) for name in catalog if name in CAPABILITIES}
    if "rank_contributors" in result:
        result["rank_contributors"].update(
            pending_component_items_covered_in_one_call=len(pending),
            recommended_arguments={
                "dimension": "sku",
                "top_k": min(10, len(scope["skus"])),
                "filters": {"skus": scope["skus"]},
            },
            priority="batch_remaining_components"
            if pending
            else "only_if_contribution_or_exploration_missing",
        )
    if "compare_periods" in result:
        result["compare_periods"]["maximum_component_items_per_call"] = 3
        result["compare_periods"]["remaining_components_require_at_least_calls"] = len(
            {(i["sku"], i["metric"]) for i in pending}
        )
    for name, required in (
        ("analyze_refunds", "退款金额、件数和分母核对"),
        ("rank_contributors", "授权范围内的完整贡献分解"),
    ):
        if name in result:
            result[name]["explicit_requirement_pending"] = required in gaps
    return result


def evidence_summary(item):
    p = item["payload"]
    result = {
        k: p[k]
        for k in (
            "call_id",
            "bundle_id",
            "bundle_hash",
            "query_spec_hash",
            "tool",
            "status",
            "scope",
            "metric",
            "coverage",
            "comparable",
            "limitations",
            "granularity",
            "component_coverage",
        )
        if k in p
    }
    result["evidence_id"] = item["id"]
    result["fact_count"] = len(p.get("fact_catalog", {}))
    # Completion reads the full ledger server-side. The model needs identities,
    # coverage, capability state and a small sample, not repeated whole ledgers.
    result["fact_sample"] = {
        key: {
            k: fact[k]
            for k in ("value", "unit", "entity_type", "entity_id", "period", "comparison_period")
            if k in fact
        }
        for key, fact in list(p.get("fact_catalog", {}).items())[:4]
    }
    if "items" in p:
        result["items"] = [
            {
                k: i[k]
                for k in (
                    "sku",
                    "reason",
                    "snapshot_day",
                    "sales_coverage",
                    "lead_days",
                    "coverage_days",
                    "below_lead_proxy",
                )
                if k in i
            }
            for i in p["items"]
        ]
    if "quotes" in p:
        result["quotes"] = p["quotes"][:4]
    if "series" in p:
        result["series_coverage"] = [
            {k: s[k] for k in ("day", "period_name", "coverage", "status") if k in s}
            for s in p["series"]
        ]
    result["summary_truncated"] = True
    return result


def prompt(task, bundle, scope, steps, evidence, catalog):
    obligations = component_obligations(task["query"], evidence, scope)
    budget, metrics = task["parameters"]["budget"], task.get("metrics", {})
    context = {
        "prompt_version": "diagnosis-v12",
        "objective": task["query"],
        "request_contract": {
            **request_contract(task["query"]),
            "completion_mode": "literal_comparison"
            if literal_comparison(task["query"])
            else "requested_checks",
        },
        "bundle": {
            k: bundle[k] for k in ("bundle_id", "bundle_hash", "period", "comparison_period")
        },
        "bound_sources": task["parameters"]["sources"],
        "scope": scope,
        "resource_limits": budget,
        "remaining_budget": {
            "tokens_before_soft_stop": max(
                0,
                budget["max_tokens"]
                - metrics.get("input_tokens", 0)
                - metrics.get("output_tokens", 0),
            ),
            "model_calls": max(0, budget["max_model_calls"] - metrics.get("model_calls", 0)),
            "decision_calls": max(
                0,
                (
                    min(12, budget["max_model_calls"]) - 3
                    if min(12, budget["max_model_calls"]) >= 4
                    else min(12, budget["max_model_calls"])
                )
                - metrics.get("model_calls", 0),
            ),
            "tool_calls": max(0, budget["max_tool_calls"] - metrics.get("tool_calls", 0)),
            "decision_steps": max(0, 8 - len(steps)),
        },
        "required_evidence_gaps": missing_coverage(task["query"], evidence, scope),
        "component_obligations": obligations,
        "capability_frontier": capability_frontier(task["query"], evidence, scope, catalog),
        "completed_steps": [
            {
                "sequence": s["sequence"],
                "status": s["status"],
                "tool": s["proposal"].get("tool_name"),
                "arguments": s.get("validated_args"),
                "assessment": s.get("assessment"),
            }
            for s in steps
        ],
        "evidence": [evidence_summary(e) for e in evidence],
        "tool_constraints": {"drilldown_series": SERIES_CONSTRAINT}
        if "drilldown_series" in catalog
        else {},
        "tools": catalog,
        "schema": StepProposalV2.model_json_schema(),
    }
    # Large scopes retain every obligation in a columnar table rather than
    # repeating labels, keys and hundreds of prose gaps on each request.
    if len(obligations) > 72:
        context["component_obligations"] = {
            "columns": ["sku", "metric", "part", "status", "fact_key"],
            "rows": [
                [i[k] for k in ("sku", "metric", "part", "status", "fact_key")] for i in obligations
            ],
        }
        component_gaps = [g for g in context["required_evidence_gaps"] if g.startswith("必答项 ")]
        context["required_evidence_gaps"] = [
            g for g in context["required_evidence_gaps"] if not g.startswith("必答项 ")
        ]
        if component_gaps:
            context["required_evidence_gaps"].append(
                f"{len(component_gaps)}个分量必答项未取得，逐项见component_obligations"
            )
    if len(json.dumps(context, ensure_ascii=False).encode()) > 96 * 1024:
        for e in context["evidence"]:
            for key in ("fact_sample", "quotes", "component_coverage", "items", "series_coverage"):
                e.pop(key, None)
            e["detail_location"] = "full authorized evidence ledger; component states above"
        context["context_truncated"] = True
    if len(json.dumps(context, ensure_ascii=False).encode()) > 96 * 1024:
        # Preserve authorization and all obligation identities; fail before send.
        raise BudgetExceeded("diagnosis_context_budget")
    system = (
        SYSTEM_PROMPT
        + """
按required_evidence_gaps和component_obligations判定剩余工作，先读capability_frontier的实际输出能力及remaining_budget。
rank_contributors一次返回所选全部SKU的净额、支付金额、退款金额两期和差额，TopK不截断这些分量；批量必答项优先用它，别逐SKU逐指标重复compare_periods。
退款件数、支付分母及记账占比仍须analyze_refunds。可选等价证据路径，但应在剩余额度内完成；已经acquired或not_applicable的项目不要重查。
summary_truncated只表示模型摘要压缩；程序用完整证据判定覆盖并生成报告。局部新品比较期不适用不能说成全部数据缺失；缺行不能补零。
仅对字面两期金额问题，完整同范围基线即足够；原因、贡献、逐日、库存、退款分母或规则请求仍须各自的核查。
模型原文不能增加权限或变成已验证结论。上一提议失败时按completed_steps中的类型化correction修正。
"""
    )
    return [("system", system), ("human", json.dumps(context, ensure_ascii=False))]


def demo_proposal(task, scope, evidence, steps):
    if baseline_complete(task["query"], evidence, scope):
        return StepProposalV2(
            action="stop", question="所问两期事实已核对", stop_reason="target_covered"
        )
    return planner_v11.demo_proposal(task, scope, evidence, steps)
