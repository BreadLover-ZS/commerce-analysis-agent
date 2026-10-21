"""Deterministic inventory observations and literal-only document bindings."""

from app.diagnosis.inputs import digest
from app.diagnosis.validation import information_digest as sales_digest
from app.diagnosis.validation import observations as sales_observations
from app.diagnosis.validation import selected_fact_keys as sales_fact_keys


def information_digest(payload):
    if payload.get("tool") == "inventory_risk":
        return digest({"base": sales_digest(payload), "items": payload["items"]})
    if payload.get("tool") == "search_rule_documents":
        # Query wording, rank and locator changes do not create new source facts.
        return digest(sorted({(q["chunk_id"], q["quote_hash"]) for q in payload["quotes"]}))
    return sales_digest(payload)


def selected_fact_keys(payload):
    keys = sales_fact_keys(payload)
    coverage = payload.get("coverage", {})
    return [
        key
        for key in keys
        if not any(
            payload["fact_catalog"][key]["period"] == payload.get(period)
            and coverage.get(name, {}).get("observed_records") == 0
            for name, period in (("current", "period"), ("comparison", "comparison_period"))
        )
    ]


def rule_quotes(evidence):
    return [
        {"evidence_id": e["id"], **quote}
        for e in evidence
        for quote in e["payload"].get("quotes", [])
    ][:50]


def rule_conflicts(evidence):
    return [
        {"evidence_id": e["id"], **candidate}
        for e in evidence
        for candidate in e["payload"].get("conflict_candidates", [])
    ][:50]


def valid_interpretations(values, evidence):
    allowed = {
        (e["id"], q["chunk_id"], q["quote_hash"])
        for e in evidence
        for q in e["payload"].get("quotes", [])
    }
    return all(
        value.get("status") == "unverified"
        and all(
            (r["evidence_id"], r["chunk_id"], r["quote_hash"]) in allowed
            for r in value["references"]
        )
        for value in values
    )


def interpretations(steps, evidence):
    values = [
        value
        for step in steps
        if step["status"] in {"SUCCEEDED", "REUSED"}
        for value in step["proposal"].get("rule_interpretations", [])
    ]
    if not valid_interpretations(values, evidence):
        raise ValueError("解读引用未绑定固定原文")
    return values[:40]


def observations(task_id, evidence, steps=()):
    values = [
        f
        for f in sales_observations(task_id, evidence, steps)
        if f["hypothesis_code"] not in {"inventory_proxy", "rule_applicability"}
    ]
    for source in evidence:
        p = source["payload"]
        if p["tool"] == "inventory_risk":
            for row in p["items"]:
                verdict = row["below_lead_proxy"]
                text = (
                    "完整窗口支付件数代理下，库存覆盖天数少于配置提前期；不能据此确认实际缺货或采购量。"
                    if verdict
                    else "完整窗口支付件数代理下，库存覆盖天数不少于配置提前期；不构成不会缺货的保证。"
                    if verdict is False
                    else "库存代理不可判断："
                    + str(row["reason"])
                    + "；未把缺数或零支付需求当作安全结论。"
                )
                values.append(
                    {
                        "finding_id": "F-"
                        + digest(
                            {"task": task_id, "spec": p["query_spec_hash"], "sku": row["sku"]}
                        )[:24],
                        "hypothesis_code": "inventory_proxy",
                        "status": "insufficient"
                        if verdict is None
                        else "supported_observation"
                        if verdict
                        else "contradicted",
                        "text": text,
                        "scope": {"skus": [row["sku"]]},
                        "supporting_evidence": [source["id"]] if verdict else [],
                        "contradicting_evidence": [source["id"]] if verdict is False else [],
                        "missing_data": [row["reason"]] if verdict is None else [],
                        "fact_refs": [
                            {"evidence_id": source["id"], "metric_key": key}
                            for key in row["fact_keys"].values()
                        ],
                    }
                )
        elif p["tool"] == "search_rule_documents":
            values.append(
                {
                    "finding_id": "F-"
                    + digest(
                        {
                            "task": task_id,
                            "spec": p["query_spec_hash"],
                            "code": "rule_applicability",
                        }
                    )[:24],
                    "hypothesis_code": "rule_applicability",
                    "status": "insufficient",
                    "text": "已找到固定清单内的逐字引用；适用性、解读和不同阈值的冲突候选仍未核验。"
                    if p["quotes"]
                    else "固定文档清单没有匹配原文；不能据此断言不存在相关规则。",
                    "scope": p["scope"],
                    "supporting_evidence": [],
                    "contradicting_evidence": [],
                    "missing_data": ["人工核对规则适用范围、版本及语义"],
                    "fact_refs": [],
                    "quote_refs": [
                        {
                            "evidence_id": source["id"],
                            "chunk_id": q["chunk_id"],
                            "quote_hash": q["quote_hash"],
                        }
                        for q in p["quotes"]
                    ],
                }
            )
    return values[:50]
