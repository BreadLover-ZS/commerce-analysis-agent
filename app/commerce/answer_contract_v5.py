"""Boundary semantics v2; frozen v4 requests keep their original interpretation."""

import re

from app.commerce.answer_contract_v3 import bound_request as previous_bound
from app.commerce.answer_contract_v3 import request_contract as base_contract
from app.commerce.answer_contract_v4 import VALUES
from app.commerce.answer_contract_v4 import initial_scope as initial_scope
from app.commerce.answer_contract_v4 import request_contract as previous_contract


def _boundary(query):
    directives = re.compile(
        r"(?:无需|无须|不用|不必|不要|不需|禁止|别|暂不)(?:再|另外|额外)?(?:说明|解释|讨论|列明|列出)"
    )
    clauses = [
        c for c in re.split(r"[，,；;。！？!?]", query) if c.strip() and not directives.search(c)
    ]
    context = "；".join(clauses)
    for clause in clauses:
        if previous_contract(clause)["boundary_intent"]:
            return True
        directive = bool(re.search(r"说明|解释|列明|列出|哪些|什么|为什么|为何|有什么", clause))
        unavailability = bool(
            re.search(
                r"不适用|未启用|启用前|尚未启用|不能(?:用于)?(?:比较|计算)|无法(?:比较|计算)|不支持(?:计算)?|不成立|局限",
                clause,
            )
        )
        boundary = bool(re.search(r"边界|限制|局限|原因|为什么|为何|哪些", clause))
        # "比较期不能比较，请解释原因" spans two clauses; retain that subject.
        if (
            directive
            and boundary
            and (unavailability or re.search(r"不适用|未启用|启用前|不能(?:用于)?比较", context))
        ):
            return True
    return False


def request_contract(query):
    c = previous_contract(query)
    text = c["canonical_query"]
    boundary = _boundary(text)
    values = bool(
        re.search(
            r"列出|给出|计算|核算|多少|变化金额|变化数值|差额|差值|核对(?:本期|两期|比较期|前期)?(?:的)?(?:退款金额|支付金额|净销售额|净额)",
            text,
        )
    )
    pure = boundary and not values
    requested = [] if pure else sorted(set(c["requested_metrics"]) & VALUES)
    c.update(
        version="answer-contract-v5",
        request_kind="boundary" if boundary else "values",
        boundary_intent=boundary,
        pure_boundary=pure,
        value_metrics=requested,
        component_metrics=[m for m in base_contract(query)["component_metrics"] if m in requested],
        amount_identity_requested=bool(
            {"gross_revenue", "refund_amount"} <= set(requested)
            and re.search(r"差额|变化|变动|对账|恒等式", text)
        ),
    )
    return c


def bound_request(query, bundle, products):
    return {**previous_bound(query, bundle, products), **request_contract(query)}


def unsupported_gaps(query):
    return [
        f"请求指标 {m}：当前固定数据或只读工具不支持；保留其他可回答项"
        for m in request_contract(query)["unsupported_requests"]
    ]
