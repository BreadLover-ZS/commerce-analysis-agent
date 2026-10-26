"""Bounded, clause-scoped explanation requests. This never grants data/tool access."""

import re

CODES = {"sales_drivers", "refund_drivers", "product_mix", "daily_structure", "data_quality"}
TOPICS = {
    "sales_drivers": r"支付|销量|销售原因|payment|sales drivers?",
    "refund_drivers": r"退款|refund",
    "product_mix": r"商品组合|商品结构|product mix",
    "daily_structure": r"逐日|每日|daily",
    "data_quality": r"数据质量|数据完整|记录完整|data quality",
}
ACTION = r"解释|解读|讨论|假设|原因|explain|interpret|discuss"
DENY = r"(?:(?:不要|不用|无需|不需要|不得|禁止|不必|不想|不应(?:该)?|请勿|勿|(?<!分)别|不)\s*(?:(?:再|去|分别|逐一|逐个|各类|每类|详细|进一步)\s*)*(?:解释|解读|讨论|分析|(?:提出|给出)\s*(?:解释|假设|原因))|(?:do not|don't|without)\s*(?:explain|interpret|discuss|analy[sz]e))"


def contract(query):
    if re.search(r"(?:不要|不得|不能|无需)\s*(?:不|别)\s*(?:解释|解读|讨论|分析)", query):
        raise ValueError("解释要求包含不可靠双重否定，请明确需要或排除的方向")
    requested, excluded = set(), set()
    for clause in re.split(r"[，,；;。\n]|(?=但是|但|而不|而是|并且不|并不|且不|同时不)", query):
        topics = {code for code, pattern in TOPICS.items() if re.search(pattern, clause, re.I)}
        negative = bool(re.search(DENY, clause, re.I))
        if negative:
            excluded.update(topics or CODES)
        elif re.search(ACTION, clause, re.I) and re.search(
            r"逐一|分别|每类|各类|每一类|每种|各项|separately|each", clause, re.I
        ):
            requested.update(topics)
    if requested & excluded:
        raise ValueError("解释方向的要求与排除冲突，请明确需要核对的范围")
    return {
        "version": "explanation-contract-v1",
        "required": sorted(requested),
        "excluded": sorted(excluded),
        "recommendations_allowed": not bool(
            re.search(
                r"(?:不要|不需要|无需|不用|不得|别|不必|请勿)\s*(?:(?:再|给出|提供|提出)\s*)*(?:任何)?(?:行动)?建议",
                query,
            )
        ),
    }


def allowed(payload, options):
    excluded = set(contract(payload.get("objective", ""))["excluded"])
    return {code: text for code, text in options.items() if code not in excluded}
