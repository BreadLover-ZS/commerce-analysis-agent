"""v15 reporting uses program-owned capability witnesses and completion states."""

from decimal import Decimal

from app.diagnosis.inputs import digest
from app.diagnosis.schemas_v4 import DiagnosticReportV4


def contract(task):
    from app.commerce.answer_versions import completion_module

    semantic = completion_module(task["parameters"])
    amount_witness, missing_coverage = semantic.amount_witness, semantic.missing_coverage
    from app.diagnosis.reporting import report_contract

    keys, previous_findings, _, _ = report_contract({**task, "graph_version": "diagnosis-v14"})

    def findings(task_id, evidence, steps):
        result = previous_findings(task_id, evidence, steps)
        scope = {
            "skus": task["parameters"]["answer_contract"]["entity_scope"]["selected"],
            "objective": None,
        }
        for step in steps:
            if step["proposal"]["action"] == "clarify" and step["status"] == "SUCCEEDED":
                scope = (step["assessment"] or {}).get("scope", scope)
        witness = (
            amount_witness(evidence, scope)
            if task["parameters"]["answer_contract"]["amount_identity_requested"]
            else None
        )
        if witness:
            d = {m: str(Decimal(v) / 100) for m, v in witness["delta_cents"].items()}
            result.insert(
                0,
                {
                    "finding_id": "F-" + digest({"task": task_id, "witness": witness})[:24],
                    "hypothesis_code": "refund_amount_change",
                    "status": "supported_observation",
                    "text": f"同范围完整两期金额对账：净销售额变化 {d['net_revenue']} 元 = 支付金额变化 {d['gross_revenue']} 元 − 退款金额变化 {d['refund_amount']} 元。此为记账恒等式，不证明业务因果。",
                    "scope": scope,
                    "supporting_evidence": sorted({r["evidence_id"] for r in witness["fact_refs"]}),
                    "contradicting_evidence": [],
                    "missing_data": [],
                    "fact_refs": witness["fact_refs"],
                },
            )
        return result[:50]

    return keys, findings, DiagnosticReportV4, missing_coverage
