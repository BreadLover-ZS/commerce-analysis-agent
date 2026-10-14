from datetime import date

from sqlalchemy import delete, select

from app.commerce.service import seed_commerce
from app.storage.models import DailySale, Product


def prepare_case(repo, case):
    # Each case starts from the same fixture; previous fault injection cannot leak.
    with repo.session() as db:
        db.execute(delete(DailySale).where(DailySale.tenant_id == "demo"))
        db.execute(delete(Product).where(Product.tenant_id == "demo"))
        db.commit()
    seed_commerce(repo)
    setup = case.get("setup", {})
    if set(setup) - {"missing_sale", "stock", "snapshot", "zero_sales_sku"}:
        raise ValueError("未知评估fixture修改")
    with repo.session() as db:
        products = {
            p.sku: p for p in db.scalars(select(Product).where(Product.tenant_id == "demo"))
        }
        if item := setup.get("missing_sale"):
            db.execute(
                delete(DailySale).where(
                    DailySale.product_id == products[item["sku"]].id,
                    DailySale.day == date.fromisoformat(item["day"]),
                )
            )
        if item := setup.get("stock"):
            products[item["sku"]].stock = item["value"]
        if item := setup.get("snapshot"):
            products[item["sku"]].stock_as_of = date.fromisoformat(item["day"])
        if sku := setup.get("zero_sales_sku"):
            for sale in db.scalars(
                select(DailySale).where(DailySale.product_id == products[sku].id)
            ):
                sale.paid_units = sale.refunded_units = sale.revenue_cents = sale.refund_cents = 0
        db.commit()
