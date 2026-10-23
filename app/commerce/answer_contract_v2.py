"""Frozen v2 request adapter for diagnosis v13; never an authority grant."""

import re
from dataclasses import dataclass

from app.commerce.answer_contract import UNSUPPORTED
from app.commerce.answer_contract import request_contract as old_contract
from app.diagnosis.intent_v3 import CLAUSES

COMPONENT_METRICS = (
    "net_revenue",
    "gross_revenue",
    "refund_amount",
    "paid_units",
    "refunded_units",
)
PER_ENTITY = re.compile(
    r"(?:逐(?:个)?|每(?:一)?个|各(?:个)?|所有|全部|分|按)\s*(?:已)?(?:授权(?:的)?)?\s*(?:商品|SKU)",
    re.I,
)
EXPLORATION = re.compile(
    r"为什么|为何|原因|解释|分析|诊断|来源|驱动|影响|归因|贡献|排名|排行|抵消|逐日|每日|日序列|库存|规则|占比|分母"
)
CONTRIBUTION = re.compile(r"贡献|排名|排行|排序|头部|抵消|哪些商品|哪个商品|定位.*(?:下降|增长)")


@dataclass(frozen=True)
class EntitySelection:
    included: tuple[str, ...]
    excluded: tuple[str, ...]
    selected: tuple[str, ...]

    def as_json(self):
        return {
            "included": list(self.included),
            "excluded": list(self.excluded),
            "selected": list(self.selected),
        }


def _negative(clause, start, end, identifiers):
    before, after = clause[:start], clause[end:]
    # The same exclusion may enumerate several identifiers before the suffix.
    if re.search(r"除(?:了)?", before) and re.search(r"(?:以外|之外|外)", after):
        return True
    for identifier in identifiers:
        before = re.sub(r"(?<![\w-])" + re.escape(identifier) + r"(?![\w-])", "", before)
    before = re.sub(r"[、\s和与及]+$", "", before)
    negative = bool(
        re.search(
            r"(?:不要|别|不看|排除|不分析|不核对|不包括|不包含|不考虑|剔除|除去)(?:再|分析|核对|查看|看|比较|计算|商品|SKU|\s)*$",
            before,
            re.I,
        )
    )
    if negative and re.search(r"不要不|不能不|并非不|不是不", before):
        raise ValueError("对象包含双重否定，请明确需要核对的商品")
    return negative


def entity_selection(query, allowed):
    included, excluded = set(), set()
    for clause in CLAUSES.split(query):
        for sku in allowed:
            for match in re.finditer(
                r"(?<![A-Za-z0-9_-])" + re.escape(sku) + r"(?![A-Za-z0-9_-])", clause
            ):
                (
                    excluded if _negative(clause, match.start(), match.end(), allowed) else included
                ).add(sku)
        for match in re.finditer(r"(?:商品|SKU)\s*([A-Za-z0-9_-]+)", clause, re.I):
            sku = match.group(1)
            if sku not in allowed and not _negative(clause, match.start(1), match.end(1), allowed):
                raise ValueError("请求包含不存在或未授权的商品，请明确授权目录中的对象")
    if included & excluded:
        raise ValueError("对象包含与排除条件冲突，请明确需要核对的商品")
    selected = (included or set(allowed)) - excluded
    if not selected:
        raise ValueError("请求排除了全部授权商品，请明确需要核对的对象")
    return EntitySelection(
        tuple(sorted(included)), tuple(sorted(excluded)), tuple(sorted(selected))
    )


def request_contract(query):
    normalized = query
    for pattern, replacement in (
        (r"净额", "净销售额"),
        (r"支付额|付款金额", "支付金额"),
        (r"退款额", "退款金额"),
        (r"支付数量", "支付件数"),
        (r"退款数量", "退款件数"),
    ):
        normalized = re.sub(pattern, replacement, normalized)
    c = old_contract(normalized)
    metrics = sorted(
        {
            i["metric"]
            for i in c["items"]
            if i["polarity"] == "requested" and i["metric"] not in UNSUPPORTED
        }
    )
    each = bool(PER_ENTITY.search(normalized) or re.search(r"分别.*商品|商品.*分别", normalized))
    changed = bool(re.search(r"差额|差值|变化|变动|增减|增长|下降", c["canonical_query"]))
    if changed:
        periods = ["current", "comparison", "delta"]
    elif re.search(r"两期|本期.*(?:比较期|前期)|(?:比较期|前期).*本期", normalized):
        periods = ["current", "comparison"]
    elif re.search(r"比较期|前期", normalized):
        periods = ["comparison"]
    elif "本期" in normalized:
        periods = ["current"]
    else:
        periods = ["current", "comparison", "delta"]
    dimension = (
        ("category" if re.search(r"品类|类目", normalized) else "sku")
        if CONTRIBUTION.search(normalized)
        else None
    )
    c.update(
        version="answer-contract-v2",
        requested_metrics=metrics,
        component_metrics=[m for m in COMPONENT_METRICS if m in metrics] if each else [],
        periods=periods,
        per_entity=each,
        contribution_dimension=dimension,
        matrix_literal=bool(each and metrics and not EXPLORATION.search(c["canonical_query"])),
    )
    return c


def bound_request(query, bundle):
    return {
        **request_contract(query),
        "entity_scope": entity_selection(query, bundle["allowed_skus"]).as_json(),
    }


def initial_scope(query, bundle):
    return {
        "skus": list(entity_selection(query, bundle["allowed_skus"]).selected),
        "objective": None,
    }
