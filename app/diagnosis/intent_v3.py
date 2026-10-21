"""Versioned intent hints, never an authorization boundary or a business oracle.

Only the user question is classified here. Dataset names, retrieved instructions
and prior model prose cannot add capabilities, sources, scope or budget.
"""

import re

CLAUSES = re.compile(r"[，,；;。.!?？\n]|但是|不过|然而|同时|另外|并且|并(?!非|不)")
RULES = re.compile(r"规则|规定|政策|条款|文档|\brules?\b|\bpolicy\b", re.I)
UNSUPPORTED = re.compile(
    r"最终(?:订单)?退款率|利润|转化率|(?<![a-z])roi(?![a-z])|\bprofit\b|\bconversion rate\b|\b(?:final|cohort) refund rate\b|自动改价|采购下单|自动发券",
    re.I,
)
NEGATIVE = re.compile(
    r"(?:不|不要|勿|别|无需|不必|不能|不得|禁止|避免)"
    r"(?:再|直接|擅自)?(?:计算|核算|预测|估计|生成|输出|提供|推断|声称|宣称|认定|称(?:作|为)?|执行)?"
    r"(?:同一批订单|同批订单|订单|记账期|所谓的|实际的|真实的)?$"
    r"|(?:不是|不等于|不代表|不能视作|不能等同于|非)$"
    r"|(?:不|不要|勿|不得|避免)(?:把|将)(?:净销售额|记账(?:期|周期)退款(?:件数)?占比|本期占比|该占比)(?:称为|称作|当作|当成|等同于|视为)$"
    r"|(?:\bdo not|\bdon't|\bnever|\bno need to|\bnot)\b(?:\s+(?:calculate|compute|estimate|claim|execute))?\s*$",
    re.I,
)
DOUBLE_NEGATIVE = re.compile(r"(?:不能|不可|不得|不要|不必|不|并非|不是)\s*$")


def negated(clause, match):
    before, after = clause[: match.start()].rstrip(), clause[match.end() :].lstrip()
    negative = NEGATIVE.search(before)
    if negative and not DOUBLE_NEGATIVE.search(before[: negative.start()]):
        return True
    return bool(re.match(r"(?:不可|不能|无法|不应|不足以)(?:直接)?(?:计算|核算|推断|提供)", after))


def unsupported_question(query):
    return any(
        not negated(clause, m)
        for clause in CLAUSES.split(query)
        if not (
            RULES.search(clause)
            and re.search(r"检索|查阅|查找|逐字|原文|引用", clause)
            and not re.search(r"计算|核算|预测|改价|下单|发券", clause)
        )
        for m in UNSUPPORTED.finditer(clause)
    )


def objectives(query):
    goals = set()
    for clause in CLAUSES.split(query):
        if not clause.strip():
            continue
        # Removing a denied metric does not remove permitted work in this clause.
        denied = [m for m in UNSUPPORTED.finditer(clause) if negated(clause, m)]
        for match in reversed(denied):
            clause = clause[: match.start()] + clause[match.end() :]
        rule_question = bool(RULES.search(clause))
        if rule_question:
            goals.add("rules")
            # A document's subject is not a request to compute its subject.
            # Coordinated explicit calculations in the same clause remain mixed.
            parts = RULES.split(clause)
            calculations = [
                part
                for part in parts
                if re.search(
                    r"(?:核对|分析|计算|比较|核算|分解).*(?:金额|件数|分母|占比|净销售额|销量|贡献|库存|补货|缺货)",
                    part,
                )
                and (part != parts[0] or re.search(r"(?:与|和|及|以及)\s*$", part))
            ]
            if not calculations:
                continue
            clause = "；".join(calculations)
        if re.search(r"库存|补货|缺货|\bstock\b|\binventory\b", clause, re.I):
            goals.add("inventory")
        if re.search(r"退款|退货|\brefunds?\b", clause, re.I):
            goals.add("refunds")
        if re.search(
            r"贡献|排名|排行|头部|哪些商品|哪个商品|各商品|分商品|按商品|各品类|分品类|按品类|增减抵消",
            clause,
        ):
            goals.add("products")
        if re.search(r"逐日|按日|每日|哪天|日序列|日波动|日期结构|日结构|日分布", clause):
            goals.add("daily")
        if re.search(
            r"净销售|销售额|销售(?:变化|变动|走势|趋势)|经营|下降|增长|\brevenue\b|\bsales\b",
            clause,
            re.I,
        ):
            goals.add("net_revenue")
        if re.search(r"覆盖|完整|缺数|缺行|零销量|零值|停报|数据质量|记录", clause):
            goals.add("coverage")
    return sorted(goals or {"net_revenue"})
