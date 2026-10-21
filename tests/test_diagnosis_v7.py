"""Lookup completion cannot certify rule applicability, even with literal quotes."""

import pytest

from app.diagnosis.planner_v7 import APPLICABILITY_GAP, applicability_requested
from app.diagnosis.schemas import ClarificationAnswer
from app.diagnosis.store import DiagnosisStore
from tests.test_diagnosis_extensions import extended_task, run
from tests.test_diagnosis_graph import tool_proposal
from tests.test_diagnosis_model_calls import ScriptedDecisionModel
from tests.test_diagnosis_v3 import projected, stop


@pytest.mark.parametrize(
    "query,requested",
    [
        ("查阅库存规则原文，列出阈值和尚待核验的适用性", False),
        ("只列规则原文，不要判断适用性", False),
        ("确定库存规则是否适用", True),
        ("验证哪条政策实际适用", True),
    ],
)
def test_literal_lookup_and_verification_are_different_goals(query, requested):
    assert applicability_requested(query) is requested


@pytest.mark.parametrize(
    "query,reason",
    [
        ("查阅库存规则原文，标注适用性尚待核验", "target_covered"),
        ("确定库存规则是否适用", "evidence_gap"),
    ],
)
async def test_bound_quote_and_model_covered_claim_cannot_certify_applicability(
    setup, query, reason
):
    setup[0].diagnosis_runtime = "v7"
    task, _, _ = extended_task(setup, query, sources=["sales", "documents"])
    model = ScriptedDecisionModel(tool_proposal("search_rule_documents", {"query": "库存"}), stop())
    done = await run(setup, task, model)
    assert done["validation"]["passed"], done
    assert done["report"]["rule_quotes"]
    assert done["validation"]["rule_semantics"] == "unverified"
    assert done["report"]["stop_reason"] == reason
    assert (APPLICABILITY_GAP in done["report"]["open_questions"]) is (reason == "evidence_gap")
    assert len(model.requests) == 2


async def test_authorized_objective_narrowing_keeps_lookup_and_unverified_boundary(setup):
    setup[0].diagnosis_runtime = "v7"
    task, _, _ = extended_task(
        setup, "确定库存规则是否适用并列出原文", sources=["sales", "documents"]
    )
    model = ScriptedDecisionModel(
        {
            "action": "clarify",
            "question": "请确认先核对原文还是实际适用性",
            "clarification_scope": "objective",
        },
        tool_proposal("search_rule_documents", {"query": "库存"}),
        stop(),
    )
    assert (await run(setup, task, model))["status"] == "waiting_input"
    pending = projected(setup, task)["pending_input"]
    DiagnosisStore(setup[1]).answer(
        task["id"],
        setup[2],
        ClarificationAnswer(
            interrupt_id=pending["interrupt_id"],
            expected_version=pending["expected_version"],
            choice="rules",
        ),
    )
    done = await run(setup, task, model)
    assert done["validation"]["passed"], done
    assert done["report"]["scope"]["objective"] == "rules"
    assert done["report"]["stop_reason"] == "target_covered"
    assert done["report"]["rule_quotes"] and done["validation"]["rule_semantics"] == "unverified"
