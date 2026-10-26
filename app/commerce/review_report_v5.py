"""Atomic, capacity-aware fixed report composition. Legacy report contracts stay frozen."""

import copy
import re

from app.commerce.hypotheses import allowed_hypotheses
from app.commerce.review_actions import render_actions
from app.diagnosis.inputs import digest
from app.research.schemas import Claim, ResearchReport, ReviewNarrativeV3

LABELS = {
    "sales_drivers": "支付金额变化",
    "refund_drivers": "退款变化",
    "product_mix": "商品组合",
    "daily_structure": "逐日结构",
    "data_quality": "数据质量",
}


def required_explanations(payload):
    if payload.get("answer_version") == 6:
        from app.commerce.explanation_contract_v6 import contract

        return set(contract(payload.get("objective", ""))["required"])
    query = payload.get("objective", "")
    if not re.search(r"逐一|分别|每类|各类|每一类", query) or not re.search(
        r"解释|假设|讨论|原因", query
    ):
        return set()
    terms = {
        "sales_drivers": r"支付|销量|销售原因",
        "refund_drivers": r"退款",
        "product_mix": r"商品组合|商品结构",
        "daily_structure": r"逐日|每日",
        "data_quality": r"数据质量|数据完整|记录完整",
    }
    return {code for code, pattern in terms.items() if re.search(pattern, query)}


def explanation_completion(report, evidence):
    source = next(e for e in evidence if e["payload"].get("review_profile") == "commerce_review")
    required = required_explanations(source["payload"])
    options = allowed_hypotheses(source["payload"])
    shown = {
        code
        for code, text in options.items()
        if any(c.text == text for s in report.sections for c in s.claims)
    }
    claim = next(c for s in report.sections for c in s.claims if c.id == "review-answer-completion")
    if required - shown:
        claim.text = "答复完成度：请求尚未答齐（指定解释方向尚未完整展示）。"
    else:
        from app.commerce.review_report_v4 import build_report as v4_report

        frozen = copy.deepcopy(evidence)
        next(e for e in frozen if e["id"] == source["id"])["payload"]["answer_version"] = 4
        original = next(
            c for s in v4_report(frozen, []).sections for c in s.claims if c.id == claim.id
        )
        claim.text = original.text
    return report


def build_report(evidence, gaps):
    from app.commerce.review_report_v4 import build_report as v4_report

    frozen = copy.deepcopy(evidence)
    next(e for e in frozen if e["payload"].get("review_profile") == "commerce_review")["payload"][
        "answer_version"
    ] = 4
    # Revalidate plain data, not an already validated (but mutable) instance.
    report = ResearchReport.model_validate(v4_report(frozen, list(gaps)).model_dump(mode="json"))
    payload = next(
        e["payload"] for e in evidence if e["payload"].get("review_profile") == "commerce_review"
    )
    if payload.get("answer_version") == 6:
        from app.commerce.explanation_contract_v6 import contract

        if not contract(payload.get("objective", ""))["recommendations_allowed"]:
            report.sections[4].claims = []
            note = "行动建议：按请求不提供建议。"
            if len(report.limitations) < 20:
                report.limitations.append(note)
            else:
                report.limitations[-1] += "；" + note
    return explanation_completion(report, evidence)


def compose(evidence, gaps, narrative):
    from app.research.evidence import render_bound_metrics, validate_report

    source = next(e for e in evidence if e["payload"].get("review_profile") == "commerce_review")
    schema = ReviewNarrativeV3
    if source["payload"].get("answer_version") == 6:
        from app.research.schemas import ReviewNarrativeV6

        schema = ReviewNarrativeV6
    narrative = schema.model_validate(
        narrative.model_dump(mode="json") if hasattr(narrative, "model_dump") else narrative
    )
    options = allowed_hypotheses(source["payload"])
    if source["payload"].get("answer_version") == 6:
        from app.commerce.explanation_contract_v6 import allowed

        options = allowed(source["payload"], options)
    # Validate every choice and action BEFORE any capacity truncation.
    if any(code not in options for code in narrative.analysis):
        raise ValueError("hypothesis_outside_evidence_scope")
    texts = render_actions(narrative.recommendations, source["payload"])
    baseline = build_report(evidence, gaps)
    candidate = baseline.model_dump(mode="json")
    selected = list(dict.fromkeys(narrative.analysis))
    slots = 8 - len(candidate["sections"][3]["claims"])
    required = required_explanations(source["payload"])
    prioritized = [c for c in selected if c in required] + [
        c for c in selected if c not in required
    ]
    kept, omitted = prioritized[:slots], [c for c in selected if c not in prioritized[:slots]]
    candidate["sections"][3]["claims"].extend(
        Claim(
            id=f"review-hypothesis-{i}",
            kind="analysis",
            text=options[code],
            evidence_ids=[source["id"]],
        ).model_dump(mode="json")
        for i, code in enumerate(kept)
    )
    recommendations = [
        Claim(
            id=f"review-ai-recommendation-{i}",
            kind="recommendation",
            text=text,
            evidence_ids=[source["id"]],
        ).model_dump(mode="json")
        for i, text in enumerate(texts)
    ]
    if recommendations or source["payload"].get("answer_version") != 6:
        candidate["sections"][4]["claims"] = recommendations
    if omitted:
        disclosure = "补充解释因报告容量省略：" + "、".join(LABELS[c] for c in omitted)
        notes = candidate["limitations"]
        if len(notes) < 20:
            notes.append(disclosure)
        else:
            # Preserve all existing limitations; do not silently clip the 21st.
            notes[-1] += "；" + disclosure
    report = render_bound_metrics(ResearchReport.model_validate(candidate), evidence)
    report = explanation_completion(report, evidence)
    if not validate_report(report, evidence)["passed"]:
        raise ValueError("composed_report_validation_failed")
    metadata = {
        "version": 1,
        "narrative": narrative.model_dump(mode="json"),
        "gaps": list(gaps),
        "selected": selected,
        "kept": kept,
        "omitted": omitted,
        "required_explanations": sorted(required),
        "unshown_required": sorted(required - set(kept)),
        "reason": (
            "required_explanation_capacity"
            if required.intersection(omitted)
            else "optional_hypothesis_capacity"
            if omitted
            else None
        ),
        "baseline_sha256": digest(baseline.model_dump(mode="json")),
    }
    return report, metadata


def binding(report, composition):
    return digest({"report": report, "composition": composition})


def stopped_report(report, reason="model_budget"):
    data = report.model_dump(mode="json")
    claim = next(
        c for s in data["sections"] for c in s["claims"] if c["id"] == "review-answer-completion"
    )
    claim["text"] = "答复完成度：请求尚未答齐（模型阶段未完成）。"
    note = (
        "模型阶段因用量未知停止，答复尚未完成；未继续调用模型"
        if reason == "usage_unknown"
        else "模型阶段因预算停止，答复尚未完成；未继续调用模型"
    )
    if note not in data["limitations"]:
        if len(data["limitations"]) < 20:
            data["limitations"].append(note)
        else:
            data["limitations"][-1] += "；" + note
    return ResearchReport.model_validate(data)


def verify_composition(report, evidence, composition):
    expected, metadata = compose(evidence, composition["gaps"], composition["narrative"])
    return expected.model_dump(mode="json") == report and metadata == composition
