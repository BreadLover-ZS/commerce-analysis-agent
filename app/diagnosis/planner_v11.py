"""Request-item completion, with no reinterpretation of v1-v10 checkpoints."""

import json

from app.commerce.answer_contract import request_contract, unsupported_gaps
from app.diagnosis import planner_v5, planner_v7, planner_v10
from app.diagnosis.schemas_v2 import StepProposalV2


def objectives(query):
    contract = request_contract(query)
    return sorted(
        set(contract["goals"])
        | ({"rule_applicability"} if planner_v7.applicability_requested(query) else set())
    )


def unsupported_question(query):
    c = request_contract(query)
    return bool(c["unsupported_requests"] and not c["goals"])


def component_obligations(query, evidence, scope):
    """Bound each requested SKU/metric/period to a fact or an explicit state."""
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
    obligations = []
    for sku in scope["skus"]:
        for metric in c["component_metrics"]:
            for part in ("current", "comparison", "delta"):
                key = f"component.{sku}.{metric}.{part}"
                alternate = f"{metric}.{part}"
                periods = ("current", "comparison") if part == "delta" else (part,)
                not_applicable = any(
                    coverage.get(sku, {}).get(p, {}).get("expected_records") == 0 for p in periods
                )
                selected = key if (sku, key) in available else alternate
                status = (
                    "not_applicable"
                    if not_applicable
                    else "acquired"
                    if (sku, selected) in available
                    else "missing_source"
                    if sku in coverage
                    else "not_queried"
                )
                obligations.append(
                    {
                        "sku": sku,
                        "metric": metric,
                        "part": part,
                        "status": status,
                        "fact_key": selected if status == "acquired" else None,
                    }
                )
    return obligations


def missing_coverage(query, evidence, scope):
    c = request_contract(query)
    if unsupported_question(query):
        return unsupported_gaps(query)
    gaps = planner_v10.missing_coverage(c["canonical_query"], evidence, scope)
    for item in component_obligations(query, evidence, scope):
        if item["status"] in {"missing_source", "not_queried"}:
            gaps.append(f"必答项 {item['sku']}/{item['metric']}/{item['part']} 尚未取得")
    return list(dict.fromkeys(gaps + unsupported_gaps(query)))


def computable_gap_tools(query, evidence, scope, catalog):
    gaps = missing_coverage(query, evidence, scope)
    tools = set(
        planner_v5.computable_gap_tools(
            request_contract(query)["canonical_query"], evidence, scope, catalog
        )
    )
    if any(g.startswith("必答项 ") for g in gaps):
        tools.add("rank_contributors")
    return sorted(tools & set(catalog))


def pending_computable(query, evidence, scope):
    return [g for g in missing_coverage(query, evidence, scope) if g not in unsupported_gaps(query)]


def prompt(task, bundle, scope, steps, evidence, catalog):
    messages = planner_v10.prompt(task, bundle, scope, steps, evidence, catalog)
    context = json.loads(messages[1][1])
    context.update(
        prompt_version="diagnosis-v11",
        objectives=objectives(task["query"]),
        request_contract=request_contract(task["query"]),
        component_obligations=component_obligations(task["query"], evidence, scope),
        required_evidence_gaps=missing_coverage(task["query"], evidence, scope),
    )
    # Bounded summaries retain source scope and fact identities; omit old prose repetition.
    context["completed_steps"] = [
        {
            "sequence": s["sequence"],
            "status": s["status"],
            "tool": s["proposal"].get("tool_name"),
            "arguments": s.get("validated_args"),
            "assessment": s.get("assessment"),
        }
        for s in steps
    ]
    return [
        (
            "system",
            messages[0][1]
            + "\n以request_contract逐项核对请求。component事实同时给出各对象支付、退款、净额两期和差额；不要重复查询已取得的必答项。不支持项保留独立缺口，继续其他可答项。",
        ),
        ("human", json.dumps(context, ensure_ascii=False)),
    ]


def demo_proposal(task, scope, evidence, steps):
    query = task["query"]
    if "那个商品" in query and len(scope["skus"]) > 1:
        return StepProposalV2(
            action="clarify", question="请选择已授权商品", clarification_scope="sku"
        )
    gaps = pending_computable(query, evidence, scope)
    if not gaps:
        return StepProposalV2(
            action="stop",
            question="必答项已核对",
            stop_reason="evidence_gap" if unsupported_gaps(query) else "target_covered",
        )
    if any(g.startswith("必答项 ") for g in gaps):
        return StepProposalV2(
            action="tool",
            question="核对各商品分量",
            hypothesis_code="sales_concentration",
            tool_name="rank_contributors",
            arguments={"dimension": "sku", "top_k": 10, "filters": {"skus": scope["skus"]}},
        )
    from app.diagnosis import planner_v9

    clean_task = {**task, "query": request_contract(query)["canonical_query"]}
    return planner_v9.demo_proposal(clean_task, scope, evidence, steps)
