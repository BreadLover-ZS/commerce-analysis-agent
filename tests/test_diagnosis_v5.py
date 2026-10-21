"""A narrowed report must bind its own net facts, not copy its parent's total."""

import pytest

from app.diagnosis.schemas import ClarificationAnswer
from app.diagnosis.store import DiagnosisStore
from tests.test_diagnosis_extensions import run
from tests.test_diagnosis_graph import new_diagnosis, tool_proposal
from tests.test_diagnosis_model_calls import ScriptedDecisionModel
from tests.test_diagnosis_v3 import projected, stop


@pytest.mark.parametrize("runtime", ["v4", "v5"])
async def test_narrowed_net_binding_is_required_only_in_new_version(setup, runtime):
    settings, repo, user, _ = setup
    settings.diagnosis_runtime = runtime
    task, _ = new_diagnosis(setup, "那个商品的销售额变化")
    model = ScriptedDecisionModel(
        {"action": "clarify", "question": "请选择已授权商品", "clarification_scope": "sku"},
        tool_proposal("analyze_refunds"),
        stop(),
        tool_proposal("compare_periods", {"metric": "net_revenue"}),
        stop(),
    )
    waiting = await run(setup, task, model)
    assert waiting["status"] == "waiting_input"
    settings.diagnosis_runtime = "legacy"
    pending = projected(setup, task)["pending_input"]
    DiagnosisStore(repo).answer(
        task["id"],
        user,
        ClarificationAnswer(
            interrupt_id=pending["interrupt_id"],
            expected_version=pending["expected_version"],
            choice="B",
        ),
    )
    done = await run(setup, task, model)
    assert done["validation"]["passed"], done
    assert done["report"]["scope"]["skus"] == ["B"]
    assert done["report"]["versions"]["graph_version"] == "diagnosis-" + runtime
    assert done["report"]["stop_reason"] == "target_covered"
    facts = [
        f
        for f in done["report"]["facts"]
        if f["metric_key"] == "net_revenue.delta" and f["entity_id"] == "B"
    ]
    if runtime == "v5":
        assert len(model.requests) == done["metrics"]["model_calls"] == 5
        assert done["metrics"]["tool_calls"] == 4
        assert len(facts) == 1 and facts[0]["value"] == "200.00"
        assert projected(setup, task)["steps"][2]["assessment"]["correction"][
            "available_tools"
        ] == ["compare_periods"]
    else:
        assert len(model.requests) == 3 and not facts


async def test_demo_narrowing_adds_exact_net_comparison_without_model_requests(setup):
    settings, repo, user, _ = setup
    settings.diagnosis_runtime = "v5"
    task, _ = new_diagnosis(setup, "那个商品的销售额变化")
    assert (await run(setup, task))["status"] == "waiting_input"
    pending = projected(setup, task)["pending_input"]
    DiagnosisStore(repo).answer(
        task["id"],
        user,
        ClarificationAnswer(
            interrupt_id=pending["interrupt_id"],
            expected_version=pending["expected_version"],
            choice="A",
        ),
    )
    done = await run(setup, task)
    assert done["validation"]["passed"], done
    assert done["report"]["stop_reason"] == "target_covered"
    assert any(
        f["metric_key"] == "net_revenue.delta" and f["entity_id"] == "A" and f["value"] == "-400.00"
        for f in done["report"]["facts"]
    )
    assert done["metrics"]["model_calls"] == 0
