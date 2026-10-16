from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

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
    profile: Literal["research", "commerce_review"] = "research"
    dataset_id: str | None = Field(default=None, min_length=36, max_length=36)

    @model_validator(mode="after")
    def check_period(self):
        if self.profile == "commerce_review":
            if (
                not self.dataset_id
                or self.sources != ["sales"]
                or self.strategy != "workflow"
                or self.public_queries
            ):
                raise ValueError("经营复盘须绑定数据集，仅允许sales来源与workflow策略")
        elif self.dataset_id is not None:
            raise ValueError("数据集仅用于经营复盘")
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
    evidence_id: str = Field(description="只能引用kind=metrics的证据，不能引用文档或网页")
    key: str = Field(description="必须原样取自该指标证据的fact_catalog键，不得自行计算或创建")
    value: str
    entity_id: str
    unit: Literal["CNY", "count", "percent", "days"]
    period_start: str
    period_end: str
    snapshot_as_of: str | None = None


class Claim(BaseModel):
    id: str
    kind: Literal["fact", "analysis", "recommendation"] = Field(
        description="fact呈现可核对事实；analysis解释事实；recommendation提出待人工确认的建议"
    )
    text: str = Field(min_length=1, max_length=1200)
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)
    metrics: list[MetricReference] = Field(
        default_factory=list,
        max_length=8,
        description="仅kind=fact可以包含指标引用；analysis/recommendation必须为空列表，使用evidence_ids引用依据",
    )

    @model_validator(mode="after")
    def metrics_only_on_facts(self):
        if self.metrics and self.kind != "fact":
            raise PydanticCustomError(
                "non_fact_metrics",
                "指标引用只允许用于fact；analysis/recommendation的metrics必须为空",
            )
        return self


class ReportSection(BaseModel):
    name: Literal[
        "经营表现", "库存风险", "竞品观察", "行动建议", "变化贡献", "退款观察", "经营解读"
    ]
    claims: list[Claim] = Field(default_factory=list, max_length=8)


class ResearchReport(BaseModel):
    title: str = Field(max_length=120)
    sections: list[ReportSection] = Field(max_length=5)
    limitations: list[str] = Field(default_factory=list, max_length=20)


class ReviewNarrative(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analysis: list[
        Literal["sales_drivers", "refund_drivers", "product_mix", "daily_structure", "data_quality"]
    ] = Field(min_length=1, max_length=4)
    recommendations: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(
        min_length=1, max_length=4
    )


class SemanticReview(BaseModel):
    unsupported_claim_ids: list[str] = Field(default_factory=list)
    unsupported_limitation_indices: list[int] = Field(
        default_factory=list,
        max_length=20,
        description="不支持或与已提供原文矛盾的limitations索引（从0开始），特别检查把已有规则误称为缺失",
    )
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


class FindingVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    finding_id: str
    verdict: Literal["supported", "unsupported", "uncertain"]
    explanation: str = Field(
        min_length=1, max_length=400, description="简短说明原文支持或不支持的关系，不输出推理过程"
    )


class ResearchSemanticReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    findings: list[FindingVerdict] = Field(
        max_length=12, description="对每个研究发现ID恰好返回一条结论；不能遗漏、重复或添加ID"
    )
    unsupported_gap_indices: list[int] = Field(
        max_length=10, description="与已取得资料或任务范围矛盾的gaps索引，从0开始；没有则返回[]"
    )
