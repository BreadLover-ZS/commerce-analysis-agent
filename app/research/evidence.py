import json
import re
from decimal import Decimal, InvalidOperation

from app.research.schemas import Claim, MetricReference, ReportSection, ResearchReport

SECTION_NAMES = ["经营表现", "库存风险", "竞品观察", "行动建议"]


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


def validate_report(report: ResearchReport, evidence: list[dict]) -> dict:
    by_id = {e["id"]: e for e in evidence}
    issues, seen = [], set()
    if [s.name for s in report.sections] != SECTION_NAMES:
        issues.append("章节缺失、重复或顺序错误")
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
                    issues.append(f"{claim.id}: 数字没有可核对的指标引用或原文依据")
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
            limitations=list(dict.fromkeys(gaps))[:20],
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
