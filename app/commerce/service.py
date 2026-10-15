import hashlib
import math
import re
from collections import Counter
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Numeric, case, cast, func, or_, select, text

from app.storage.models import Chunk, DailySale, Document, Product

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"
# Tool classification parameter, not a verified enterprise replenishment policy.
OVERSTOCK_THRESHOLD_DAYS = 60


def decimal_string(value, scale=2):
    return str(Decimal(str(value)).quantize(Decimal("1").scaleb(-scale), rounding=ROUND_HALF_UP))


def money(cents):
    return decimal_string(Decimal(cents) / 100)


def fact(key, value, unit, label, entity_id, entity_name, start, end, snapshot=None):
    return {
        "key": key,
        "value": str(value),
        "unit": unit,
        "label": label,
        "entity_id": entity_id,
        "entity_name": entity_name,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "snapshot_as_of": snapshot.isoformat() if snapshot else None,
    }


def _read_only(db, repo):
    if repo.engine.dialect.name == "postgresql":
        db.execute(text("SET TRANSACTION READ ONLY"))
        db.execute(text("SET LOCAL statement_timeout = '5s'"))


def _sales_period(db, tenant_id, start, end, products):
    row = db.execute(
        select(
            func.count(DailySale.id),
            func.coalesce(func.sum(DailySale.paid_units), 0),
            func.coalesce(func.sum(DailySale.refunded_units), 0),
            func.coalesce(func.sum(DailySale.revenue_cents), 0),
            func.coalesce(func.sum(DailySale.refund_cents), 0),
            func.max(case((DailySale.sample.is_(True), 1), else_=0)),
        ).where(DailySale.tenant_id == tenant_id, DailySale.day >= start, DailySale.day <= end)
    ).one()
    count, paid, refunded, revenue, refunds, sample = row
    expected = products * ((end - start).days + 1)
    return {
        "row_count": count,
        "paid": paid,
        "refunded": refunded,
        "revenue": revenue,
        "refunds": refunds,
        "sample": bool(sample),
        "coverage": {
            "expected_records": expected,
            "observed_records": count,
            "missing_records": max(0, expected - count),
            "complete": expected > 0 and count == expected,
        },
    }


def sales_summary(repo, tenant_id, start, end):
    days = (end - start).days + 1
    prior_start, prior_end = start - timedelta(days=days), start - timedelta(days=1)
    with repo.session() as db:
        _read_only(db, repo)
        products = db.scalar(
            select(func.count()).select_from(Product).where(Product.tenant_id == tenant_id)
        )
        current = _sales_period(db, tenant_id, start, end, products)
        prior = _sales_period(db, tenant_id, prior_start, prior_end, products)
    paid, refunded = current["paid"], current["refunded"]
    metrics = {
        "gross_revenue": money(current["revenue"]),
        "refund_amount": money(current["refunds"]),
        "net_revenue": money(current["revenue"] - current["refunds"]),
        "paid_units": str(paid),
        "refunded_units": str(refunded),
        "net_units": str(paid - refunded),
    }
    if paid:
        metrics["refund_rate_pct"] = decimal_string(Decimal(refunded) * 100 / paid)
    limitations = []
    if not current["row_count"]:
        limitations.append("所选周期没有销售数据，不能据此推断真实销售为零")
    elif not current["coverage"]["complete"]:
        limitations.append("销售日期记录不完整：金额与件数仅为已记录部分，缺失记录没有补零")
    prior_net = prior["revenue"] - prior["refunds"]
    comparable = current["coverage"]["complete"] and prior["coverage"]["complete"] and prior_net > 0
    if comparable:
        metrics["net_revenue_change_pct"] = decimal_string(
            Decimal(current["revenue"] - current["refunds"] - prior_net) * 100 / prior_net
        )
    else:
        limitations.append("前期比较不可用：当前或前期记录不完整，或前期净销售额基数非正")
    labels = {
        "gross_revenue": ("支付金额", "CNY"),
        "refund_amount": ("退款金额", "CNY"),
        "net_revenue": ("净销售额", "CNY"),
        "paid_units": ("支付件数", "count"),
        "refunded_units": ("退款件数", "count"),
        "net_units": ("净销量", "count"),
        "refund_rate_pct": ("按记账周期计算的退款件数占支付件数比例", "percent"),
        "net_revenue_change_pct": ("相对前一同长度周期的净销售额变化率", "percent"),
    }
    catalog = {
        key: fact(
            key,
            value,
            labels[key][1],
            labels[key][0],
            tenant_id,
            "已记录经营数据" if not current["coverage"]["complete"] else "经营汇总",
            start,
            end,
        )
        for key, value in metrics.items()
    }
    return {
        "period": {"start": start.isoformat(), "end": end.isoformat(), "timezone": "Asia/Shanghai"},
        "currency": "CNY",
        "metrics": metrics,
        "fact_catalog": catalog,
        "row_count": current["row_count"],
        "coverage": current["coverage"],
        "previous_period": {
            "start": prior_start.isoformat(),
            "end": prior_end.isoformat(),
            "net_revenue": money(prior_net),
            "coverage": prior["coverage"],
            "comparable": comparable,
        },
        "sample": current["sample"] or prior["sample"],
        "limitations": limitations,
        "definitions": {
            "net_revenue": "支付金额减本周期记账退款金额，不含税费/广告/物流成本",
            "refund_rate_pct": "本周期退款件数 / 本周期支付件数；按记账周期统计，非订单队列退款率",
            "coverage": "当前商品目录中的每个商品每天应有一行；零销量需要显式零行；未建模上架生命周期",
        },
    }


def inventory_risks(repo, tenant_id, start, end):
    days = (end - start).days + 1
    with repo.session() as db:
        _read_only(db, repo)
        sales = (
            select(
                DailySale.product_id,
                func.sum(DailySale.paid_units - DailySale.refunded_units).label("units"),
                func.count(DailySale.id).label("observed_days"),
            )
            .where(DailySale.tenant_id == tenant_id, DailySale.day >= start, DailySale.day <= end)
            .group_by(DailySale.product_id)
            .subquery()
        )
        coverage = cast(Product.stock, Numeric) * days / func.nullif(sales.c.units, 0)
        unavailable = (func.coalesce(sales.c.observed_days, 0) < days) | (
            func.coalesce(sales.c.units, 0) <= 0
        )
        priority = case(
            (unavailable, 0),
            (coverage < Product.lead_days, 1),
            (coverage > OVERSTOCK_THRESHOLD_DAYS, 2),
            else_=3,
        )
        severity = case(
            (coverage < Product.lead_days, coverage / Product.lead_days),
            (coverage > OVERSTOCK_THRESHOLD_DAYS, -coverage),
            else_=coverage,
        )
        rows = db.execute(
            select(Product, sales.c.units, sales.c.observed_days)
            .outerjoin(sales, sales.c.product_id == Product.id)
            .where(Product.tenant_id == tenant_id)
            .order_by(priority, severity, Product.sku)
            .limit(100)
        ).all()
        total = db.scalar(
            select(func.count()).select_from(Product).where(Product.tenant_id == tenant_id)
        )
    items, metrics, catalog = [], {}, {}
    gaps = ["库存为标注日期的快照；历史销量仅用于估计，不自动执行补货"]
    for product, units, observed in rows:
        observed = observed or 0
        complete = observed == days
        value = (
            decimal_string(Decimal(product.stock) * days / units)
            if complete and units and units > 0
            else None
        )
        risk = (
            "incomplete"
            if not complete
            else "no_sales"
            if value is None
            else "stockout"
            if Decimal(value) < product.lead_days
            else "overstock"
            if Decimal(value) > OVERSTOCK_THRESHOLD_DAYS
            else "normal"
        )
        age = (end - product.stock_as_of).days
        if not complete:
            gaps.append("存在销售日期不完整的商品，未将缺失天数补零，不能估计其库存覆盖")
        if age > 14 or age < 0:
            gaps.append("库存快照与销量周期不一致：存在超过14天的旧快照或周期结束后的快照")
        item = {
            "sku": product.sku,
            "name": product.name,
            "stock": product.stock,
            "stock_as_of": product.stock_as_of.isoformat(),
            "lead_days": product.lead_days,
            "net_units": units if observed else None,
            "coverage_days": value,
            "risk": risk,
            "observed_days": observed,
            "missing_days": days - observed,
            "complete": complete,
            "snapshot_age_days": age,
            "snapshot_aligned": 0 <= age <= 14,
            "sample": product.sample,
        }
        items.append(item)
        for field, label, unit, field_value in [
            ("stock", "库存快照数量", "count", product.stock),
            ("net_units", "已记录净销量", "count", units if observed else None),
            ("coverage_days", "按历史日均净销量估算的库存覆盖天数", "days", value),
            ("lead_days", "当前商品配置的补货提前期", "days", product.lead_days),
            (
                "overstock_threshold_days",
                "本工具积压判定阈值（非企业规则）",
                "days",
                OVERSTOCK_THRESHOLD_DAYS,
            ),
        ]:
            if field_value is not None:
                key = f"{product.sku}.{field}"
                metrics[key] = str(field_value)
                catalog[key] = fact(
                    key,
                    field_value,
                    unit,
                    label,
                    product.sku,
                    product.name,
                    start,
                    end,
                    product.stock_as_of,
                )
    if total > len(rows):
        gaps.append(
            "库存结果按缺数、缺货、积压、正常风险排序，仅展示前100个商品；未展示不等于无风险"
        )
    return {
        "items": items,
        "metrics": metrics,
        "fact_catalog": catalog,
        "sample": any(r[0].sample for r in rows),
        "total_products": total,
        "displayed_products": len(rows),
        "truncated": total > len(rows),
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "definitions": {
            "coverage_days": "完整日期记录的库存快照 / 所选周期日均净销量；不代表未来销量预测",
            "stockout": "覆盖天数小于补货提前期",
            "overstock": f"覆盖天数大于{OVERSTOCK_THRESHOLD_DAYS}天；这是本工具算法阈值，非企业规则",
            "lead_days": "当前商品配置值，不证明该提前期在历史销量周期内有效；下单前须确认实际交期",
        },
        "limitations": list(dict.fromkeys(gaps)),
    }


def terms(value):
    words = re.findall(r"[a-z0-9]+", value.lower())
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", value))
    return words + [chinese[i : i + 2] for i in range(len(chinese) - 1)]


def search_documents(repo, ctx, query, limit=5):
    # Small reproducible lexical baseline; it is not a vector/reranker substitute.
    with repo.session() as db:
        statement = (
            select(Document, Chunk)
            .join(Chunk, Document.id == Chunk.document_id)
            .where(
                Document.tenant_id == ctx.tenant_id,
                or_(
                    Document.user_id.is_(None),
                    (Document.user_id == ctx.user_id)
                    & (Document.conversation_id == ctx.conversation_id),
                ),
            )
        )
        count = db.scalar(select(func.count()).select_from(statement.subquery()))
        if count > 10000:
            raise ValueError("授权文档超过10000片段的词法检索容量，需要独立索引")
        rows = db.execute(statement).all()
    query_terms = set(terms(query))
    counters = [Counter(terms(chunk.text)) for _, chunk in rows]
    document_frequencies = Counter(term for counter in counters for term in counter)
    average = sum(sum(c.values()) for c in counters) / len(counters) if counters else 1
    ranked = []
    for (doc, chunk), counts in zip(rows, counters, strict=True):
        score = 0.0
        for term in query_terms:
            df = document_frequencies[term]
            frequency = counts[term]
            idf = math.log(1 + (len(rows) - df + 0.5) / (df + 0.5))
            score += (
                idf
                * frequency
                * 2.5
                / (frequency + 1.5 * (0.25 + 0.75 * sum(counts.values()) / (average or 1)))
            )
        if score > 0:
            ranked.append((score, doc, chunk))
    ranked.sort(key=lambda r: (-r[0], r[2].id))
    return [
        {
            "document_id": d.id,
            "title": d.name,
            "locator": f"document://{d.id}/{c.locator}",
            "excerpt": c.text,
            "sample": d.sample,
            "score": round(score, 4),
        }
        for score, d, c in ranked[:limit]
    ]


def add_document(
    repo, tenant_id, name, fragments, user_id=None, conversation_id=None, sample=False
):
    content = "\n".join(fragment[1] for fragment in fragments)
    digest = hashlib.sha256(content.encode()).hexdigest()
    with repo.session() as db:
        prior = db.scalar(
            select(Document).where(
                Document.tenant_id == tenant_id,
                Document.user_id == user_id,
                Document.conversation_id == conversation_id,
                Document.name == name,
                Document.content_hash == digest,
            )
        )
        if prior:
            return prior.id
        count = db.scalar(
            select(func.count()).select_from(Document).where(Document.tenant_id == tenant_id)
        )
        if count >= 100:
            raise ValueError("文档数量达到本版本上限")
        doc = Document(
            id=str(uuid4()),
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            name=name,
            content_hash=digest,
            sample=sample,
        )
        db.add(doc)
        db.flush()
        for locator, text_content in fragments:
            for offset in range(0, len(text_content), 1000):
                db.add(
                    Chunk(
                        id=str(uuid4()),
                        document_id=doc.id,
                        locator=f"{locator};chars={offset}-{min(offset + 1200, len(text_content))}",
                        text=text_content[offset : offset + 1200],
                    )
                )
        db.commit()
        return doc.id


def seed_commerce(repo):
    for tenant in ("demo", "other"):
        with repo.session() as db:
            if db.scalar(select(Product).where(Product.tenant_id == tenant).limit(1)):
                continue
            products = []
            for sku, name, price, stock, lead, daily_units in [
                ("CUP-01", "轻便保温杯", 12900, 20, 7, 10),
                ("BAG-02", "通勤收纳包", 8900, 900, 14, 4),
                ("LAMP-03", "桌面阅读灯", 15900, 150, 10, 5),
            ]:
                product = Product(
                    id=str(uuid4()),
                    tenant_id=tenant,
                    sku=sku,
                    name=name,
                    category="日用生活",
                    price_cents=price,
                    stock=stock,
                    lead_days=lead,
                    stock_as_of=date(2026, 9, 30),
                    sample=True,
                )
                db.add(product)
                products.append((product, daily_units))
            db.flush()
            for product, daily_units in products:
                for i in range(60):
                    day = date(2026, 8, 2) + timedelta(days=i)
                    units = daily_units + i % 3
                    refunds = 1 if product.sku == "CUP-01" and i % 5 == 0 else 0
                    db.add(
                        DailySale(
                            tenant_id=tenant,
                            product_id=product.id,
                            day=day,
                            paid_units=units,
                            refunded_units=refunds,
                            revenue_cents=units * product.price_cents,
                            refund_cents=refunds * product.price_cents,
                            sample=True,
                        )
                    )
            db.commit()
        for path in (DATA_ROOT / "documents").glob("*.md"):
            add_document(
                repo,
                tenant,
                path.name,
                [
                    (
                        "lines=1-" + str(len(path.read_text().splitlines())),
                        path.read_text(encoding="utf-8"),
                    )
                ],
                sample=True,
            )
