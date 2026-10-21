"""Versioned evaluation data. Expectations/harness tags never enter Agent context."""

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictBool

from app.commerce.datasets import DatasetInput
from app.diagnosis.inputs import digest
from app.diagnosis.schemas import Period, StrictModel

DEFAULT_CASES = Path(__file__).with_name("diagnosis_cases.json")
Split = Literal["development", "regression", "holdout"]


class Expected(StrictModel):
    outcome: Literal["report", "gap", "rejected", "cancelled", "unknown_usage"]
    net_cents: int = Field(strict=True)
    comparison_net_cents: int = Field(strict=True)
    delta_cents: int | None = Field(strict=True)
    comparable: StrictBool
    required_tools: list[str] = Field(max_length=7)
    forbidden_metrics: list[str] = Field(max_length=10)
    critical: StrictBool
    positive: StrictBool
    baseline_capability: Literal["common", "additional"]
    http_status: Literal[400, 404] | None
    task_statuses: list[
        Literal["succeeded", "degraded", "failed", "cancelled", "rejected", "expired"]
    ]
    stop_reasons: list[str] = Field(max_length=5)
    required_assertions: list[str] = Field(max_length=15)
    accepted_conclusions: list[str] = Field(max_length=8)
    forbidden_conclusions: list[str] = Field(max_length=8)


class Harness(StrictModel):
    mode: str = Field(max_length=40)
    clarification_choice: str | None = None


class Case(StrictModel):
    case_id: str = Field(pattern=r"^[DRH][0-9]{2}$")
    split: Split
    facet: str = Field(max_length=40)
    query: str = Field(min_length=2, max_length=2000)
    execution: Literal["diagnostic", "admission", "fault"]
    sales: DatasetInput
    period: Period
    comparison_period: Period
    sources: list[Literal["sales", "inventory", "documents"]]
    scope_skus: list[str] = Field(min_length=1, max_length=100)
    inventory: dict | None
    documents: list[dict] = Field(max_length=5)
    expected: Expected
    harness: Harness


def business_input(case):
    """Allowlist for fixture creation; exclude split, facet, answers and fault harness."""
    raw = case.model_dump(mode="json")
    return {
        k: raw[k]
        for k in (
            "query",
            "sales",
            "period",
            "comparison_period",
            "sources",
            "scope_skus",
            "inventory",
            "documents",
        )
    }


def reference_answer(case):
    """Independent raw-record arithmetic, without the production aggregation kernel."""
    skus = set(case.scope_skus)
    products = [p for p in case.sales.products if p.sku in skus]
    parts = []
    for period in (case.period, case.comparison_period):
        rows = [
            r
            for r in case.sales.daily_sales
            if r.sku in skus and period.start <= r.day <= period.end
        ]
        expected = sum(
            max(0, (period.end - max(period.start, p.active_from)).days + 1) for p in products
        )
        parts.append((sum(r.revenue_cents - r.refund_cents for r in rows), len(rows), expected))
    comparable = all(observed == expected and expected > 0 for _, observed, expected in parts)
    return {
        "net_cents": parts[0][0],
        "comparison_net_cents": parts[1][0],
        "comparable": comparable,
        "delta_cents": parts[0][0] - parts[1][0] if comparable else None,
    }


def load_cases(path=DEFAULT_CASES, split=None, case_ids=None):
    content = Path(path).read_bytes()
    if len(content) > 5 * 1024 * 1024:
        raise ValueError("评测用例文件过大")
    raw = json.loads(content)
    if (
        set(raw) != {"schema_version", "oracle", "splits", "cases"}
        or raw["schema_version"] != 1
        or raw["oracle"] != "independent_raw_rows_v1"
    ):
        raise ValueError("评测版本或独立预期来源不匹配")
    cases = [Case.model_validate(c) for c in raw["cases"]]
    ids = [c.case_id for c in cases]
    if not cases or len(ids) != len(set(ids)):
        raise ValueError("评测用例为空或编号重复")
    counts = {
        name: sum(c.split == name for c in cases)
        for name in ("development", "regression", "holdout")
    }
    if raw["splits"] != counts:
        raise ValueError("分集声明与实际用例数不一致")
    for case in cases:
        if case.case_id[0] != {"development": "D", "regression": "R", "holdout": "H"}[case.split]:
            raise ValueError("用例编号与分集不一致")
        if not set(case.scope_skus) <= {p.sku for p in case.sales.products} or len(
            set(case.scope_skus)
        ) != len(case.scope_skus):
            raise ValueError("评测授权范围不合法")
        expected = reference_answer(case)
        if any(getattr(case.expected, k) != v for k, v in expected.items()):
            raise ValueError(f"独立预期与原始输入不匹配：{case.case_id}")
        if ("inventory" in case.sources) != (case.inventory is not None) or (
            "documents" in case.sources
        ) != bool(case.documents):
            raise ValueError("评测来源与输入不一致")
    hashes = [digest(business_input(c)) for c in cases]
    if len(hashes) != len(set(hashes)):
        raise ValueError("评测业务输入重复")
    if split is not None:
        if split not in ("development", "regression", "holdout"):
            raise ValueError("未知评测分集")
        cases = [c for c in cases if c.split == split]
    if case_ids is not None:
        if not case_ids or len(case_ids) != len(set(case_ids)) or set(case_ids) - set(ids):
            raise ValueError("选取的评测编号为空、重复或不存在")
        cases = [c for c in cases if c.case_id in case_ids]
    if not cases:
        raise ValueError("评测选择结果为空")
    return cases, digest([c.model_dump(mode="json") for c in cases])
