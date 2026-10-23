"""Versioned request items shared by diagnosis v11 and fixed answer v3.

This is a bounded language adapter, never a permission or arithmetic authority.
The retained item/polarity/scope survives independently of tool selection.
"""

import re

from app.diagnosis.intent_v3 import CLAUSES, RULES
from app.diagnosis.intent_v3 import objectives as legacy_goals

METRICS = {
    "profit": r"净?利润|\bprofit\b",
    "conversion_rate": r"转化率|\bconversion rate\b",
    "roi": r"(?<![a-z])roi(?![a-z])",
    "cohort_refund_rate": r"(?:同一批|同批|最终)(?:订单)?退款率|\bcohort refund rate\b",
    "write_operation": r"自动改价|采购下单|自动发券",
    "net_revenue": r"净销售额|销售额|\brevenue\b",
    "gross_revenue": r"支付金额",
    "refund_amount": r"退款金额",
    "paid_units": r"支付件数|支付分母",
    "refunded_units": r"退款件数",
    "refund_ratio": r"记账.*占比|退款占比|退款率",
}
UNSUPPORTED = {"profit", "conversion_rate", "roi", "cohort_refund_rate", "write_operation"}
NEGATION = re.compile(
    r"(?:不要|不能|不得|无需|不必|不用|不需要|不应|不|勿|别|禁止|避免)"
    r"(?:再|直接|擅自)?(?:计算|核算|算|预测|估计|生成|输出|提供|推断|声称|宣称|认定|执行)?"
    r"(?:同一批订单|同批订单|订单|记账期|所谓的|实际的|真实的)?$"
)
DOUBLE = re.compile(r"不能|不可|不得|不要|不必|不|并非|不是")


def request_contract(query):
    items, canonical = [], []
    for clause in CLAUSES.split(query):
        if not clause.strip():
            continue
        literal_rule = bool(RULES.search(clause) and re.search(r"引用|逐字|原文|检索|查阅", clause))
        denied_spans = []
        for metric, pattern in METRICS.items():
            for match in re.finditer(pattern, clause, re.I):
                before, after = clause[: match.start()].rstrip(), clause[match.end() :].lstrip()
                neg = NEGATION.search(before)
                negative = bool(
                    neg
                    and not DOUBLE.search(before[: neg.start()])
                    or re.match(
                        r"(?:不可|不能|无法|不应|不足以)(?:直接)?(?:计算|核算|算|推断|提供)", after
                    )
                )
                polarity = "mentioned" if literal_rule else "excluded" if negative else "requested"
                items.append({"metric": metric, "polarity": polarity, "clause": clause})
                if polarity != "requested" or metric in UNSUPPORTED:
                    denied_spans.append(match.span())
        clean = clause
        for start, end in sorted(set(denied_spans), reverse=True):
            clean = clean[:start] + clean[end:]
        canonical.append(clean)
    canonical = "；".join(canonical)
    unsupported = sorted(
        {i["metric"] for i in items if i["polarity"] == "requested" and i["metric"] in UNSUPPORTED}
    )
    supported = {
        i["metric"]
        for i in items
        if i["polarity"] == "requested" and i["metric"] not in UNSUPPORTED
    }
    goals = legacy_goals(canonical)
    if (
        unsupported
        and not supported
        and not re.search(r"规则|文档|库存|贡献|逐日|日序列", canonical)
    ):
        goals = []
    per_entity = bool(re.search(r"逐商品|各商品|分商品|按商品|每个商品|逐SKU|每个SKU", query, re.I))
    components = (
        sorted(supported & {"net_revenue", "gross_revenue", "refund_amount"}) if per_entity else []
    )
    return {
        "version": "answer-contract-v1",
        "items": items,
        "goals": goals,
        "canonical_query": canonical,
        "unsupported_requests": unsupported,
        "component_metrics": components,
        "dimension": "categories" if "品类" in query else "products",
        "periods": ["current", "comparison", "delta"],
        "presentation": "each_authorized_sku" if components else "authorized_scope",
    }


def unsupported_gaps(query):
    return [
        f"请求指标 {metric}：当前固定数据或只读工具不支持；保留其他可回答项"
        for metric in request_contract(query)["unsupported_requests"]
    ]
