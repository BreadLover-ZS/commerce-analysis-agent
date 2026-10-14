from datetime import date
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from app.storage.models import DailySale, Product


class ProductInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=80)
    price_cents: int = Field(gt=0, le=2147483647, strict=True)
    stock: int = Field(ge=0, le=2147483647, strict=True)
    lead_days: int = Field(ge=1, le=365, strict=True)
    stock_as_of: date


class SaleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str
    day: date
    paid_units: int = Field(ge=0, le=2147483647, strict=True)
    refunded_units: int = Field(ge=0, le=2147483647, strict=True)
    revenue_cents: int = Field(ge=0, le=2147483647, strict=True)
    refund_cents: int = Field(ge=0, le=2147483647, strict=True)


class CommerceImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    currency: Literal["CNY"] = "CNY"
    products: list[ProductInput] = Field(min_length=1, max_length=100)
    daily_sales: list[SaleInput] = Field(max_length=10000)

    @model_validator(mode="after")
    def unique_rows(self):
        skus = [p.sku for p in self.products]
        sale_keys = [(s.sku, s.day) for s in self.daily_sales]
        if len(set(skus)) != len(skus) or len(set(sale_keys)) != len(sale_keys):
            raise ValueError("商品或日销售记录重复")
        if any(s.sku not in skus for s in self.daily_sales):
            raise ValueError("销售记录包含未提供的商品")
        return self


def import_commerce(repo, tenant_id: str, payload: CommerceImport, sample=False):
    """Operator-only atomic upsert. It is deliberately absent from agent tools/API."""
    with repo.session() as db:
        mapping = {}
        for value in payload.products:
            item = db.scalar(
                select(Product)
                .where(Product.tenant_id == tenant_id, Product.sku == value.sku)
                .with_for_update()
            )
            if not item:
                item = Product(id=str(uuid4()), tenant_id=tenant_id, sku=value.sku)
                db.add(item)
            for name, field in value.model_dump().items():
                setattr(item, name, field)
            item.sample = sample
            mapping[value.sku] = item.id
        db.flush()
        for value in payload.daily_sales:
            item = db.scalar(
                select(DailySale)
                .where(DailySale.product_id == mapping[value.sku], DailySale.day == value.day)
                .with_for_update()
            )
            if not item:
                item = DailySale(tenant_id=tenant_id, product_id=mapping[value.sku], day=value.day)
                db.add(item)
            for name, field in value.model_dump(exclude={"sku", "day"}).items():
                setattr(item, name, field)
            item.sample = sample
        db.commit()
    return {
        "products": len(payload.products),
        "daily_sales": len(payload.daily_sales),
        "sample": sample,
    }
