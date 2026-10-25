"""v14 independent literal requirements; never import the production intent adapter."""

import re

from evaluations.diagnosis_oracle import raw_totals

SPLIT = re.compile(r"[，,；;。\n]|(?=但是|但|而是|只看|只按|只核对|只分析|同时看|同时核对)")
NEG = r"不要|不需要|不用|无需|不看|不(?=计算|核算|算|列出|展示|提供|比较|分析|核对)|别看|排除|剔除|不分析|不核对|不包含|不包括|不考虑|不按|除去|除了|除|勿"
METRIC_NEG = NEG.replace("除去|除了|除|", "")


def expected_entities(query, allowed, products=()):
    categories = {p["category"] for p in products if p["sku"] in allowed}
    include, exclude, yes_categories, no_categories = set(), set(), set(), set()
    for clause in SPLIT.split(query):
        mentions = []
        for sku in allowed:
            mentions += [
                (m.start(), m.end(), sku, False)
                for m in re.finditer(
                    r"(?<![A-Za-z0-9_-])" + re.escape(sku) + r"(?![A-Za-z0-9_-])", clause
                )
            ]
        for category in categories:
            context = (
                r"(?:^|核对|分析|比较|查看|只看|排除|剔除|除|不要|不看|品类|类目|分类|和|与|及|、)(?P<category>"
                + re.escape(category)
                + r")(?=$|的|品类|类目|分类|除外|以外|之外|和|与|及|、|两期|本期|前期|比较期|净销售额|支付金额|退款金额)"
            )
            for match in re.finditer(context, clause):
                start, end = match.span("category")
                typed = re.search(r"(?:品类|类目|分类)\s*$", clause[:start])
                if (
                    category
                    in {
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
                    and not typed
                ):
                    continue
                mentions.append((start, end, category, True))
        negative, end_previous = False, 0
        for start, end, identity, category in sorted(mentions):
            prefix, suffix = clause[end_previous:start], clause[end:]
            if re.search(NEG, prefix):
                negative = True
            elif re.search(r"只|保留|查看|分析|比较|核对|包括|包含|看", prefix):
                negative = False
            if re.match(r"\s*(?:除外|以外|之外|不纳入|不看|不分析|不核对)", suffix):
                negative = True
            target = (
                (no_categories if negative else yes_categories)
                if category
                else (exclude if negative else include)
            )
            target.add(identity)
            end_previous = end
    selected = (include or set(allowed)) - exclude
    if yes_categories:
        selected &= {p["sku"] for p in products if p["category"] in yes_categories}
    selected -= {p["sku"] for p in products if p["category"] in no_categories}
    return sorted(selected)


def requested_dimensions(query):
    wanted = set()
    for clause in SPLIT.split(query):
        if not re.search(
            r"贡献|排名|排行|排序|头部|抵消|最多|最少|最大|最小|哪些商品|哪个商品|哪些品类|哪个品类|定位.*(?:下降|增长)|变化来源|变动来源",
            clause,
        ):
            continue
        groups = []
        previous_weight = 0
        for match in re.finditer(r"品类|类目|商品|SKU", clause, re.I):
            if re.match(r"\s*[A-Za-z0-9_-]", clause[match.end() :]):
                continue
            prefix = clause[: match.start()]
            if re.search(rf"(?:{NEG})(?:按|分|逐|核对|比较|看|分析|\s)*$", prefix):
                continue
            weight = (
                3
                if prefix.endswith("按")
                else 2
                if re.search(r"各(?:个)?$|每(?:个)?$|逐$", prefix)
                else previous_weight
                if re.search(r"[和、与及]$", prefix)
                else 1
            )
            previous_weight = weight
            groups.append(
                (weight, "category" if match.group().lower() in {"品类", "类目"} else "sku")
            )
        if groups:
            wanted.update(dim for weight, dim in groups if weight == max(w for w, _ in groups))
        elif not re.search(rf"^\s*(?:{NEG})", clause):
            wanted.add("sku")
    return sorted(wanted)


def requested_items(query, selected, *, scope=False):
    each = False
    pattern = r"(?:逐(?:个)?|每(?:一)?个|各(?:个)?|所有|全部|分|按)\s*(?:已)?(?:授权(?:的)?)?\s*(?:商品|SKU)|分别.*商品|商品.*分别"
    for clause in SPLIT.split(query):
        for match in re.finditer(pattern, clause, re.I):
            if not re.search(
                rf"(?:{NEG})(?:按|核对|比较|看|分析|计算|显示|展示|列出|\s)*$",
                clause[: match.start()],
            ):
                each = True
    if (not each and not scope) or (each and scope):
        return []
    patterns = {
        "net_revenue": r"净销售额|净额|销售额",
        "gross_revenue": r"支付金额|支付额|付款金额",
        "refund_amount": r"退款金额|退款额",
        "paid_units": r"支付件数|支付数量|支付分母",
        "refunded_units": r"退款件数|退款数量",
    }
    pairs, previous_metrics = set(), set()
    for clause in SPLIT.split(query):
        hits = sorted(
            (m.start(), m.end(), metric)
            for metric, pattern in patterns.items()
            for m in re.finditer(pattern, clause)
        )
        if (
            not hits
            and re.search(r"变化金额|变化数值|差额|差值|增减金额", clause)
            and not re.search(r"不(?:计算|核算)|不要|无需|不用", clause)
        ):
            pairs.update(
                (metric, part)
                for metric in previous_metrics
                for part in ["current", "comparison", "delta"]
            )
        negative, previous_end, active = False, 0, None
        for start, end, metric in hits:
            prefix = clause[previous_end:start]
            if re.search(METRIC_NEG, prefix):
                negative = True
            elif re.search(r"要|需|列|看|核对|比较|分析", prefix):
                negative = False
            quote = bool(
                re.search(r"规则|文档", clause) and re.search(r"引用|逐字|原文|查阅|检索", clause)
            )
            positive = bool(
                re.search(
                    r"(?:计算|核算|算|给出|提供|输出)(?:本期|前期|两期|的|\s)*$", clause[:start]
                )
            )
            if negative or quote and not positive:
                previous_end = end
                continue
            if re.search(r"差额|差值|变化|变动|增减|下降|增长", clause):
                parts = ["current", "comparison", "delta"]
            else:
                current, prior = "本期" in prefix, bool(re.search(r"比较期|前期", prefix))
                if "两期" in prefix or current and prior:
                    active = ["current", "comparison"]
                elif current:
                    active = ["current"]
                elif prior:
                    active = ["comparison"]
                if active:
                    parts = active
                elif "两期" in clause or "本期" in clause and re.search(r"比较期|前期", clause):
                    parts = ["current", "comparison"]
                elif re.search(r"比较期|前期", clause):
                    parts = ["comparison"]
                elif "本期" in clause:
                    parts = ["current"]
                else:
                    parts = ["current", "comparison", "delta"]
            pairs.update((metric, part) for part in parts)
            previous_end = end
        if hits:
            previous_metrics = {
                metric for _, _, metric in hits if any(m == metric for m, _ in pairs)
            }
    identities = [",".join(sorted(selected))] if scope else selected
    return [(sku, metric, part) for sku in identities for metric, part in sorted(pairs)]


def excluded_metrics(query):
    result = set()
    names = {
        "net_revenue": r"净销售额|净额",
        "gross_revenue": r"支付金额|支付额|付款金额",
        "refund_amount": r"退款金额|退款额",
        "paid_units": r"支付件数|支付数量",
        "refunded_units": r"退款件数|退款数量",
    }
    for clause in SPLIT.split(query):
        for metric, pattern in names.items():
            match = re.search(pattern, clause)
            if match and re.search(
                rf"(?:{METRIC_NEG})(?:计算|算|列出|列|输出|显示|展示|提供|查看|看|分析|核对|比较|本期|前期|比较期|两期|\s)*$",
                clause[: match.start()],
            ):
                result.add(metric)
    return result


def unsupported_positive_requests(query):
    requests = set()
    patterns = {
        "profit": r"利润|\bprofit\b",
        "conversion_rate": r"转化率|\bconversion rate\b",
        "roi": r"\broi\b",
        "cohort_refund_rate": r"(?:同批|同一批|最终)(?:订单)?退款率",
        "write_operation": r"自动改价|采购下单|自动发券",
    }
    for clause in SPLIT.split(query):
        quoted = bool(
            re.search(r"规则|文档", clause) and re.search(r"引用|逐字|原文|查阅|检索", clause)
        )
        for metric, pattern in patterns.items():
            for match in re.finditer(pattern, clause, re.I):
                before, after = clause[: match.start()], clause[match.end() :]
                negative = (
                    re.search(
                        rf"(?:{METRIC_NEG})(?:计算|核算|算|给出|提供|输出|展示|本期|两期|\s)*$",
                        before,
                    )
                    or re.search(
                        r"(?:do not|don't|without|not)(?:\s+(?:calculate|compute|show|display))*\s*$",
                        before,
                        re.I,
                    )
                    or re.match(
                        r"(?:不可|不能|无法|不应)(?:直接)?(?:计算|核算|算|提供)", after.strip()
                    )
                )
                positive = re.search(
                    r"(?:计算|核算|算|给出|提供|输出|估计|预测|求)(?:本期|前期|两期|的|该|具体|数值|实际|真实|\s)*$",
                    before,
                )
                if not negative and (not quoted or positive):
                    requests.add(metric)
    return requests


def completion_checks(business, report, sources, selected):
    complete = report.get("stop_reason") == "target_covered"
    dimension_ok = all(
        any(
            p.get("tool") == "rank_contributors"
            and p.get("dimension") == dim
            and p.get("scope", {}).get("skus") == selected
            and (p.get("reconciliation") or {}).get("balanced") is True
            for p in sources
        )
        and any(
            f.get("entity_type") == dim and f.get("metric_key", "").startswith("contribution.")
            for f in report.get("facts", [])
        )
        for dim in requested_dimensions(business["query"])
    )
    missing, missing_scope = [], []
    items = requested_items(business["query"], selected)
    scope_items = requested_items(business["query"], selected, scope=True)
    for sku, metric, part in items + scope_items:
        periods = (
            ["period", "comparison_period"]
            if part == "delta"
            else ["period"]
            if part == "current"
            else ["comparison_period"]
        )
        is_scope = (sku, metric, part) in scope_items
        identities = selected if is_scope else [sku]
        if not is_scope and any(
            raw_totals(business["sales"], identities, business[p])[1]["expected_records"] == 0
            for p in periods
        ):
            continue
        raw_ok = all(
            (cov := raw_totals(business["sales"], identities, business[p])[1])["complete"]
            and cov["expected_records"] > 0
            for p in periods
        )
        if not raw_ok or not any(
            f.get("entity_id") == sku
            and f.get("metric_key") in [f"{metric}.{part}", f"component.{sku}.{metric}.{part}"]
            and f.get("unit") == ("count" if metric.endswith("units") else "CNY")
            for f in report.get("facts", [])
        ):
            (missing_scope if is_scope else missing).append((sku, metric, part))
    full, shown = report.get("open_question_details", []), report.get("open_questions", [])
    gap_ok = (
        shown == full[:20]
        and len(full) == len(set(full))
        and report.get("gap_summary")
        == {"total": len(full), "displayed": len(shown), "omitted": len(full) - len(shown)}
        and all(any("/".join(item) in q for q in full) for item in missing + missing_scope)
    )
    blocked = excluded_metrics(business["query"])
    return {
        "requested_dimension_bound": not complete or dimension_ok,
        "requested_per_entity_items": not complete or not missing,
        "requested_scope_items": not complete or not missing_scope,
        "complete_gap_inventory": bool(gap_ok),
        "unsupported_positive_requests_preserved": all(
            report.get("stop_reason") != "target_covered" and any(metric in text for text in full)
            for metric in unsupported_positive_requests(business["query"])
        ),
        "excluded_metrics_not_displayed": not any(
            blocked.intersection(f.get("metric_key", "").split("."))
            for f in report.get("facts", [])
        ),
    }
