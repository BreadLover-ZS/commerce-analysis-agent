"""Pure two-period aggregation shared by fixed review and diagnostic tools."""

from datetime import timedelta

from app.commerce.datasets import DatasetInput


def comparison_dates(start, end, comparison_start=None, comparison_end=None):
    days = (end - start).days + 1
    if not 1 <= days <= 90:
        raise ValueError("分析周期必须为1–90天")
    if (comparison_start is None) != (comparison_end is None):
        raise ValueError("须同时提供对比期起止日期")
    if comparison_start is None:
        comparison_start, comparison_end = start - timedelta(days=days), start - timedelta(days=1)
    if (comparison_end - comparison_start).days + 1 != days or comparison_end >= start:
        raise ValueError("两期须等长且对比期早于本期，不得重叠")
    return comparison_start, comparison_end


class SalesCube:
    def __init__(self, payload):
        self.payload = DatasetInput.model_validate(payload)
        self.products = {p.sku: p for p in self.payload.products}
        self.rows = self.payload.daily_sales

    def aggregate(self, skus, first, last):
        chosen = [s for s in self.rows if s.sku in skus and first <= s.day <= last]
        expected = sum(
            max(0, (last - max(first, self.products[k].active_from)).days + 1) for k in skus
        )
        revenue = sum(s.revenue_cents for s in chosen)
        refund = sum(s.refund_cents for s in chosen)
        return {
            "revenue": revenue,
            "refund": refund,
            "net": revenue - refund,
            "paid": sum(s.paid_units for s in chosen),
            "refunded": sum(s.refunded_units for s in chosen),
            "coverage": {
                "expected_records": expected,
                "observed_records": len(chosen),
                "missing_records": expected - len(chosen),
                "complete": len(chosen) == expected,
            },
        }
