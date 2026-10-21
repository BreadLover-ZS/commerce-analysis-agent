"""Bounded CSV import and reproducible, owner-scoped immutable datasets."""

import csv
import hashlib
import io
import json
from datetime import date, timedelta
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import delete, func, select

from app.commerce.importer import SaleInput
from app.storage.models import AnalysisInputBundle, CommerceDataset, Task, User, utcnow
from app.storage.repository import Conflict, NotFound

MAX_FILE_BYTES = 2 * 1024 * 1024
CSV_FIELDS = [
    "sku",
    "name",
    "category",
    "active_from",
    "day",
    "paid_units",
    "refunded_units",
    "revenue_cents",
    "refund_cents",
]
SCENARIOS = {
    "normal": "正常经营",
    "sales_decline": "销售下降",
    "refund_spike": "退款增加",
    "top_decline": "头部商品下滑",
    "missing": "缺失记录",
    "zero": "零销量",
    "no_prior": "前期不可比较",
    "new_product": "新品上架",
    "cross_refund": "跨期退款",
}


class DatasetProduct(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=80)
    active_from: date


class DatasetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    products: list[DatasetProduct] = Field(min_length=1, max_length=100)
    daily_sales: list[SaleInput] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def consistent(self):
        products = {p.sku: p for p in self.products}
        if len(products) != len(self.products):
            raise ValueError("商品重复")
        keys = [(s.sku, s.day) for s in self.daily_sales]
        if len(set(keys)) != len(keys):
            raise ValueError("商品日销售记录重复")
        for sale in self.daily_sales:
            if sale.sku not in products or sale.day < products[sale.sku].active_from:
                raise ValueError("未知商品或销售早于商品启用日期")
        days = [s.day for s in self.daily_sales]
        if (max(days) - min(days)).days > 365:
            raise ValueError("单个数据集最多覆盖连续366天")
        return self


def canonical(payload):
    data = payload.model_dump(mode="json")
    data["products"].sort(key=lambda p: p["sku"])
    data["daily_sales"].sort(key=lambda s: (s["sku"], s["day"]))
    return data


def parse_csv(content):
    if not content or len(content) > MAX_FILE_BYTES:
        raise ValueError("CSV不能为空且不得超过2 MiB")
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")), strict=True)
        if reader.fieldnames != CSV_FIELDS:
            raise ValueError("CSV列名或顺序不匹配，请下载模板")
        products, sales, errors, keys = {}, [], [], set()
        for row in reader:
            line = reader.line_num
            if len(sales) >= 10000:
                raise ValueError("最多10000条销售记录")
            try:
                if None in row or any(v is None for v in row.values()):
                    raise ValueError("列数不匹配")
                product = DatasetProduct.model_validate({k: row[k] for k in CSV_FIELDS[:4]})
                old = products.get(product.sku)
                if old is not None and old != product:
                    raise ValueError("同一SKU的商品信息不一致")
                sale = SaleInput.model_validate(
                    {
                        "sku": row["sku"],
                        "day": row["day"],
                        **{k: int(row[k]) for k in CSV_FIELDS[5:]},
                    }
                )
                if (sale.sku, sale.day) in keys:
                    raise ValueError("商品日期重复")
                if sale.day < product.active_from:
                    raise ValueError("销售日期早于启用日期")
                products[product.sku] = product
                sales.append(sale)
                keys.add((sale.sku, sale.day))
            except (ValueError, ValidationError):
                # Do not echo arbitrary uploaded values or Pydantic input values.
                errors.append(f"第{line}行：字段类型、范围、日期或商品一致性校验失败")
                if len(errors) >= 20:
                    break
        if errors:
            raise ValueError("；".join(errors))
        try:
            return DatasetInput(products=list(products.values()), daily_sales=sales)
        except ValidationError:
            raise ValueError("需要1–100个商品、1–10000条销售记录，日期跨度最多366天") from None
    except (UnicodeError, csv.Error):
        raise ValueError("需要有效的UTF-8 CSV文件") from None


def csv_bytes(payload):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    products = {p.sku: p for p in payload.products}
    for sale in payload.daily_sales:
        writer.writerow({**products[sale.sku].model_dump(), **sale.model_dump()})
    return output.getvalue().encode("utf-8-sig")


def scenario_payload(scenario, seed=42):
    if scenario not in SCENARIOS:
        raise ValueError("未知模拟场景")
    start = date(2026, 8, 2)
    products = [
        DatasetProduct(
            sku=f"SKU-{i + 1}",
            name=["保温杯", "收纳包", "水壶", "餐盒"][i],
            category="饮具" if i % 2 == 0 else "收纳",
            active_from=date(2026, 9, 10) if scenario == "new_product" and i == 3 else start,
        )
        for i in range(4)
    ]
    sales = []
    for i, product in enumerate(products):
        for offset in range(60):
            day = start + timedelta(days=offset)
            if day < product.active_from or scenario == "no_prior" and offset < 30:
                continue
            if scenario == "missing" and i == 0 and offset == 40:
                continue
            units = 20 - i * 4 + (seed + offset + i) % 3
            refunds = 1
            if offset >= 30:
                if scenario == "sales_decline":
                    units //= 2
                if scenario == "top_decline" and i == 0:
                    units //= 4
                if scenario == "refund_spike":
                    refunds = units // 2
            if scenario == "zero":
                units = refunds = 0
            if scenario == "cross_refund" and offset == 35 and i == 0:
                refunds = units + 10
            price = 2000 + i * 1000
            sales.append(
                SaleInput(
                    sku=product.sku,
                    day=day,
                    paid_units=units,
                    refunded_units=refunds,
                    revenue_cents=units * price,
                    refund_cents=refunds * price,
                )
            )
    return DatasetInput(products=products, daily_sales=sales)


def owned_dataset(db, dataset_id, user_id, tenant_id, committed=True):
    item = db.get(CommerceDataset, dataset_id)
    if (
        not item
        or item.user_id != user_id
        or item.tenant_id != tenant_id
        or committed
        and not item.committed
    ):
        raise NotFound()
    return item


def dataset_info(item):
    days = [s["day"] for s in item.payload["daily_sales"]]
    end = date.fromisoformat(max(days))
    return {
        "id": item.id,
        "name": item.name,
        "sample": item.sample,
        "committed": item.committed,
        "content_hash": item.content_hash,
        "file_hash": item.file_hash,
        "schema_version": 1,
        "products": len(item.payload["products"]),
        "rows": len(days),
        "period_start": min(days),
        "period_end": max(days),
        "suggested_start": max(min(days), (end - timedelta(days=29)).isoformat()),
        "created_at": item.created_at.isoformat() + "Z",
    }


def stage_dataset(repo, user, payload, name, sample, file_hash):
    data = canonical(payload)
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    with repo.session() as db:
        # Same admission lock as task creation; protects quotas and duplicate imports.
        db.scalar(select(User).where(User.id == user.id).with_for_update())
        referenced_bundle = (
            select(AnalysisInputBundle.id)
            .where(AnalysisInputBundle.sales_dataset_id == CommerceDataset.id)
            .exists()
        )
        referenced_review = (
            select(Task.id)
            .where(Task.parameters["dataset_id"].as_string() == CommerceDataset.id)
            .exists()
        )
        db.execute(
            delete(CommerceDataset).where(
                CommerceDataset.user_id == user.id,
                CommerceDataset.committed.is_(False),
                CommerceDataset.created_at < utcnow() - timedelta(days=1),
                ~referenced_bundle,
                ~referenced_review,
            )
        )
        existing = db.scalar(
            select(CommerceDataset).where(
                CommerceDataset.user_id == user.id,
                CommerceDataset.content_hash == digest,
                CommerceDataset.sample == sample,
            )
        )
        if existing:
            db.commit()
            return dataset_info(existing)
        count = db.scalar(
            select(func.count())
            .select_from(CommerceDataset)
            .where(
                CommerceDataset.user_id == user.id,
            )
        )
        if count >= 50:
            raise Conflict("数据集达到50个上限，请联系管理员归档；未确认预览一天后自动清理")
        item = CommerceDataset(
            id=str(uuid4()),
            user_id=user.id,
            tenant_id=user.tenant_id,
            name=name[:120],
            sample=sample,
            payload=data,
            content_hash=digest,
            file_hash=file_hash,
            committed=False,
        )
        db.add(item)
        db.commit()
        return dataset_info(item)


def commit_dataset(repo, user, dataset_id):
    with repo.session() as db:
        db.scalar(select(User).where(User.id == user.id).with_for_update())
        item = owned_dataset(db, dataset_id, user.id, user.tenant_id, committed=False)
        item.committed = True
        db.commit()
        return dataset_info(item)


def list_datasets(repo, user):
    with repo.session() as db:
        return [
            dataset_info(item)
            for item in db.scalars(
                select(CommerceDataset)
                .where(
                    CommerceDataset.user_id == user.id,
                    CommerceDataset.tenant_id == user.tenant_id,
                    CommerceDataset.committed.is_(True),
                )
                .order_by(CommerceDataset.created_at.desc())
            )
        ]
