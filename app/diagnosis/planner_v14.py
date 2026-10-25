"""Frozen v14 completion: entity/metric/period and contribution dimension."""

import copy
import json
import re

from app.commerce.answer_contract_v3 import initial_scope as initial_scope
from app.commerce.answer_contract_v3 import request_contract, unsupported_gaps
from app.diagnosis import planner_v3, planner_v9, planner_v10, planner_v11, planner_v12
from app.diagnosis.schemas_v2 import StepProposalV2

CLARIFICATION_GAP = "指代商品尚未明确，请在授权范围内选择商品"


def ambiguous_entity(query, scope):
    return bool(
        re.search(r"那个商品|这个商品|这款商品|那款商品|该商品", query) and len(scope["skus"]) > 1
    )


def single_period(query):
    c = request_contract(query)
    if not c["matrix_literal"] or len(c["periods"]) != 1:
        return None
    part = c["periods"][0]
    # A question explicitly discussing the other period's boundary still needs
    # that boundary. A genuine one-period request does not require absent rows.
    other = r"前期|比较期|两期" if part == "current" else r"本期|两期"
    return part if not re.search(other, c["canonical_query"]) else None


def single_period_covered(query, payload):
    part = single_period(query)
    coverage = payload.get("coverage", {}).get(part, {})
    return bool(
        part and coverage.get("complete") is True and coverage.get("observed_records", 0) > 0
    )


def objectives(query):
    return planner_v11.objectives(request_contract(query)["canonical_query"])


def unsupported_question(query):
    c = request_contract(query)
    return bool(c["unsupported_requests"] and not c["goals"])


def component_obligations(query, evidence, scope):
    c = request_contract(query)
    if not c["per_entity"]:
        same = planner_v3.matching_payloads(evidence, scope)
        identity = ",".join(sorted(scope["skus"]))
        items = []
        for metric in c["requested_metrics"]:
            if metric not in {
                "net_revenue",
                "gross_revenue",
                "refund_amount",
                "paid_units",
                "refunded_units",
            }:
                continue
            for part in c["metric_periods"][metric]:
                key = f"{metric}.{part}"
                periods = ("current", "comparison") if part == "delta" else (part,)
                catalogs = [p.get("fact_catalog", {}) for p in same]
                acquired = any(
                    key in catalog
                    and catalog[key].get("entity_id") == identity
                    and all(
                        p.get("coverage", {}).get(k, {}).get("complete") is True
                        and p["coverage"][k].get("expected_records", 0) > 0
                        for k in periods
                    )
                    for p, catalog in zip(same, catalogs)
                )
                unavailable = any(
                    k in p.get("coverage", {})
                    and (
                        not p["coverage"][k].get("complete")
                        or p["coverage"][k].get("expected_records") == 0
                    )
                    for p in same
                    for k in periods
                )
                items.append(
                    {
                        "sku": identity,
                        "metric": metric,
                        "part": part,
                        "status": "acquired"
                        if acquired
                        else "missing_source"
                        if unavailable
                        else "not_queried",
                        "fact_key": key if acquired else None,
                    }
                )
        return items
    available = {
        (f["entity_id"], f["key"])
        for e in evidence
        for f in e["payload"].get("fact_catalog", {}).values()
    }
    coverage = {
        sku: periods
        for e in evidence
        for sku, periods in e["payload"].get("component_coverage", {}).items()
    }
    for e in evidence:
        p = e["payload"]
        if len(p.get("scope", {}).get("skus", [])) == 1 and p.get("coverage", {}).get("current"):
            coverage.setdefault(p["scope"]["skus"][0], p["coverage"])
    items = []
    for sku in scope["skus"]:
        for metric in c["component_metrics"]:
            for part in c["metric_periods"].get(metric, c["periods"]):
                key = f"component.{sku}.{metric}.{part}"
                selected = key if (sku, key) in available else f"{metric}.{part}"
                periods = ("current", "comparison") if part == "delta" else (part,)
                status = (
                    "not_applicable"
                    if any(
                        coverage.get(sku, {}).get(p, {}).get("expected_records") == 0
                        for p in periods
                    )
                    else "acquired"
                    if (sku, selected) in available
                    else "missing_source"
                    if sku in coverage
                    else "not_queried"
                )
                items.append(
                    {
                        "sku": sku,
                        "metric": metric,
                        "part": part,
                        "status": status,
                        "fact_key": selected if status == "acquired" else None,
                    }
                )
    return items


def missing_dimensions(query, evidence, scope):
    acquired = {
        p.get("dimension")
        for p in planner_v3.matching_payloads(evidence, scope)
        if p.get("tool") == "rank_contributors"
        and p.get("status") == "ok"
        and (p.get("reconciliation") or {}).get("balanced") is True
    }
    return [
        dim for dim in request_contract(query)["contribution_dimensions"] if dim not in acquired
    ]


def dimension_covered(query, evidence, scope):
    return not missing_dimensions(query, evidence, scope)


def dimension_gaps(query, evidence, scope):
    return [
        "所问贡献维度尚未取得：" + ("品类" if dim == "category" else "商品")
        for dim in missing_dimensions(query, evidence, scope)
    ]


def missing_coverage(query, evidence, scope):
    c = request_contract(query)
    if unsupported_question(query):
        return unsupported_gaps(query)
    gaps = planner_v10.missing_coverage(c["canonical_query"], evidence, scope)
    # An amount/count display request does not implicitly request attribution.
    gaps = [g for g in gaps if g != "授权范围内的完整贡献分解"]
    if (
        planner_v9.dynamic_scope_covered(evidence, scope)
        or planner_v12.literal_comparison(query)
        or c["matrix_literal"]
    ):
        gaps = [g for g in gaps if g != planner_v9.DYNAMIC_GAP]
    if c["matrix_literal"]:
        gaps = [g for g in gaps if g != "退款金额、件数和分母核对"]
    one_period = single_period(query)
    same = planner_v3.matching_payloads(evidence, scope)
    if one_period and any(single_period_covered(query, p) for p in same):
        gaps = [g for g in gaps if g != "当前对象范围的两期净销售额事实核对"]
    if ambiguous_entity(query, scope):
        gaps.append(CLARIFICATION_GAP)
    gaps.extend(dimension_gaps(query, evidence, scope))
    for item in component_obligations(query, evidence, scope):
        if item["status"] in {"not_queried", "missing_source"}:
            gaps.append(f"必答项 {item['sku']}/{item['metric']}/{item['part']} 尚未取得")
    return list(
        dict.fromkeys(
            gaps
            + unsupported_gaps(query)
            + (
                []
                if one_period and any(single_period_covered(query, p) for p in same)
                else planner_v12.source_gaps(query, evidence, scope)
            )
        )
    )


def pending_computable(query, evidence, scope):
    return [g for g in missing_coverage(query, evidence, scope) if g not in unsupported_gaps(query)]


def computable_gap_tools(query, evidence, scope, catalog):
    if not missing_coverage(query, evidence, scope):
        return []
    tools = set(planner_v11.computable_gap_tools(query, evidence, scope, catalog))
    if (
        not dimension_covered(query, evidence, scope)
        or request_contract(query)["per_entity"]
        and any(i["status"] == "not_queried" for i in component_obligations(query, evidence, scope))
    ):
        tools.add("rank_contributors")
    if not request_contract(query)["per_entity"] and any(
        i["status"] == "not_queried" for i in component_obligations(query, evidence, scope)
    ):
        tools.update({"compare_periods", "analyze_refunds"})
    return sorted(tools & set(catalog))


def baseline_complete(query, evidence, scope):
    return bool(
        (request_contract(query)["matrix_literal"] or planner_v12.literal_comparison(query))
        and not missing_coverage(query, evidence, scope)
    )


def prompt(task, bundle, scope, steps, evidence, catalog):
    # Reuse the bounded compressor without mutating any frozen v12 helper.
    # v12's context builder accepts versioned projections via this independent
    # context rebinding below; the full v14 obligation table is authoritative.
    messages = planner_v12.prompt(task, bundle, scope, steps, evidence, catalog)
    context = json.loads(messages[1][1])
    context.update(
        prompt_version="diagnosis-v14",
        request_contract=task["parameters"]["answer_contract"],
        component_obligations=component_obligations(task["query"], evidence, scope),
        required_evidence_gaps=missing_coverage(task["query"], evidence, scope),
    )
    frontier = copy.deepcopy(context["capability_frontier"])
    remaining = [i for i in context["component_obligations"] if i["status"] == "not_queried"]
    if "compare_periods" in frontier:
        frontier["compare_periods"]["remaining_components_require_at_least_calls"] = len(
            {(i["sku"], i["metric"]) for i in remaining}
        )
    if "analyze_refunds" in frontier:
        frontier["analyze_refunds"]["explicit_requirement_pending"] = (
            "退款金额、件数和分母核对" in context["required_evidence_gaps"]
        )
    if "rank_contributors" in frontier:
        frontier["rank_contributors"].update(
            pending_component_items_covered_in_one_call=len(remaining)
            if request_contract(task["query"])["per_entity"]
            else 0,
            recommended_arguments={
                "dimension": (missing_dimensions(task["query"], evidence, scope) or ["sku"])[0],
                "top_k": min(10, len(scope["skus"])),
                "filters": {"skus": scope["skus"]},
            },
        )
        frontier["rank_contributors"]["outputs"].extend(
            [
                "all_skus.paid_units.current/comparison/delta",
                "all_skus.refunded_units.current/comparison/delta",
            ]
        )
    context["capability_frontier"] = frontier
    # Keep every obligation identity, but share column labels for large scopes.
    if len(context["component_obligations"]) > 72:
        context["component_obligations"] = {
            "columns": ["sku", "metric", "part", "status", "fact_key"],
            "rows": [
                [i[k] for k in ("sku", "metric", "part", "status", "fact_key")]
                for i in context["component_obligations"]
            ],
        }
        components = [g for g in context["required_evidence_gaps"] if g.startswith("必答项 ")]
        context["required_evidence_gaps"] = [
            g for g in context["required_evidence_gaps"] if not g.startswith("必答项 ")
        ]
        if components:
            context["required_evidence_gaps"].append(
                f"{len(components)}项未完成，完整身份见component_obligations"
            )
    from app.storage.repository import BudgetExceeded

    if len(json.dumps(context, ensure_ascii=False).encode()) > 96 * 1024:
        for e in context["evidence"]:
            for key in ("fact_sample", "quotes", "component_coverage", "items", "series_coverage"):
                e.pop(key, None)
    if len(json.dumps(context, ensure_ascii=False).encode()) > 96 * 1024:
        raise BudgetExceeded("diagnosis_context_budget")
    system = (
        messages[0][1].replace(
            "退款件数、支付分母及记账占比仍须analyze_refunds。",
            "注册表6支持逐商品支付/退款件数；记账占比及分母核查仍须analyze_refunds。",
        )
        + "\nv14以保存的answer-contract-v3为准；注册表6排行还返回支付/退款件数分量。逐对象字面金额/件数只需覆盖明确义务；贡献请求必须覆盖所问维度。错误维度可以探索但不能完成。实体排除已由服务器缩小范围，不得重新加入。"
    )
    return [("system", system), ("human", json.dumps(context, ensure_ascii=False))]


def demo_proposal(task, scope, evidence, steps):
    if ambiguous_entity(task["query"], scope):
        return StepProposalV2(
            action="clarify", question="请选择已授权商品", clarification_scope="sku"
        )
    if not missing_coverage(task["query"], evidence, scope):
        return StepProposalV2(
            action="stop", question="请求义务已覆盖", stop_reason="target_covered"
        )
    pending = [
        i
        for i in component_obligations(task["query"], evidence, scope)
        if i["status"] == "not_queried"
    ]
    if not request_contract(task["query"])["per_entity"] and pending:
        refund_decomposition = (
            "refund_amount" in request_contract(task["query"])["requested_metrics"]
        )
        return StepProposalV2(
            action="tool",
            question="核对所问范围的汇总必答项",
            tool_name="analyze_refunds" if refund_decomposition else "compare_periods",
            arguments={
                "filters": {"skus": scope["skus"]},
                **({} if refund_decomposition else {"metric": pending[0]["metric"]}),
            },
        )
    if not dimension_covered(task["query"], evidence, scope) or any(
        i["status"] == "not_queried" for i in component_obligations(task["query"], evidence, scope)
    ):
        return StepProposalV2(
            action="tool",
            question="核对所问维度及逐商品必答项",
            tool_name="rank_contributors",
            arguments={
                "dimension": (missing_dimensions(task["query"], evidence, scope) or ["sku"])[0],
                "filters": {"skus": scope["skus"]},
            },
        )
    return planner_v12.demo_proposal(task, scope, evidence, steps)
