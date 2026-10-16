"""Evidence-gated candidate explanations. Selection never establishes causality."""

TEXTS = {
    "sales_drivers": "待验证假设：支付金额变化可能与销量、成交单价或促销有关；需补充逐日销售、价格及活动记录，不能由汇总金额确定原因。",
    "refund_drivers": "待验证假设：退款相关变化可能涉及退款原因或跨期记账；需核对原订单周期、退款时间与售后明细，记账占比不能替代订单队列退款率。",
    "product_mix": "待验证假设：商品组合或上架时间变化可能影响整体销售；需补充商品生命周期、分商品逐日记录与活动资料，不能将贡献分解当作因果。",
    "daily_structure": "待核对事项：期间净变化持平可能掩盖逐日结构差异；需查看日级记录，不据此认定实际存在波动。",
    "data_quality": "待核对事项：应先核对记录完整性和统计口径，再讨论变化；数据来源与模拟标记必须保留，模拟场景不能证明真实业务效果。",
}


def allowed_hypotheses(payload):
    allowed = {"data_quality"}
    if not payload["previous_period"]["comparable"] or int(payload["metrics"]["paid_units"]) == 0:
        return {key: TEXTS[key] for key in sorted(allowed)}
    metrics = payload["metrics"]
    if metrics["gross_revenue_delta"] != "0.00":
        allowed.add("sales_drivers")
    if (
        metrics["refund_amount_delta"] != "0.00"
        or metrics["refunded_units_delta"] != "0"
        or any(f["code"] == "refund_increase" for f in payload["findings"])
    ):
        allowed.add("refund_drivers")
    if any(e["delta_cents"] for e in payload["dimensions"]["products"]):
        allowed.add("product_mix")
    if metrics["net_revenue_delta"] == "0.00":
        allowed.add("daily_structure")
    return {key: TEXTS[key] for key in sorted(allowed)}
