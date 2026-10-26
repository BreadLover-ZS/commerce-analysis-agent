"""Deterministic v4 direct answers, computed from the shared request obligations."""

import copy

from app.diagnosis.completion_v15 import amount_witness, completion, value_obligations
from app.research.schemas import Claim


def build_report(evidence, gaps):
    from app.research.evidence import commerce_review_report, metric_reference, render_bound_metrics

    source = next(e for e in evidence if e["payload"].get("review_profile") == "commerce_review")
    p, eid = source["payload"], source["id"]
    legacy = copy.deepcopy(evidence)
    next(e for e in legacy if e["id"] == eid)["payload"]["answer_version"] = 3
    report = commerce_review_report(legacy, gaps)
    query, scope = p["objective"], p["scope"]
    required = value_obligations(query, [source], scope)
    displayed = {m.key for s in report.sections for c in s.claims for m in c.metrics}
    missing = list(
        dict.fromkeys(
            v["fact_key"]
            for v in required
            if v["status"] == "acquired" and v["fact_key"] not in displayed
        )
    )
    # Retain the existing report's bounded size, declaring any unshown item incomplete.
    section = report.sections[0]
    for start in range(0, len(missing), 8):
        if len(section.claims) == 8:
            break
        section.claims.append(
            Claim(
                id=f"review-v4-required-{start}",
                kind="fact",
                text="由程序生成的必答指标",
                evidence_ids=[eid],
                metrics=[metric_reference(source, k) for k in missing[start : start + 8]],
            )
        )
    witness = (
        amount_witness([source], scope)
        if p["answer_contract"]["amount_identity_requested"]
        else None
    )
    if witness:
        report.sections[3].claims.append(
            Claim(
                id="review-amount-identity",
                kind="analysis",
                evidence_ids=[eid],
                text="同范围完整两期金额对账：净销售额变化 = 支付金额变化 − 退款金额变化。上述直接展示的金额已按分核对，记账恒等式不证明业务因果。",
            )
        )
    done = completion(query, [source], scope, report=report.model_dump(mode="json"))
    names = {
        "fulfilled": "请求已答齐",
        "boundary_explained": "缺口与限制已答齐，源数据仍不完整",
        "incomplete": "请求尚未答齐",
    }
    report.sections[3].claims.append(
        Claim(
            id="review-answer-completion",
            kind="analysis",
            evidence_ids=[eid],
            text="答复完成度：" + names[done["status"]] + "。",
        )
    )
    if done["omitted_items"]:
        report.limitations.insert(0, "必答项尚未展示：" + "、".join(done["omitted_items"]))
    return render_bound_metrics(report, evidence)
