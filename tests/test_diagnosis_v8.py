"""Cross-field correction, real tools, saved-version dispatch and bounded rejection."""

import json

import pytest

from app.diagnosis import planner_v7, planner_v8
from app.diagnosis.graph_constraints import SERIES_CONSTRAINT
from tests.test_diagnosis_extensions import run
from tests.test_diagnosis_graph import new_diagnosis, tool_proposal
from tests.test_diagnosis_model_calls import ScriptedDecisionModel
from tests.test_diagnosis_v3 import projected, stop


@pytest.mark.parametrize("runtime", ["v7", "v8"])
async def test_series_constraint_feedback_and_correction_preserve_saved_version(setup, runtime):
    setup[0].diagnosis_runtime = runtime
    task, _ = new_diagnosis(setup, "查看每个商品的每日走势")
    setup[0].diagnosis_runtime = "legacy"
    model = ScriptedDecisionModel(
        tool_proposal("drilldown_series", {"filters": {"skus": ["A", "B"]}}),
        tool_proposal("drilldown_series", {"filters": {"skus": ["A"]}}),
        tool_proposal("drilldown_series", {"filters": {"skus": ["B"]}}),
        stop(),
    )
    done = await run(setup, task, model)
    assert done["validation"]["passed"], done
    assert done["report"]["versions"]["graph_version"] == "diagnosis-" + runtime
    assert done["report"]["stop_reason"] == "target_covered"
    steps = projected(setup, task)["steps"]
    correction = steps[0]["assessment"]["correction"]
    assert steps[0]["status"] == "REJECTED"
    assert ("constraints" in correction) is (runtime == "v8")
    if runtime == "v8":
        assert correction["constraints"] == [SERIES_CONSTRAINT]
        assert "exactly_one_entity" in json.dumps(model.requests[1], ensure_ascii=False)
    else:
        assert correction == {
            "code": "argument_schema",
            "fields": [{"field": "arguments", "type": "value_error"}],
        }
    assert [s["status"] for s in steps] == ["REJECTED", "SUCCEEDED", "SUCCEEDED", "SUCCEEDED"]
    assert len(model.requests) == done["metrics"]["model_calls"] == 4
    facts = done["report"]["facts"]
    assert {f["entity_id"] for f in facts if f["metric_key"].startswith("series.")} == {"A", "B"}
    assert done["metrics"]["tool_calls"] == 4  # Two baselines, two actual series, no auto-split.


async def test_series_constraint_does_not_relax_rejection_or_expose_rejected_data(setup):
    setup[0].diagnosis_runtime = "v8"
    task, _ = new_diagnosis(setup, "查看每个商品的每日走势")
    secret = "PRIVATE-REJECTED-PAYLOAD"
    invalid = tool_proposal("drilldown_series", {"filters": {"skus": ["A", "B"]}, secret: secret})
    done = await run(setup, task, ScriptedDecisionModel(invalid, invalid))
    assert done["validation"]["passed"], done
    assert done["report"]["stop_reason"] == "invalid_decision"
    steps = projected(setup, task)["steps"]
    assert len(steps) == 2 and all(s["status"] == "REJECTED" for s in steps)
    feedback = json.dumps([s["assessment"]["correction"] for s in steps])
    assert secret not in feedback and "<extra>" in feedback
    assert all(s["assessment"]["correction"]["constraints"] == [SERIES_CONSTRAINT] for s in steps)
    assert done["metrics"]["tool_calls"] == 2


def test_new_prompt_discloses_only_enabled_tool_constraints_without_mutating_v7():
    task = {"query": "核对商品覆盖", "parameters": {"budget": {}, "sources": ["sales"]}}
    bundle = {
        "bundle_id": "b",
        "bundle_hash": "h",
        "sample": True,
        "period": {},
        "comparison_period": {},
    }
    scope = {"skus": ["A", "B"], "objective": None}
    old = planner_v7.prompt(task, bundle, scope, [], [], {})
    assert "tool_constraints" not in json.loads(old[1][1])
    assert (
        json.loads(planner_v8.prompt(task, bundle, scope, [], [], {})[1][1])["tool_constraints"]
        == {}
    )
    catalog = {"drilldown_series": {}}
    context = json.loads(planner_v8.prompt(task, bundle, scope, [], [], catalog)[1][1])
    assert context["tool_constraints"] == {"drilldown_series": SERIES_CONSTRAINT}
    assert planner_v7.prompt(task, bundle, scope, [], [], {}) == old


async def test_demo_multiple_daily_objects_remain_in_scope_and_separate_calls(setup):
    setup[0].diagnosis_runtime = "v8"
    task, _ = new_diagnosis(setup, "逐项按日核对所有授权商品的日记录")
    done = await run(setup, task)
    assert done["validation"]["passed"], done
    assert done["report"]["scope"]["skus"] == ["A", "B"]
    assert done["report"]["stop_reason"] == "target_covered"
    steps = projected(setup, task)["steps"]
    assert [s["proposal"]["arguments"].get("filters", {}).get("skus") for s in steps[:-1]] == [
        ["A"],
        ["B"],
    ]
    assert done["metrics"]["model_calls"] == 0
    assert done["metrics"]["tool_calls"] == 4
