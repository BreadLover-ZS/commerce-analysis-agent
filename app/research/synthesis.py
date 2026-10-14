from app.research.schemas import EvidenceQuote, ResearchFinding, ResearchSynthesis


def validate_synthesis(synthesis, evidence):
    by_id = {e["id"]: e for e in evidence}
    issues, seen = [], set()
    for finding in synthesis.findings:
        if finding.id in seen:
            issues.append("研究发现ID重复")
        seen.add(finding.id)
        sources = set()
        for support in finding.support:
            source = by_id.get(support.evidence_id)
            if not source or source["kind"] == "metrics":
                issues.append("研究发现引用未知或不适用的原文来源")
            elif support.quote not in source["excerpt"]:
                issues.append("研究发现摘录与保存原文不一致")
            else:
                sources.add(source["locator"])
        if finding.kind == "conflict" and len(sources) < 2:
            issues.append("资料冲突至少需要两个不同定位的来源")
    return issues


def excerpt_synthesis(evidence):
    findings = []
    for source in evidence:
        if source["kind"] in {"document", "web", "fixture"} and len(source["excerpt"].strip()) >= 3:
            quote = source["excerpt"][:500]
            findings.append(
                ResearchFinding(
                    id="finding-" + source["id"],
                    statement=(
                        "模拟资料原文摘录："
                        if source["payload"].get("sample")
                        else "资料原文摘录："
                    )
                    + quote,
                    support=[EvidenceQuote(evidence_id=source["id"], quote=quote)],
                )
            )
    return ResearchSynthesis(
        findings=findings[:12], gaps=["确定性摘录未进行模型推理或冲突判定"] if findings else []
    )
