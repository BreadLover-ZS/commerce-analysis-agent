from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Source = Literal["sales", "inventory", "documents", "web"]


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str
    query: str = Field(min_length=2, max_length=2000)
    start_date: date = date(2026, 9, 1)
    end_date: date = date(2026, 9, 30)
    sources: list[Source] = Field(
        default_factory=lambda: ["sales", "inventory", "documents", "web"],
        min_length=1,
        max_length=4,
    )
    strategy: Literal["workflow", "single", "multi"] = "single"
    public_queries: list[str] = Field(default_factory=list, max_length=3)
    require_review: bool = False

    @model_validator(mode="after")
    def check_period(self):
        self.query = self.query.strip()
        if len(self.query) < 2:
            raise ValueError("请输入具体分析任务")
        if not 0 <= (self.end_date - self.start_date).days <= 89:
            raise ValueError("分析周期必须为 1–90 天")
        if len(set(self.sources)) != len(self.sources):
            raise ValueError("信息来源不能重复")
        if any(not q.strip() or len(q) > 160 for q in self.public_queries):
            raise ValueError("每条公开搜索关键词需要1–160字符")
        self.public_queries = list(dict.fromkeys(q.strip() for q in self.public_queries))
        return self


class TaskPlan(BaseModel):
    objective: str = Field(max_length=2000)
    sources: list[Source] = Field(max_length=4)
    web_queries: list[str] = Field(default_factory=list, max_length=3)
    document_query: str = Field(default="经营分析 库存 退款 竞品", max_length=300)


class MetricReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str
    key: str
    value: str
    entity_id: str
    unit: Literal["CNY", "count", "percent", "days"]
    period_start: str
    period_end: str
    snapshot_as_of: str | None = None


class Claim(BaseModel):
    id: str
    kind: Literal["fact", "analysis", "recommendation"]
    text: str = Field(min_length=1, max_length=1200)
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)
    metrics: list[MetricReference] = Field(default_factory=list, max_length=8)


class ReportSection(BaseModel):
    name: Literal["经营表现", "库存风险", "竞品观察", "行动建议"]
    claims: list[Claim] = Field(default_factory=list, max_length=8)


class ResearchReport(BaseModel):
    title: str = Field(max_length=120)
    sections: list[ReportSection] = Field(max_length=4)
    limitations: list[str] = Field(default_factory=list, max_length=20)


class SemanticReview(BaseModel):
    unsupported_claim_ids: list[str] = Field(default_factory=list)
    explanation: str = Field(max_length=1500)


class EvidenceQuote(BaseModel):
    evidence_id: str
    quote: str = Field(min_length=3, max_length=600)


class ResearchFinding(BaseModel):
    id: str
    kind: Literal["finding", "conflict"] = "finding"
    statement: str = Field(min_length=1, max_length=800)
    support: list[EvidenceQuote] = Field(min_length=1, max_length=4)


class ResearchSynthesis(BaseModel):
    findings: list[ResearchFinding] = Field(default_factory=list, max_length=12)
    gaps: list[str] = Field(default_factory=list, max_length=10)
