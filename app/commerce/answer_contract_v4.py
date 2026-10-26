"""v15 request semantics: value obligations, boundary answers and optional rule interpretation."""

import re

from app.commerce.answer_contract_v3 import bound_request as previous_bound
from app.commerce.answer_contract_v3 import initial_scope as initial_scope
from app.commerce.answer_contract_v3 import request_contract as previous_contract

MONEY = {"net_revenue", "gross_revenue", "refund_amount"}
VALUES = MONEY | {"paid_units", "refunded_units"}


def request_contract(query):
    c = previous_contract(query)
    text = c["canonical_query"]
    # Classification is based only on the immutable user request, never evaluation labels.
    boundary = bool(
        re.search(
            r"说明.*分母问题|有什么限制|不可比较的边界|尚不能比较|尚不能计算|哪些.*(?:可算|不能算)|哪些.*(?:具备|缺少)|列明.*(?:资料|条件)|说明.*(?:缺行|缺数|缺失).*(?:限制|边界)",
            text,
        )
    )
    explicit_values = bool(
        re.search(
            r"列出|给出|计算|核算|多少|变化金额|变化数值|差额|差值|核对(?:本期|两期|比较期|前期)?(?:的)?(?:退款金额|支付金额|净销售额|净额)",
            text,
        )
    )
    pure_boundary = boundary and not explicit_values
    # A delta-only question does not silently demand before/after absolute amounts.
    delta_only = bool(
        re.search(r"各变化多少|变化了多少|变化金额|三个变化额|差额|差值", text)
        and not re.search(r"两期|本期|比较期|前期|汇总|逐日|每日", text)
    )
    parts = {
        m: (["delta"] if delta_only and m in VALUES else list(v))
        for m, v in c["metric_periods"].items()
    }
    requested_values = [] if pure_boundary else [m for m in c["requested_metrics"] if m in VALUES]
    rule_interpretation = bool(
        re.search(r"规则|文档|条款|政策", text)
        and re.search(r"解释|解读|说明.*(?:含义|如何|适用)", text)
        and not re.search(r"不要(?:解释|解读)|仅(?:查阅|引用)|只(?:查阅|引用)", text)
    )
    identity = bool(
        {"gross_revenue", "refund_amount"} <= set(requested_values)
        and re.search(r"差额|变化|变动|对账|恒等式", text)
    )
    c.update(
        version="answer-contract-v4",
        request_kind="boundary" if boundary else "values",
        boundary_intent=boundary,
        pure_boundary=pure_boundary,
        value_metrics=sorted(requested_values),
        metric_periods=parts,
        periods=[
            p for p in ["current", "comparison", "delta"] if any(p in v for v in parts.values())
        ]
        or c["periods"],
        component_metrics=[m for m in c["component_metrics"] if m in requested_values],
        amount_identity_requested=identity,
        rule_interpretation_requested=rule_interpretation,
    )
    return c


def bound_request(query, bundle, products):
    return {**previous_bound(query, bundle, products), **request_contract(query)}


def unsupported_gaps(query):
    return [
        f"请求指标 {m}：当前固定数据或只读工具不支持；保留其他可回答项"
        for m in request_contract(query)["unsupported_requests"]
    ]
