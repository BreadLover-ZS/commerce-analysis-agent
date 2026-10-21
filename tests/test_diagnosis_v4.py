"""Premature completion correction stays inside the existing graph and budgets."""

import json

import pytest

from tests.conftest import expire_lease
from tests.test_diagnosis_extensions import run
from tests.test_diagnosis_graph import new_diagnosis, tool_proposal
from tests.test_diagnosis_model_calls import ScriptedDecisionModel
from tests.test_diagnosis_recovery import crash_at
from tests.test_diagnosis_tools import oracle_payload
from tests.test_diagnosis_v3 import projected, stop


@pytest.mark.parametrize("runtime,corrected", [("v3", False), ("v4", True)])
async def test_premature_covered_stop_is_corrected_only_for_new_contract(setup, runtime, corrected):
    setup[0].diagnosis_runtime = runtime
    task, _ = new_diagnosis(setup, "核对 A 的退款金额及件数分母")
    model = ScriptedDecisionModel(
        tool_proposal("compare_periods", {"metric": "refund_amount"}),
        stop(),
        tool_proposal("analyze_refunds"),
        stop(),
    )
    done = await run(setup, task, model)
    assert done["validation"]["passed"], done
    steps = projected(setup, task)["steps"]
    assert done["report"]["versions"]["graph_version"] == "diagnosis-" + runtime
    if corrected:
        assert done["report"]["stop_reason"] == "target_covered"
        assert steps[1]["status"] == "REJECTED"
        feedback = steps[1]["assessment"]["correction"]
        assert feedback["code"] == "computable_evidence_gap"
        assert feedback["available_tools"] == ["analyze_refunds"]
        assert (
            json.loads(model.requests[2][1][1])["completed_steps"][1]["assessment"]["correction"]
            == feedback
        )
        assert done["metrics"]["model_calls"] == len(model.requests) == 4
        assert done["metrics"]["tool_calls"] == 4
        assert any(
            f["metric_key"] == "refund_ratio.current" and f["value"] == "30.00"
            for f in done["report"]["facts"]
        )
    else:
        assert done["report"]["stop_reason"] == "evidence_gap"
        assert steps[1]["status"] == "SUCCEEDED"
        assert len(model.requests) == 2


async def test_repeated_premature_covered_stops_cannot_escape_limits(setup):
    setup[0].diagnosis_runtime = "v4"
    task, _ = new_diagnosis(setup, "核对退款金额及分母")
    model = ScriptedDecisionModel(stop(), stop(), stop())
    done = await run(setup, task, model)
    assert done["validation"]["passed"], done
    assert done["report"]["stop_reason"] == "invalid_decision"
    assert len(model.requests) == done["metrics"]["model_calls"] == 2
    assert done["metrics"]["tool_calls"] == 2
    assert all(s["status"] == "REJECTED" for s in projected(setup, task)["steps"])


async def test_incomplete_source_and_model_declared_gap_are_not_forced_to_success(setup):
    setup[0].diagnosis_runtime = "v4"
    payload = oracle_payload()
    payload.daily_sales.pop()
    task, _ = new_diagnosis(setup, "核对退款金额及分母", payload=payload)
    model = ScriptedDecisionModel(stop())
    done = await run(setup, task, model)
    assert done["validation"]["passed"], done
    assert done["report"]["stop_reason"] == "evidence_gap" and not model.requests
    task, _ = new_diagnosis(setup, "核对退款金额及分母")
    model = ScriptedDecisionModel(stop("evidence_gap"))
    done = await run(setup, task, model)
    assert done["validation"]["passed"], done
    assert done["report"]["stop_reason"] == "evidence_gap" and len(model.requests) == 1


async def test_v4_committed_completion_rejection_recovers_without_duplicate_charge(setup):
    settings, repo, *_ = setup
    settings.diagnosis_runtime = "v4"
    settings.lease_seconds = 30
    task, _ = new_diagnosis(setup, "核对 A 的退款件数及分母")
    await crash_at(settings, "completion_rejection_committed")
    settings.diagnosis_runtime = "legacy"
    before = repo.get_task(task["id"])
    assert before["metrics"]["model_calls"] == 1
    expire_lease(repo, task["id"])
    model = ScriptedDecisionModel(tool_proposal("analyze_refunds"), stop())
    done = await run(setup, task, model)
    assert done["validation"]["passed"], done
    assert done["report"]["stop_reason"] == "target_covered"
    assert done["report"]["versions"]["graph_version"] == "diagnosis-v4"
    assert len(model.requests) == 2 and done["metrics"]["model_calls"] == 3
    assert done["metrics"]["tool_calls"] == 3
    assert len(projected(setup, task)["steps"]) == 3
