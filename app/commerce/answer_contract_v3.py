"""Bounded, clause-scoped v14 intent; authorization always comes from the bundle."""

import re

from app.commerce.answer_contract import METRICS, UNSUPPORTED
from app.commerce.answer_contract import request_contract as old_contract
from app.commerce.answer_contract_v2 import COMPONENT_METRICS, PER_ENTITY

CLAUSES = re.compile(r"[，,；;。\n]|(?=但(?:是)?|而是|只看|只按|只核对|只分析|同时看|同时核对)")
NEGATIVE = r"不要|无需|不用|不需要|不得|不看|不(?=计算|核算|算|列出|展示|提供|比较|分析|核对)|别看|(?<!分)别|排除|剔除|除去|除了|除|不分析|不核对|不包括|不包含|不考虑|不按|勿"
FILLER = r"本期|前期|比较期|两期|再|计算|核算|算|列出|列|输出|显示|展示|提供|查看|看|分析|核对|比较|按|的|商品|SKU|品类|类目|授权|已授权|已|各|个|\s"
EXPLORATION = re.compile(
    r"为什么|为何|原因|解释|分析|诊断|来源|驱动|影响|归因|贡献|排名|排行|排序|抵消|逐日|每日|日序列|库存|规则|占比|分母|边界|取消|终止|退款(?!金额|件数|数量|额)"
)
ATTRIBUTION = re.compile(
    r"贡献|排名|排行|排序|头部|抵消|最多|最少|最大|最小|哪些商品|哪个商品|哪些品类|哪个品类|定位.*(?:下降|增长)|变化来源|变动来源",
    re.I,
)
DIMENSIONS = re.compile(r"品类|类目|商品|SKU", re.I)
PER_CATEGORY = re.compile(
    r"(?:逐|每(?:一)?个|各(?:个)?|所有|全部|分)\s*(?:授权(?:的)?)?\s*(?:品类|类目)"
)


def clauses(query):
    return [c.strip() for c in CLAUSES.split(query) if c.strip()]


def polarity(before, after="", identities=(), *, entity=False):
    if re.search(r"(?:不要|不能|并非|不是|不得)\s*(?:不|别|排除)", before):
        raise ValueError("包含不可靠双重否定，请明确需要核对的对象或指标")
    if re.match(r"\s*(?:除外|以外|之外|不纳入|不看|不分析|不核对)", after):
        return "excluded"
    for identity in sorted(identities, key=len, reverse=True):
        before = re.sub(
            r"(?<![A-Za-z0-9_-])" + re.escape(identity) + r"(?![A-Za-z0-9_-])", "", before
        )
    before = re.sub(
        r"(?:[、\s]|以及|和|与|及|或|商品|SKU|品类|类目|授权|已)+$", "", before, flags=re.I
    )
    if re.search(
        r"(?:do not|don\'t|without|not)(?:\s+(?:calculate|compute|show|display|include|compare|analy[sz]e|inspect))*\s*$",
        before,
        re.I,
    ):
        return "excluded"
    negative = NEGATIVE if entity else NEGATIVE.replace("除去|除了|除|", "")
    return "excluded" if re.search(rf"(?:{negative})(?>{FILLER})*$", before, re.I) else "requested"


def entity_selection(query, allowed, products=()):
    allowed = sorted(allowed)
    catalog = {p["sku"]: p["category"] for p in products if p["sku"] in allowed}
    categories = sorted(set(catalog.values()))
    included, excluded, included_categories, excluded_categories = set(), set(), set(), set()
    for clause in clauses(query):
        matches = []
        for identity in allowed:
            matches.extend(
                (m.start(), m.end(), "sku", identity)
                for m in re.finditer(
                    r"(?<![A-Za-z0-9_-])" + re.escape(identity) + r"(?![A-Za-z0-9_-])", clause
                )
            )
        for category in categories:
            for m in re.finditer(re.escape(category), clause):
                before, after = clause[: m.start()], clause[m.end() :]
                typed = bool(re.search(r"(?:品类|类目|分类)\s*$", before))
                reserved = category in {
                    "本期",
                    "前期",
                    "比较期",
                    "两期",
                    "商品",
                    "SKU",
                    "品类",
                    "类目",
                    "净销售额",
                    "支付金额",
                    "退款金额",
                    "库存",
                    "规则",
                }
                left = not before.strip() or re.search(
                    r"(?:核对|分析|比较|查看|只看|排除|剔除|除了|除|不看|不要|和|与|及|、)\s*$",
                    before,
                )
                right = not after.strip() or re.match(
                    r"(?:的|品类|类目|分类|除外|以外|之外|和|与|及|、|两期|本期|前期|比较期|净销售额|支付金额|退款金额)",
                    after,
                )
                if typed or not reserved and left and right:
                    matches.append((m.start(), m.end(), "category", category))
        # An explicit type can be adjacent to a valid identifier such as SKU01.
        # Skip a type match that is itself part of an already-recognized ID.
        for match in re.finditer(
            r"(?:商品|SKU|品类|类目)\s*([A-Za-z0-9_-]+(?:\s*(?:、|和|与|及)\s*(?:(?:商品|SKU)\s*)?[A-Za-z0-9_-]+)*)",
            clause,
            re.I,
        ):
            for token in re.finditer(r"[A-Za-z0-9_-]+", match.group(1)):
                start, end = match.start(1) + token.start(), match.start(1) + token.end()
                if not any(a <= start and end <= b for a, b, _, _ in matches):
                    raise ValueError("请求包含不存在或未授权的明确对象，请按授权目录重述")
        for start, end, kind, identity in matches:
            state = polarity(clause[:start], clause[end:], [*allowed, *categories], entity=True)
            target = (
                (excluded if state == "excluded" else included)
                if kind == "sku"
                else (excluded_categories if state == "excluded" else included_categories)
            )
            target.add(identity)
    if included & excluded or included_categories & excluded_categories:
        raise ValueError("对象包含与排除条件冲突，请明确需要核对的范围")
    selected = set(included or allowed)
    if included_categories:
        selected &= {sku for sku, category in catalog.items() if category in included_categories}
    selected -= excluded | {
        sku for sku, category in catalog.items() if category in excluded_categories
    }
    if not selected:
        raise ValueError("请求没有剩余授权商品，请明确需要核对的对象")
    return {
        "included": sorted(included),
        "excluded": sorted(excluded),
        "included_categories": sorted(included_categories),
        "excluded_categories": sorted(excluded_categories),
        "selected": sorted(selected),
    }


def period_parts(text):
    if re.search(r"差额|差值|变化|变动|增减|增长|下降", text):
        return ["current", "comparison", "delta"]
    current, prior = "本期" in text, bool(re.search(r"比较期|前期", text))
    if "两期" in text or current and prior:
        return ["current", "comparison"]
    return ["current"] if current else ["comparison"] if prior else None


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
    items, positive, metric_periods, excluded_metrics = [], [], {}, set()
    dimensions, denied_dimensions = set(), set()
    previous_metrics = set()
    for clause in clauses(normalized):
        spans = sorted(
            (m.start(), m.end(), metric)
            for metric, pattern in METRICS.items()
            for m in re.finditer(pattern, clause, re.I)
        )
        specific = [(a, b) for a, b, metric in spans if metric in UNSUPPORTED]
        spans = [
            (a, b, metric)
            for a, b, metric in spans
            if metric in UNSUPPORTED or not any(c <= a and b <= d for c, d in specific)
        ]
        if (
            not spans
            and re.search(r"变化金额|变化数值|差额|差值|增减金额", clause)
            and not re.search(r"不(?:计算|核算)|不要|无需|不用", clause)
        ):
            for metric in previous_metrics:
                metric_periods.setdefault(metric, set()).update(["current", "comparison", "delta"])
        active_parts, previous_end = None, 0
        clean = clause
        for start, end, metric in spans:
            state = polarity(clause[:start], clause[end:], [clause[a:b] for a, b, _ in spans])
            if re.match(
                r"\s*(?:不可|不能|无法|不应)(?:直接)?(?:计算|核算|算|推断|提供)", clause[end:]
            ):
                state = "excluded"
            literal_rule = bool(
                re.search(r"规则|文档", clause) and re.search(r"引用|逐字|原文|检索|查阅", clause)
            )
            explicit_calculation = bool(
                re.search(
                    r"(?:计算|核算|算|给出|提供|输出|估计|预测|求)(?:本期|前期|两期|的|该|具体|数值|实际|真实|\s)*$",
                    clause[:start],
                )
            )
            if literal_rule and state == "requested" and not explicit_calculation:
                state = "mentioned"
            items.append({"metric": metric, "polarity": state, "clause": clause})
            if state != "requested":
                if state == "excluded":
                    excluded_metrics.add(metric)
                continue
            explicit = period_parts(clause[previous_end:start])
            active_parts = explicit or active_parts
            global_parts = period_parts(clause)
            parts = (
                global_parts
                if global_parts and "delta" in global_parts
                else active_parts or global_parts or ["current", "comparison", "delta"]
            )
            metric_periods.setdefault(metric, set()).update(parts)
            previous_end = end
        if spans:
            previous_metrics = {
                i["metric"] for i in items if i["clause"] == clause and i["polarity"] == "requested"
            }
        # Contribution dimensions have their own clause polarity; a negated
        # category must not become the positive request's dimension.
        attributed = bool(ATTRIBUTION.search(clause))
        group_matches = list(DIMENSIONS.finditer(clause))
        positive_groups = []
        last_priority = 0
        for match in group_matches:
            if re.match(r"[A-Za-z0-9_-]", clause[match.end() :].lstrip()):
                continue
            dim = "category" if match.group().lower() in {"品类", "类目"} else "sku"
            state = polarity(clause[: match.start()], clause[match.end() :])
            if state == "excluded":
                if (
                    attributed
                    or not clause[match.end() :].strip()
                    or re.search(r"按$", clause[: match.start()])
                ):
                    denied_dimensions.add(dim)
            else:
                prefix = clause[: match.start()]
                priority = (
                    3
                    if re.search(r"按$", prefix)
                    else 2
                    if re.search(r"各(?:个)?$|每(?:个)?$|逐$", prefix)
                    else last_priority
                    if re.search(r"[和、与及]$", prefix)
                    else 1
                )
                last_priority = priority
                positive_groups.append((priority, dim))
        if attributed:
            if positive_groups:
                best = max(priority for priority, dim in positive_groups)
                dimensions.update(dim for priority, dim in positive_groups if priority == best)
            elif not group_matches and polarity(clause) == "requested":
                dimensions.add("sku")
        if group_matches and not positive_groups and re.match(rf"^(?:{NEGATIVE})", clause):
            clean = ""
        for start, end, metric in reversed(spans):
            if metric in excluded_metrics or metric in UNSUPPORTED:
                clean = clean[:start] + clean[end:]
        if clean:
            positive.append(clean)
    requested = {i["metric"] for i in items if i["polarity"] == "requested"}
    if requested & excluded_metrics:
        raise ValueError("指标包含与排除条件冲突，请明确需要核对的指标")
    # Both dimensions are supported, but contradictory instructions are not.
    if dimensions & denied_dimensions:
        raise ValueError("贡献维度包含与排除条件冲突，请明确需要的分组")
    if PER_CATEGORY.search(normalized) and requested & set(COMPONENT_METRICS):
        category_delta = (
            dimensions == {"category"}
            and requested - UNSUPPORTED <= {"net_revenue"}
            and not re.search(
                r"本期值|比较期值|前期值|(?:本期|前期|比较期)(?:的)?净销售额", normalized
            )
        )
        if not category_delta:
            raise ValueError("逐品类金额或件数明细尚无完成契约，请指定商品明细或品类净额贡献")
    canonical = "；".join(positive)
    c = old_contract(canonical)
    unsupported = sorted(requested & UNSUPPORTED)
    supported = sorted(requested - UNSUPPORTED)
    each = bool(PER_ENTITY.search(canonical) or re.search(r"分别.*商品|商品.*分别", canonical))
    parts_order = ["current", "comparison", "delta"]
    c.update(
        version="answer-contract-v3",
        items=items,
        canonical_query=canonical,
        requested_metrics=supported,
        excluded_metrics=sorted(excluded_metrics),
        unsupported_requests=unsupported,
        component_metrics=[m for m in COMPONENT_METRICS if m in supported] if each else [],
        metric_periods={
            m: [p for p in parts_order if p in values]
            for m, values in sorted(metric_periods.items())
        },
        periods=[p for p in parts_order if any(p in v for v in metric_periods.values())]
        or parts_order,
        per_entity=each,
        contribution_dimensions=sorted(dimensions),
        excluded_dimensions=sorted(denied_dimensions),
        contribution_dimension=next(iter(dimensions)) if len(dimensions) == 1 else None,
        matrix_literal=bool(supported and not EXPLORATION.search(canonical)),
    )
    if (
        unsupported
        and not supported
        and not re.search(r"规则|文档|库存|贡献|逐日|日序列", canonical)
    ):
        c["goals"] = []
    return c


def bound_request(query, bundle, products):
    catalog = sorted(
        (
            {"sku": p["sku"], "category": p["category"]}
            for p in products
            if p["sku"] in bundle["allowed_skus"]
        ),
        key=lambda p: p["sku"],
    )
    if {p["sku"] for p in catalog} != set(bundle["allowed_skus"]):
        raise ValueError("授权目录与已验证销售源不一致")
    return {
        **request_contract(query),
        "entity_scope": entity_selection(query, bundle["allowed_skus"], catalog),
        "entity_catalog": catalog,
        "sales_content_hash": bundle["sales_content_hash"],
    }


def initial_scope(query, bundle, products):
    return {
        "skus": entity_selection(query, bundle["allowed_skus"], products)["selected"],
        "objective": None,
    }


def unsupported_gaps(query):
    return [
        f"请求指标 {metric}：当前固定数据或只读工具不支持；保留其他可回答项"
        for metric in request_contract(query)["unsupported_requests"]
    ]
