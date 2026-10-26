import json
import re
from decimal import Decimal, InvalidOperation

from app.commerce.hypotheses import allowed_hypotheses
from app.research.schemas import Claim, MetricReference, ReportSection, ResearchReport
from app.storage.evidence import authoritative_evidence

SECTION_NAMES = ["经营表现", "库存风险", "竞品观察", "行动建议"]
REVIEW_SECTIONS = ["经营表现", "变化贡献", "退款观察", "经营解读", "行动建议"]


def unique_limitations(notes):
    """Ignore only surrounding space and sentence-final punctuation, not meaning."""
    seen, result = set(), []
    for note in notes:
        key = note.strip().rstrip("。.!！")
        if key and key not in seen:
            seen.add(key)
            result.append(note)
    return result


def numbers(text):
    text = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", text)
    return {Decimal(n) for n in re.findall(r"(?<![A-Za-z0-9])[-+]?\d+(?:\.\d+)?", text)}


def metric_reference(source, key):
    item = source["payload"]["fact_catalog"][key]
    return MetricReference(
        evidence_id=source["id"],
        key=key,
        value=item["value"],
        **{
            name: item[name]
            for name in ("entity_id", "unit", "period_start", "period_end", "snapshot_as_of")
        },
    )


def metric_binding_issues(metric, source):
    if not source:
        return ["指标没有对应来源"]
    if source.get("kind") != "metrics":
        return ["指标引用不是受限业务指标来源"]
    catalog = source.get("payload", {}).get("fact_catalog", {})
    item = catalog.get(metric.key)
    if not item:
        return ["指标缺少事实维度目录；旧证据需要重新生成"]
    issues = []
    for name in ("entity_id", "unit", "period_start", "period_end", "snapshot_as_of"):
        if getattr(metric, name) != item[name]:
            issues.append(f"指标 {metric.key} 的 {name} 与来源不一致")
    try:
        expected = source["payload"]["metrics"][metric.key]
        if (
            not Decimal(metric.value).is_finite()
            or Decimal(metric.value) != Decimal(str(expected))
            or Decimal(metric.value) != Decimal(item["value"])
        ):
            issues.append(f"指标 {metric.key} 与确定性计算结果不一致")
    except (InvalidOperation, KeyError):
        issues.append("指标值不是有效数字")
    return issues


def render_metric_claim(refs, by_id):
    parts = []
    units = {"CNY": "元", "count": "件", "percent": "%", "days": "天"}
    for ref in refs:
        item = by_id[ref.evidence_id]["payload"]["fact_catalog"][ref.key]
        period = f"销量周期 {item['period_start']} 至 {item['period_end']}"
        if item["snapshot_as_of"]:
            period += f"；库存快照 {item['snapshot_as_of']}"
        entity = item["entity_name"] + (f"（{item['entity_id']}）" if "." in ref.key else "")
        parts.append(
            f"{entity}：{item['label']}为 {item['value']} {units[item['unit']]}（{period}）。"
        )
        if ref.key == "net_revenue":
            parts.append("该指标未扣除经营成本，不能视为净利润。")
    return "".join(parts)


def render_bound_metrics(report, evidence):
    """Render only valid metric tuples; invalid references remain visible to validation."""
    evidence, _ = authoritative_evidence(evidence)
    by_id = {e["id"]: e for e in evidence}
    for section in report.sections:
        for claim in section.claims:
            if (
                claim.kind == "fact"
                and claim.metrics
                and all(
                    ref.evidence_id in claim.evidence_ids
                    and not metric_binding_issues(ref, by_id.get(ref.evidence_id))
                    for ref in claim.metrics
                )
            ):
                claim.text = render_metric_claim(claim.metrics, by_id)
    return report


def review_direction_issues(text, payload):
    """Targeted guards for observed amount/rate confusion, not a general semantic proof."""
    issues = []
    for subject, key in (
        ("退款金额", "refund_amount_delta"),
        ("退款件数", "refunded_units_delta"),
        ("支付金额", "gross_revenue_delta"),
        ("净销售额", "net_revenue_delta"),
    ):
        for pattern, direction in ((r"(?:上升|增加|增长)", 1), (r"(?:下降|减少|下滑)", -1)):
            if re.search(
                subject + r"(?:的|较前期|相比前期|有所|出现|明显|显著|大幅|小幅)*" + pattern, text
            ):
                delta = payload["metrics"].get(key)
                if delta is None or Decimal(delta) * direction <= 0:
                    issues.append(f"{subject}的方向断言与确定性变化金额/件数不符或缺少可比数据")
    if re.search(r"退款(?:率|占比|件数占比).{0,12}(?:偏高|过高|较高|很高|偏低|过低|较低)", text):
        issues.append("未配置业务阈值，不能将退款占比判断为绝对高低")
    return issues


def validate_report(report: ResearchReport, evidence: list[dict], *, model_stopped=False) -> dict:
    evidence, _ = authoritative_evidence(evidence)
    by_id = {e["id"]: e for e in evidence}
    issues, seen = [], set()
    is_review = any(e["payload"].get("review_profile") == "commerce_review" for e in evidence)
    if [s.name for s in report.sections] != (REVIEW_SECTIONS if is_review else SECTION_NAMES):
        issues.append("章节缺失、重复或顺序错误")
    if is_review:
        expected = commerce_review_report(evidence, [])
        if any(e["payload"].get("answer_version") in {5, 6} for e in evidence):
            from app.commerce.review_report_v5 import explanation_completion

            checked = explanation_completion(report.model_copy(deep=True), evidence)
            expected_claim = next(
                c for s in expected.sections for c in s.claims if c.id == "review-answer-completion"
            )
            expected_claim.text = next(
                c.text for s in checked.sections for c in s.claims if c.id == expected_claim.id
            )
        if model_stopped and any(e["payload"].get("answer_version") in {5, 6} for e in evidence):
            from app.commerce.review_report_v5 import stopped_report

            expected = stopped_report(expected)
        for actual, required in zip(report.sections[:3], expected.sections[:3]):
            if actual.model_dump() != required.model_dump():
                issues.append("经营复盘的确定性事实章节被修改或遗漏")
        for section in report.sections[3:]:
            kind = "analysis" if section.name == "经营解读" else "recommendation"
            if any(c.kind != kind or not c.evidence_ids for c in section.claims):
                issues.append("经营解读与建议必须引用证据且保持声明类型")
        payload = next(
            e["payload"]
            for e in evidence
            if e["payload"].get("review_profile") == "commerce_review"
        )
        if len(report.sections) == 5:
            baseline = expected.sections[3].claims
            actual = report.sections[3].claims
            if [c.model_dump() for c in actual[: len(baseline)]] != [
                c.model_dump() for c in baseline
            ]:
                issues.append("经营解读中的确定性观察被修改或遗漏")
            options = allowed_hypotheses(payload)
            if payload.get("answer_version") == 6:
                from app.commerce.explanation_contract_v6 import allowed as scoped_options

                options = scoped_options(payload, options)
            allowed = set(options.values())
            if any(c.text not in allowed for c in actual[len(baseline) :]):
                issues.append("经营解释超出当前证据允许的待验证假设目录")
        if payload.get("answer_version") in {3, 4, 5, 6}:
            from app.commerce.review_actions import ACTIONS

            if any(
                c.text not in {v[1] for v in ACTIONS.values()} for c in report.sections[4].claims
            ):
                issues.append("建议动作或事实前提不在已核验目录")
            displayed = {m.key for s in report.sections for c in s.claims for m in c.metrics}
            if (
                "comparison_net_revenue" in payload["fact_catalog"]
                and "comparison_net_revenue" not in displayed
            ):
                issues.append("比较期净销售额未直接展示")
            dimension = payload["answer_contract"]["dimension"]
            entries = payload["dimensions"][dimension]
            # Check the source catalogue independently of the rendering template.
            required = {
                key
                for key in payload["fact_catalog"]
                if any(
                    key.startswith(f"{entry['fact_key'].rsplit('.', 1)[0]}.{metric}.")
                    for entry in entries[:4]
                    for metric in ("gross_revenue", "refund_amount", "net_revenue")
                )
            }
            if required - displayed:
                issues.append("逐对象支付、退款或净额必答项未直接展示")
            if len(entries) > 4 and not any(
                "不能视为已完整展示" in note and all(e["entity"] in note for e in entries[4:])
                for note in report.limitations
            ):
                issues.append("展示容量之外的对象未明确列为未展示")
        for section in report.sections[3:]:
            for claim in section.claims:
                issues.extend(
                    f"{claim.id}: {issue}" for issue in review_direction_issues(claim.text, payload)
                )
    for section in report.sections:
        if not section.claims and not any(section.name in note for note in report.limitations):
            issues.append(f"{section.name}: 空章节没有具体说明范围或信息缺口")
        for claim in section.claims:
            if claim.id in seen:
                issues.append(f"{claim.id}: 结论ID重复")
            seen.add(claim.id)
            if claim.kind in {"fact", "analysis"} and not claim.evidence_ids:
                issues.append(f"{claim.id}: 事实或分析没有证据")
            if any(e not in by_id for e in claim.evidence_ids):
                issues.append(f"{claim.id}: 引用了未知证据")
            binding_ok = True
            for metric in claim.metrics:
                failures = metric_binding_issues(metric, by_id.get(metric.evidence_id))
                if metric.evidence_id not in claim.evidence_ids:
                    failures.append("指标没有对应的已引用来源")
                issues.extend(f"{claim.id}: {failure}" for failure in failures)
                binding_ok = binding_ok and not failures
            if claim.metrics:
                if claim.kind != "fact":
                    issues.append(f"{claim.id}: 指标必须独立形成程序渲染的事实句")
                if binding_ok and claim.text != render_metric_claim(claim.metrics, by_id):
                    issues.append(f"{claim.id}: 指标事实句与实体、单位、周期或权威渲染不一致")
            else:
                known = set()
                for eid in claim.evidence_ids:
                    source = by_id.get(eid)
                    if source and source["kind"] in {"document", "web", "fixture"}:
                        known |= numbers(source["excerpt"])
                    if source:
                        for item in source["payload"].get("items", []):
                            known |= numbers(item.get("name", ""))
                if not numbers(claim.text).issubset(known):
                    issues.append(
                        f"{claim.id}: 数字没有可核对的指标引用或原文依据。"
                        "经营来源的数值（包括配置提前期、算法阈值）须从fact_catalog复制完整metrics引用，"
                        "由程序渲染独立fact；关系判断另列analysis。"
                        "仅调整空格或将数字改成中文不能补足依据；确无来源则删除该数值并说明缺口。"
                    )
            if (
                any(by_id.get(eid, {}).get("kind") == "fixture" for eid in claim.evidence_ids)
                and "模拟" not in claim.text
            ):
                issues.append(f"{claim.id}: 模拟竞品证据未标注")
    return {
        "passed": not issues,
        "issues": issues,
        "semantic_status": "not_evaluated",
        "fact_binding_version": 2,
        "note": "实体、单位、周期与数字绑定检查不等于语义真实性保证；重要结论仍需人工核对",
    }


def deterministic_report(evidence: list[dict], gaps: list[str]) -> ResearchReport:
    evidence, _ = authoritative_evidence(evidence)
    if any(e["payload"].get("review_profile") == "commerce_review" for e in evidence):
        return commerce_review_report(evidence, gaps)
    sections = {name: [] for name in SECTION_NAMES}
    for source in evidence:
        payload, eid = source["payload"], source["id"]
        if payload.get("tool") == "sales":
            metrics = payload["metrics"]
            sections["经营表现"].append(
                Claim(
                    id="sales-net",
                    kind="fact",
                    text=f"本周期净销售额为 {metrics['net_revenue']} 元；该指标未扣除经营成本，不能视为净利润。",
                    evidence_ids=[eid],
                    metrics=[metric_reference(source, "net_revenue")],
                )
            )
            if "refund_rate_pct" in metrics:
                sections["经营表现"].append(
                    Claim(
                        id="sales-refund",
                        kind="fact",
                        text=f"按记账周期计算的退款件数占支付件数比例为 {metrics['refund_rate_pct']}%。",
                        evidence_ids=[eid],
                        metrics=[metric_reference(source, "refund_rate_pct")],
                    )
                )
            if "net_revenue_change_pct" in metrics:
                sections["经营表现"].append(
                    Claim(
                        id="sales-change",
                        kind="fact",
                        text="前期比较",
                        evidence_ids=[eid],
                        metrics=[metric_reference(source, "net_revenue_change_pct")],
                    )
                )
            sections["经营表现"].append(
                Claim(
                    id="sales-units",
                    kind="fact",
                    text=f"本周期支付 {metrics['paid_units']} 件、退款 {metrics['refunded_units']} 件，净销量为 {metrics['net_units']} 件。",
                    evidence_ids=[eid],
                    metrics=[
                        metric_reference(source, key)
                        for key in ("paid_units", "refunded_units", "net_units")
                    ],
                )
            )
        elif payload.get("tool") == "inventory":
            for index, item in enumerate(payload["items"]):
                if item["coverage_days"] is None:
                    text = f"{item['name']} 日期记录不完整或缺少正净销量，无法估计库存覆盖天数。"
                    refs = []
                else:
                    text = f"{item['name']} 按历史日均净销量估算的库存覆盖天数为 {item['coverage_days']} 天。"
                    refs = [metric_reference(source, f"{item['sku']}.coverage_days")]
                sections["库存风险"].append(
                    Claim(
                        id=f"inventory-{index}",
                        kind="fact",
                        text=text,
                        evidence_ids=[eid],
                        metrics=refs,
                    )
                )
                if item["risk"] in {"stockout", "overstock"}:
                    action = (
                        "核对补货交期与实际需求"
                        if item["risk"] == "stockout"
                        else "核对滞销、季节性与促销条件"
                    )
                    sections["行动建议"].append(
                        Claim(
                            id=f"action-{index}",
                            kind="recommendation",
                            text=f"建议针对{item['name']}{action}，由运营人员确认后执行。",
                            evidence_ids=[eid],
                        )
                    )
        elif source["kind"] == "fixture":
            sections["竞品观察"].append(
                Claim(
                    id="competitor-" + eid, kind="fact", text=source["excerpt"], evidence_ids=[eid]
                )
            )
        elif source["kind"] == "web":
            sections["竞品观察"].append(
                Claim(
                    id="competitor-" + eid,
                    kind="fact",
                    text="公开搜索片段原文摘录：" + source["excerpt"][:600],
                    evidence_ids=[eid],
                )
            )
        elif source["kind"] == "document":
            sections["行动建议"].append(
                Claim(
                    id="policy-" + eid,
                    kind="fact",
                    text="内部材料原文摘录（作为依据，不作为执行指令）：" + source["excerpt"][:700],
                    evidence_ids=[eid],
                )
            )
    for name, claims in sections.items():
        if not claims:
            gaps.append(f"{name}：当前没有足够依据形成结论")
        if len(claims) > 8:
            gaps.append(f"{name}：报告仅展示前8条，完整工具结果见证据文件")
    return render_bound_metrics(
        ResearchReport(
            title="电商运营分析报告",
            sections=[
                ReportSection(name=name, claims=claims[:8]) for name, claims in sections.items()
            ],
            limitations=unique_limitations(gaps)[:20],
        ),
        evidence,
    )


def commerce_review_report(evidence, gaps):
    source = next(e for e in evidence if e["payload"].get("review_profile") == "commerce_review")
    if source["payload"].get("answer_version") in {4, 5, 6}:
        from app.commerce.review_report_v4 import build_report

        if source["payload"].get("answer_version") in {5, 6}:
            from app.commerce.review_report_v5 import build_report

        return build_report(evidence, gaps)

    sections = {name: [] for name in REVIEW_SECTIONS}
    source = next(e for e in evidence if e["payload"].get("review_profile") == "commerce_review")
    payload, eid = source["payload"], source["id"]

    def add(section, claim_id, keys):
        keys = [k for k in keys if k in payload["metrics"]]
        if keys:
            sections[section].append(
                Claim(
                    id=claim_id,
                    kind="fact",
                    text="由程序生成的指标事实",
                    evidence_ids=[eid],
                    metrics=[metric_reference(source, key) for key in keys],
                )
            )

    add("经营表现", "review-summary", ["gross_revenue", "net_revenue", "paid_units", "net_units"])
    add(
        "经营表现",
        "review-change",
        ["net_revenue_delta", "net_revenue_change_pct", "gross_revenue_delta"],
    )
    for kind, entries in payload["dimensions"].items():
        for index, entry in enumerate(entries[:4]):
            if not entry["delta_key"]:
                continue
            add("变化贡献", f"review-{kind}-{index}", [entry["delta_key"] or entry["fact_key"]])
    add(
        "退款观察",
        "review-refund",
        [
            "refund_amount",
            "refunded_units",
            "refund_rate_pct",
            "refund_amount_delta",
            "refunded_units_delta",
        ],
    )
    if payload.get("answer_version") in {2, 3}:
        add(
            "退款观察",
            "review-denominator-comparison",
            [
                "paid_units",
                "comparison_paid_units",
                "paid_units_delta",
                "refunded_units",
                "comparison_refunded_units",
            ],
        )
        add(
            "退款观察",
            "review-refund-comparison",
            [
                "refund_amount",
                "comparison_refund_amount",
                "refund_rate_pct",
                "comparison_refund_rate_pct",
            ],
        )
    if payload.get("answer_version") == 3:
        add("经营表现", "review-comparison-net", ["comparison_net_revenue"])
        sections["变化贡献"] = []
        dimension = payload["answer_contract"]["dimension"]
        entries = payload["dimensions"][dimension]
        for index, entry in enumerate(entries[:4]):
            keys = list(entry.get("component_fact_keys", {}).values())
            for part in range(0, len(keys), 8):
                add("变化贡献", f"review-components-{index}-{part}", keys[part : part + 8])
        if len(entries) > 4:
            gaps = [
                *gaps,
                "变化贡献：报告仅展示前四个对象，未展示对象："
                + "、".join(e["entity"] for e in entries[4:])
                + "；完整逐项事实在证据文件，不能视为已完整展示。",
            ]
    for index, finding in enumerate(payload["findings"]):
        sections["经营解读"].append(
            Claim(
                id=f"review-finding-{index}",
                kind="analysis",
                text=finding["text"],
                evidence_ids=[eid],
            )
        )
    if not sections["经营解读"]:
        sections["经营解读"].append(
            Claim(
                id="review-neutral",
                kind="analysis",
                evidence_ids=[eid],
                text=(
                    "本期与前期资料不足以比较，应先补齐记录再判断变化。"
                    if not payload["previous_period"]["comparable"]
                    else "现有规则未识别可确认的下降或退款占比增加；这不代表经营健康或没有其他风险。"
                ),
            )
        )
    if payload.get("answer_version") in {2, 3}:
        sections["经营解读"].append(
            Claim(
                id="review-direct-answers",
                kind="analysis",
                evidence_ids=[eid],
                text="".join(a["text"] for a in payload["direct_answers"]),
            )
        )
    if payload.get("answer_version") == 3:
        from app.commerce.review_actions import ACTIONS

        sections["行动建议"] = [
            Claim(
                id="review-action",
                kind="recommendation",
                evidence_ids=[eid],
                text=ACTIONS["verify_records"][1],
            )
        ]
    else:
        sections["行动建议"].append(
            Claim(
                id="review-action",
                kind="recommendation",
                evidence_ids=[eid],
                text="建议先核对数据完整性与退款记账归属，再结合商品变化贡献补充原因资料，由运营人员确认后执行。",
            )
        )
    return render_bound_metrics(
        ResearchReport(
            title="经营复盘报告",
            sections=[ReportSection(name=name, claims=claims) for name, claims in sections.items()],
            limitations=unique_limitations(gaps + payload["limitations"])[:20],
        ),
        evidence,
    )


def render_markdown(
    task: dict, report: ResearchReport, evidence: list[dict], validation: dict
) -> str:
    params = task["parameters"]
    mode = (
        "离线演示：未调用模型或真实搜索服务"
        if task["mode"] == "demo"
        else "真实模型模式：业务数据性质以各证据标记为准"
    )
    lines = [
        f"# {report.title}",
        "",
        f"> {mode}",
        "",
        f"统计周期：{params['start_date']} 至 {params['end_date']}；时区 Asia/Shanghai；币种 CNY。",
        "库存使用证据中标注日期的快照。"
        + (
            "报告包含模拟资料，不代表真实公司的结果。"
            if any(e["payload"].get("sample") for e in evidence)
            else "数据性质以证据目录为准，重要结论需要人工核对。"
        ),
        "",
    ]
    for source in evidence:
        if source["payload"].get("review_profile") == "commerce_review":
            lines += [
                f"数据版本：{source['payload']['dataset_id']}；内容SHA-256：{source['payload']['dataset_hash']}。",
                "",
            ]
            if source["payload"].get("bundle_id"):
                payload = source["payload"]
                prior = payload["previous_period"]
                lines += [
                    f"固定输入：{payload['bundle_id']}；组合SHA-256：{payload['bundle_hash']}。",
                    f"对比期：{prior['start']} 至 {prior['end']}；授权SKU：{','.join(payload['scope']['skus'])}。",
                    "",
                ]
    for section in report.sections:
        lines += ["## " + section.name, ""]
        if not section.claims:
            lines += ["该部分证据不足，无法形成可靠结论。", ""]
        for claim in section.claims:
            label = {"fact": "事实", "analysis": "分析推断", "recommendation": "行动建议"}[
                claim.kind
            ]
            citations = " ".join(f"[{eid}]" for eid in claim.evidence_ids)
            lines += [f"- **{label}**：{claim.text.replace(chr(10), ' ')} {citations}"]
        lines += [""]
    lines += ["## 数据口径与信息缺口", ""]
    for source in evidence:
        if source["payload"].get("tool") == "inventory":
            dates = sorted({item["stock_as_of"] for item in source["payload"].get("items", [])})
            lines += ["- 库存快照日期：" + "、".join(dates)]
        for name, definition in source["payload"].get("definitions", {}).items():
            lines += [f"- {name}：{definition}"]
    lines += [f"- {gap}" for gap in report.limitations]
    lines += [
        "",
        "## 校验说明",
        "",
        validation["note"],
        f"结构检查：{'通过' if validation['passed'] else '未通过'}；语义检查：{validation['semantic_status']}。",
        "",
        "## 证据目录",
        "",
    ]
    for source in evidence:
        location = source["locator"]
        label = "模拟资料" if source["payload"].get("sample") else "非模拟标记"
        lines += [
            f"- [{source['id']}] {source['title']}（{label}）；定位：{location}；获取时间：{source['retrieved_at']}；SHA-256：{source['content_hash']}"
        ]
    return "\n".join(lines) + "\n"


def bounded_evidence_json(evidence):
    from app.research.context import evidence_package

    return json.dumps(evidence_package(evidence)["sources"], ensure_ascii=False)
