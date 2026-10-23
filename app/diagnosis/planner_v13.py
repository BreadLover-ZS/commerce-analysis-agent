"""Frozen v13 completion: entity/metric/period and contribution dimension."""

import copy
import json

from app.commerce.answer_contract import unsupported_gaps
from app.commerce.answer_contract_v2 import initial_scope as initial_scope
from app.commerce.answer_contract_v2 import request_contract
from app.diagnosis import planner_v3, planner_v9, planner_v10, planner_v11, planner_v12
from app.diagnosis.schemas_v2 import StepProposalV2

objectives = planner_v11.objectives
unsupported_question = planner_v11.unsupported_question


def component_obligations(query, evidence, scope):
    c = request_contract(query)
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
            for part in c["periods"]:
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


def dimension_covered(query, evidence, scope):
    dimension = request_contract(query)["contribution_dimension"]
    if not dimension:
        return True
    return any(
        p.get("tool") == "rank_contributors"
        and p.get("dimension") == dimension
        and p.get("status") == "ok"
        and (p.get("reconciliation") or {}).get("balanced") is True
        for p in planner_v3.matching_payloads(evidence, scope)
    )


def dimension_gap(query):
    dim = request_contract(query)["contribution_dimension"]
    return "所问贡献维度尚未取得：" + ("品类" if dim == "category" else "商品")


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
    if not dimension_covered(query, evidence, scope):
        gaps.append(dimension_gap(query))
    for item in component_obligations(query, evidence, scope):
        if item["status"] in {"not_queried", "missing_source"}:
            gaps.append(f"必答项 {item['sku']}/{item['metric']}/{item['part']} 尚未取得")
    return list(
        dict.fromkeys(
            gaps + unsupported_gaps(query) + planner_v12.source_gaps(query, evidence, scope)
        )
    )


def pending_computable(query, evidence, scope):
    return [g for g in missing_coverage(query, evidence, scope) if g not in unsupported_gaps(query)]


def computable_gap_tools(query, evidence, scope, catalog):
    if not missing_coverage(query, evidence, scope):
        return []
    tools = set(planner_v11.computable_gap_tools(query, evidence, scope, catalog))
    if not dimension_covered(query, evidence, scope) or any(
        i["status"] == "not_queried" for i in component_obligations(query, evidence, scope)
    ):
        tools.add("rank_contributors")
    return sorted(tools & set(catalog))


def baseline_complete(query, evidence, scope):
    return planner_v12.baseline_complete(query, evidence, scope) and not missing_coverage(
        query, evidence, scope
    )


def prompt(task, bundle, scope, steps, evidence, catalog):
    # Reuse the bounded compressor without mutating any frozen v12 helper.
    # v12's context builder accepts versioned projections via this independent
    # context rebinding below; the full v13 obligation table is authoritative.
    messages = planner_v12.prompt(task, bundle, scope, steps, evidence, catalog)
    context = json.loads(messages[1][1])
    context.update(
        prompt_version="diagnosis-v13",
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
            pending_component_items_covered_in_one_call=len(remaining),
            recommended_arguments={
                "dimension": request_contract(task["query"])["contribution_dimension"] or "sku",
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
        + "\nv13以保存的answer-contract-v2为准；注册表6排行还返回支付/退款件数分量。逐对象字面金额/件数只需覆盖明确义务；贡献请求必须覆盖所问维度。错误维度可以探索但不能完成。实体排除已由服务器缩小范围，不得重新加入。"
    )
    return [("system", system), ("human", json.dumps(context, ensure_ascii=False))]


def demo_proposal(task, scope, evidence, steps):
    if "那个商品" in task["query"] and len(scope["skus"]) > 1:
        return StepProposalV2(
            action="clarify", question="请选择已授权商品", clarification_scope="sku"
        )
    if not missing_coverage(task["query"], evidence, scope):
        return StepProposalV2(
            action="stop", question="请求义务已覆盖", stop_reason="target_covered"
        )
    if not dimension_covered(task["query"], evidence, scope) or any(
        i["status"] == "not_queried" for i in component_obligations(task["query"], evidence, scope)
    ):
        return StepProposalV2(
            action="tool",
            question="核对所问维度及逐商品必答项",
            tool_name="rank_contributors",
            arguments={
                "dimension": request_contract(task["query"])["contribution_dimension"] or "sku",
                "filters": {"skus": scope["skus"]},
            },
        )
    return planner_v12.demo_proposal(task, scope, evidence, steps)
