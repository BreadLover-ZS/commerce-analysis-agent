"""Fixed answer v3: model-selected actions, server-bound premises and rendering."""

ACTIONS = {
    "verify_activation": (
        "activation_boundary",
        "核对商品启用日期与适用范围；启用前不适用、不要求补零，启用后零销量须有真实显式零记录，缺行不能造零。",
    ),
    "verify_records": (
        "record_coverage",
        "核对授权对象适用日期的原始记录与统计口径；缺失须追溯原始资料，不将缺失记录补为零。",
    ),
    "verify_refund_timing": (
        "accounting_refund",
        "核对原订单周期、退款发生日期和原因资料；记账退款件数占比不等于同批订单最终退款率。",
    ),
    "verify_price_activity": (
        "unverified_causality",
        "补充价格、活动与逐日记录以核对待验证原因；变化贡献只描述记账关系，不证明因果。",
    ),
    "obtain_costs": (
        "missing_costs",
        "补充成本与费用口径后再计算利润；净销售额未扣成本，不能当作利润。",
    ),
    "obtain_traffic": (
        "missing_traffic",
        "补充访客、曝光及订单关联和统计口径后再计算转化率；不能用净销售额变化率替代。",
    ),
}


def action_catalog(payload):
    if payload.get("answer_version") == 6:
        from app.commerce.explanation_contract_v6 import contract

        if not contract(payload.get("objective", ""))["recommendations_allowed"]:
            return {}
    skus = payload.get("scope", {}).get("skus", [])
    return {
        key: {"precondition_id": condition, "scope_skus": skus, "text": text}
        for key, (condition, text) in ACTIONS.items()
    }


def render_actions(actions, payload):
    catalog = action_catalog(payload)
    texts = []
    for action in actions:
        value = action.model_dump() if hasattr(action, "model_dump") else action
        allowed = catalog.get(value["action_id"])
        if (
            allowed is None
            or value["precondition_id"] != allowed["precondition_id"]
            or sorted(value["scope_skus"]) != sorted(allowed["scope_skus"])
        ):
            raise ValueError("recommendation_premise_or_scope_rejected")
        texts.append(allowed["text"])
    if len(texts) != len(set(texts)):
        raise ValueError("duplicate_recommendation_action")
    return texts
