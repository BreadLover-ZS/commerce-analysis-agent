"""Separate completion of literal lookup from verification of applicability."""

import json
import re

from app.diagnosis import planner_v3, planner_v5, planner_v6
from app.diagnosis.intent_v3 import CLAUSES, RULES

APPLICABILITY_GAP = "规则适用范围、业务对象与版本优先级尚未经独立核验"
SYSTEM_PROMPT_V7 = (
    planner_v5.SYSTEM_PROMPT_V5
    + """
区分问题所要求的完成与资料适用性已被证明。若用户只要求查阅原文、列出阈值并标注尚待核验，在引用齐备且required_evidence_gaps为空时应选target_covered；解读仍必须unverified。
若用户要求确定哪条规则实际适用或版本优先级，原文命中不能完成该核验，应保留真实缺口。不得用target_covered表示适用性得到认证。
"""
)


def applicability_requested(query):
    if not RULES.search(query):
        return False
    for clause in CLAUSES.split(query):
        if re.search(r"尚待|未核验|待核验|待核对|不(?:要|必|需|应|能)|无需|勿", clause):
            continue
        if re.search(
            r"(?:确定|判断|判定|确认|验证|核验).*(?:适用|优先级)|(?:哪条|哪个|哪份).*规则.*适用",
            clause,
        ):
            return True
    return False


def objectives(query):
    return sorted(
        set(planner_v3.objectives(query))
        | ({"rule_applicability"} if applicability_requested(query) else set())
    )


def missing_coverage(query, evidence, scope):
    gaps = planner_v5.missing_coverage(query, evidence, scope)
    if scope.get("objective") == "rule_applicability" or (
        not scope.get("objective") and applicability_requested(query)
    ):
        gaps.append(APPLICABILITY_GAP)
    return gaps


def prompt(task, bundle, scope, steps, evidence, catalog):
    context = json.loads(planner_v6.prompt(task, bundle, scope, steps, evidence, catalog)[1][1])
    context.update(
        prompt_version="diagnosis-v7",
        objectives=objectives(task["query"]),
        required_evidence_gaps=missing_coverage(task["query"], evidence, scope),
    )
    return [("system", SYSTEM_PROMPT_V7), ("human", json.dumps(context, ensure_ascii=False))]
