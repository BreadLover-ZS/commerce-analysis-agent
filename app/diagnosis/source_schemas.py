"""Local versioned inventory and document input schemas, independent of model output."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.diagnosis.schemas import Filters, StrictModel


class InventoryRow(StrictModel):
    sku: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    snapshot_day: date
    available_units: int = Field(ge=0, le=2147483647, strict=True)
    lead_days: int | None = Field(default=None, ge=0, le=2147483647, strict=True)


class InventoryInput(StrictModel):
    schema_version: Literal[1] = 1
    items: list[InventoryRow] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_skus(self):
        if len({r.sku for r in self.items}) != len(self.items):
            raise ValueError("每SKU只能有一条固定库存快照")
        return self


class ManifestRequest(StrictModel):
    document_ids: list[UUID] = Field(min_length=1, max_length=10)
    conversation_id: UUID | None = None

    @model_validator(mode="after")
    def unique_documents(self):
        if len(set(self.document_ids)) != len(self.document_ids):
            raise ValueError("文档不能重复")
        return self


class InventoryArgs(StrictModel):
    filters: Filters = Field(default_factory=Filters)


class RuleSearchArgs(StrictModel):
    query: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=5, ge=1, le=5, strict=True)

    @model_validator(mode="after")
    def nonempty_query(self):
        self.query = self.query.strip()
        if not self.query:
            raise ValueError("检索词不能为空")
        return self
