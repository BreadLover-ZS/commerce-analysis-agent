"""Strict server-side input contracts; identity and periods are never tool arguments."""

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.commerce.calculations import comparison_dates

METRIC_VERSION = "diagnosis-v1"
GRAPH_VERSION = "diagnosis-v1"
PROMPT_VERSION = "diagnosis-v1"
TOOL_VERSION = "1"
Metric = Literal["net_revenue", "gross_revenue", "refund_amount", "paid_units", "refunded_units"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Period(StrictModel):
    start: date
    end: date

    @model_validator(mode="after")
    def valid_range(self):
        if not 0 <= (self.end - self.start).days <= 89:
            raise ValueError("周期必须为1–90天")
        return self


class Filters(StrictModel):
    skus: list[str] = Field(default_factory=list, max_length=100)
    categories: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("skus", "categories")
    @classmethod
    def canonical_values(cls, value):
        if any(not v or len(v) > 120 for v in value) or len(value) != len(set(value)):
            raise ValueError("筛选项须非空、唯一且不超过120字符")
        return sorted(value)


class InputBundleRequest(StrictModel):
    sales_dataset_id: UUID
    inventory_dataset_id: UUID | None = None
    document_manifest_id: UUID | None = None
    period: Period
    comparison_period: Period | None = None
    allowed_skus: list[str] = Field(default_factory=list, max_length=100)
    allowed_categories: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def consistent(self):
        first, last = comparison_dates(
            self.period.start,
            self.period.end,
            self.comparison_period.start if self.comparison_period else None,
            self.comparison_period.end if self.comparison_period else None,
        )
        self.comparison_period = Period(start=first, end=last)
        filters = Filters(skus=self.allowed_skus, categories=self.allowed_categories)
        self.allowed_skus, self.allowed_categories = filters.skus, filters.categories
        return self


class InspectArgs(StrictModel):
    pass


class CompareArgs(StrictModel):
    metric: Metric = "net_revenue"
    filters: Filters = Field(default_factory=Filters)


class RankArgs(StrictModel):
    dimension: Literal["sku", "category"] = "sku"
    top_k: int = Field(default=5, ge=1, le=20, strict=True)
    filters: Filters = Field(default_factory=Filters)


class SeriesArgs(StrictModel):
    filters: Filters
    granularity: Literal["day"] = "day"

    @model_validator(mode="after")
    def one_entity(self):
        if len(self.filters.skus) + len(self.filters.categories) != 1:
            raise ValueError("日下钻须指定一个SKU或一个品类")
        return self


class RefundArgs(StrictModel):
    filters: Filters = Field(default_factory=Filters)


SALES_TOOLS = {
    "inspect_dataset": InspectArgs,
    "compare_periods": CompareArgs,
    "rank_contributors": RankArgs,
    "drilldown_series": SeriesArgs,
    "analyze_refunds": RefundArgs,
}

HypothesisCode = Literal[
    "sales_concentration",
    "refund_amount_change",
    "denominator_effect",
    "daily_distribution",
    "data_coverage",
]


class StepProposal(StrictModel):
    action: Literal["tool", "clarify", "stop"]
    question: str = Field(min_length=1, max_length=300)
    hypothesis_code: HypothesisCode = "data_coverage"
    tool_name: str | None = Field(default=None, max_length=40)
    arguments: dict[str, Any] = Field(default_factory=dict, max_length=10)
    expected_evidence: str = Field(default="", max_length=300)
    clarification_scope: Literal["sku", "category", "objective"] | None = None
    stop_reason: Literal["target_covered", "evidence_gap", "exploration_complete"] | None = None

    @model_validator(mode="after")
    def action_contract(self):
        if self.action == "tool":
            if not self.tool_name or self.clarification_scope or self.stop_reason:
                raise ValueError("工具提议须且仅须提供工具和参数")
        elif self.tool_name or self.arguments:
            raise ValueError("非工具提议不能包含工具参数")
        if (self.action == "clarify") != (self.clarification_scope is not None):
            raise ValueError("澄清须限定在SKU、品类或目标中")
        if (self.action == "stop") != (self.stop_reason is not None):
            raise ValueError("停止须提供结构化原因")
        return self


class ClarificationAnswer(StrictModel):
    interrupt_id: UUID
    expected_version: int = Field(ge=1, strict=True)
    choice: str = Field(min_length=1, max_length=120)


class FeedbackRequest(StrictModel):
    expected_version: int = Field(ge=0, strict=True)
    finding_id: str | None = Field(default=None, max_length=40)
    category: Literal["useful", "incorrect_fact", "missing_data", "unclear", "other"]
    comment: str = Field(default="", max_length=1000)


class DiagnosticFact(StrictModel):
    evidence_id: str
    metric_key: str
    value: str
    unit: Literal["CNY", "count", "percent", "days"]
    entity_type: str
    entity_id: str
    period: dict[str, str]
    comparison_period: dict[str, str]
    text: str


class DiagnosticReport(StrictModel):
    profile: Literal["commerce_diagnosis"] = "commerce_diagnosis"
    schema_version: Literal[1] = 1
    title: str = "经营诊断报告"
    objective: str = Field(max_length=2000)
    bundle_id: str
    bundle_hash: str
    scope: dict
    period: dict[str, str]
    comparison_period: dict[str, str]
    versions: dict[str, str]
    report_version: int = Field(ge=1, strict=True)
    execution_mode: Literal["demo_policy", "scripted_protocol", "live_model"]
    facts: list[DiagnosticFact] = Field(min_length=1, max_length=250)
    observations: list[dict] = Field(max_length=50)
    steps: list[dict] = Field(max_length=8)
    stop_reason: str
    open_questions: list[str] = Field(max_length=20)
    recommendations: list[str] = Field(max_length=10)
    limitations: list[str] = Field(max_length=30)
    usage: dict
