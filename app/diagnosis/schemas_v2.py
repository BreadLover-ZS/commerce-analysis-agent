"""Separate v2 contracts; importing this module does not mutate v1 JSON schemas."""

from typing import Literal

from pydantic import Field

from app.diagnosis.schemas import DiagnosticFact, DiagnosticReport, StepProposal, StrictModel


class QuoteReference(StrictModel):
    evidence_id: str = Field(min_length=1, max_length=40)
    chunk_id: str = Field(min_length=1, max_length=80)
    quote_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class RuleInterpretation(StrictModel):
    text: str = Field(min_length=1, max_length=800)
    references: list[QuoteReference] = Field(min_length=1, max_length=5)
    status: Literal["unverified"] = "unverified"


class StepProposalV2(StepProposal):
    hypothesis_code: Literal[
        "sales_concentration",
        "refund_amount_change",
        "denominator_effect",
        "daily_distribution",
        "data_coverage",
        "inventory_proxy",
        "rule_applicability",
    ] = "data_coverage"
    rule_interpretations: list[RuleInterpretation] = Field(default_factory=list, max_length=5)


class DiagnosticFactV2(DiagnosticFact):
    unit: Literal["CNY", "count", "percent", "days", "units_per_day"]
    snapshot_as_of: str | None = None


class DiagnosticReportV2(DiagnosticReport):
    schema_version: Literal[2] = 2
    facts: list[DiagnosticFactV2] = Field(default_factory=list, max_length=250)
    rule_quotes: list[dict] = Field(default_factory=list, max_length=50)
    rule_conflicts: list[dict] = Field(default_factory=list, max_length=50)
    rule_interpretations: list[RuleInterpretation] = Field(default_factory=list, max_length=40)
