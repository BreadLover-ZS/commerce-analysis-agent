"""Frozen-source proposal schemas and bounded diagnostics for diagnosis-v15 only."""

from typing import Annotated, Literal

from pydantic import Field, RootModel, ValidationError

from app.diagnosis.schemas import StrictModel
from app.diagnosis.schemas_v2 import RuleInterpretation

Hypothesis = Literal[
    "sales_concentration",
    "refund_amount_change",
    "denominator_effect",
    "daily_distribution",
    "data_coverage",
    "inventory_proxy",
    "rule_applicability",
]


class Common(StrictModel):
    question: str = Field(min_length=1, max_length=500)
    hypothesis_code: Hypothesis = "data_coverage"


class ToolProposal(Common):
    action: Literal["tool"]
    tool_name: Literal[
        "inspect_dataset",
        "compare_periods",
        "rank_contributors",
        "drilldown_series",
        "analyze_refunds",
        "inventory_risk",
        "search_rule_documents",
    ]
    arguments: dict = Field(default_factory=dict)
    expected_evidence: str = Field(default="", max_length=1000)


class ClarifyProposal(Common):
    action: Literal["clarify"]
    clarification_scope: Literal["sku", "objective"]


class StopProposal(Common):
    action: Literal["stop"]
    stop_reason: Literal["target_covered", "exploration_complete", "evidence_gap"]


class DocumentStopProposal(StopProposal):
    rule_interpretations: list[RuleInterpretation] = Field(default_factory=list, max_length=5)


class SalesProposalV3(
    RootModel[
        Annotated[ToolProposal | ClarifyProposal | StopProposal, Field(discriminator="action")]
    ]
):
    pass


class DocumentProposalV3(
    RootModel[
        Annotated[
            ToolProposal | ClarifyProposal | DocumentStopProposal, Field(discriminator="action")
        ]
    ]
):
    pass


def proposal_schema(task):
    c = task["parameters"]["answer_contract"]
    return (
        DocumentProposalV3
        if "documents" in task["parameters"]["sources"] and c["rule_interpretation_requested"]
        else SalesProposalV3
    )


def is_v3_schema(schema):
    return schema in (SalesProposalV3, DocumentProposalV3)


def validation_code(error):
    # Only program-owned classifications escape the parser. Never include input or raw errors.
    if isinstance(error, ValidationError):
        for item in error.errors(include_input=False, include_context=False, include_url=False):
            if "rule_interpretations" in item["loc"]:
                return "proposal_rule_annotation_invalid"
    return "proposal_schema_invalid"


def correction(code, evidence=()):
    value = {
        "code": code
        if code in {"proposal_rule_annotation_invalid", "unbound_rule_interpretation"}
        else "proposal_schema_invalid",
        "field_path": ["rule_interpretations"]
        if code in {"proposal_rule_annotation_invalid", "unbound_rule_interpretation"}
        else ["proposal"],
        "required_action": "重新输出当前动作允许的完整JSON；工具/澄清提议不得携带规则解读；仅明确需要解释的文档任务可在stop中引用已经取得的quote。不得编造引用。",
        "tool_executed": False,
    }
    if code == "unbound_rule_interpretation":
        value["allowed_quote_references"] = [
            {"evidence_id": e["id"], "chunk_id": q["chunk_id"], "quote_hash": q["quote_hash"]}
            for e in evidence
            for q in e["payload"].get("quotes", [])
        ][:20]
    return value
