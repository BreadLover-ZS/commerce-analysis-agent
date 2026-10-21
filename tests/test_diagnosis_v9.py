"""Collective scope coverage without redundant work or missing-data shortcuts."""

import copy

import pytest

from app.diagnosis.planner_v7 import APPLICABILITY_GAP
from app.diagnosis.planner_v9 import DYNAMIC_GAP, dynamic_scope_covered, missing_coverage
from tests.test_diagnosis_extensions import extended_task, run
from tests.test_diagnosis_graph import new_diagnosis, tool_proposal
from tests.test_diagnosis_model_calls import ScriptedDecisionModel
from tests.test_diagnosis_v3 import projected, stop


def partition(sku):
    return {
        "payload": {
            "tool": "drilldown_series",
            "status": "ok",
            "comparable": True,
            "scope": {"skus": [sku]},
            "coverage": {name: {"complete": True} for name in ("current", "comparison")},
        }
    }


@pytest.mark.parametrize(
    "change",
    ["only_one", "duplicate", "outside", "incomparable", "missing", "no_prior", "baseline"],
)
def test_collective_scope_requires_every_authorized_object_and_both_complete_periods(change):
    evidence = [partition("A"), partition("B")]
    scope = {"skus": ["A", "B"], "objective": None}
    assert dynamic_scope_covered(evidence, scope)
    if change == "only_one":
        evidence.pop()
    elif change == "duplicate":
        evidence[1] = copy.deepcopy(evidence[0])
    elif change == "outside":
        evidence[1]["payload"]["scope"]["skus"].append("FOREIGN")
    elif change == "incomparable":
        evidence[1]["payload"]["comparable"] = False
    elif change == "missing":
        evidence[1]["payload"]["coverage"]["current"]["complete"] = False
    elif change == "no_prior":
        evidence[1]["payload"]["coverage"].pop("comparison")
    else:
        evidence[1]["payload"]["tool"] = "compare_periods"
    assert not dynamic_scope_covered(evidence, scope)


@pytest.mark.parametrize("runtime,covered", [("v8", False), ("v9", True)])
async def test_partitioned_real_series_complete_without_unasked_ranking(setup, runtime, covered):
    settings, repo, _, _ = setup
    settings.diagnosis_runtime = runtime
    task, _ = new_diagnosis(setup, "按日核对全部商品的净销售额序列")
    settings.diagnosis_runtime = "legacy"
    model = ScriptedDecisionModel(
        tool_proposal("drilldown_series", {"filters": {"skus": ["A"]}}),
        tool_proposal("drilldown_series", {"filters": {"skus": ["B"]}}),
        stop(),
        stop(),
    )
    done = await run(setup, task, model)
    assert done["validation"]["passed"], done
    assert done["report"]["stop_reason"] == ("target_covered" if covered else "invalid_decision")
    assert done["report"]["versions"]["graph_version"] == "diagnosis-" + runtime
    steps = projected(setup, task)["steps"]
    assert not any(s["proposal"].get("tool_name") == "rank_contributors" for s in steps)
    assert len(model.requests) == (3 if covered else 4)
    assert done["metrics"]["tool_calls"] == 4
    assert {
        f["entity_id"] for f in done["report"]["facts"] if f["metric_key"].startswith("series.")
    } == {"A", "B"}
    if covered:
        assert not done["report"]["open_questions"]
        assert dynamic_scope_covered(repo.evidence(task["id"]), done["report"]["scope"])


async def test_complete_series_does_not_cover_different_requested_refund_or_contribution(setup):
    setup[0].diagnosis_runtime = "v9"
    task, _ = new_diagnosis(setup, "按日核对全部商品的净销售额序列")
    model = ScriptedDecisionModel(
        tool_proposal("drilldown_series", {"filters": {"skus": ["A"]}}),
        tool_proposal("drilldown_series", {"filters": {"skus": ["B"]}}),
        stop(),
    )
    done = await run(setup, task, model)
    evidence = setup[1].evidence(task["id"])
    scope = done["report"]["scope"]
    assert not missing_coverage(task["query"], evidence, scope)
    assert DYNAMIC_GAP not in missing_coverage("分商品贡献并核对退款件数", evidence, scope)
    gaps = missing_coverage("分商品贡献并核对退款件数", evidence, scope)
    assert "授权范围内的完整贡献分解" in gaps
    assert "退款金额、件数和分母核对" in gaps


async def test_v9_literal_lookup_declared_gap_becomes_completion(setup):
    setup[0].diagnosis_runtime = "v9"
    task, _, _ = extended_task(
        setup, "查阅库存规则原文，列出阈值；适用性尚待核验", sources=["sales", "documents"]
    )
    model = ScriptedDecisionModel(
        tool_proposal("search_rule_documents", {"query": "库存"}), stop("evidence_gap")
    )
    done = await run(setup, task, model)
    assert done["validation"]["passed"], done
    assert done["report"]["rule_quotes"]
    assert done["validation"]["rule_semantics"] == "unverified"
    assert done["report"]["stop_reason"] == "target_covered"
    assert not done["report"]["open_questions"]
    assert len(model.requests) == 2


@pytest.mark.parametrize(
    "runtime,query,reason",
    [
        ("v9", "确定库存规则是否适用", "evidence_gap"),
        ("v8", "查阅库存规则原文，列出阈值；适用性尚待核验", "evidence_gap"),
    ],
)
async def test_declared_gap_survives_applicability_verification_and_older_runtime(
    setup, runtime, query, reason
):
    setup[0].diagnosis_runtime = runtime
    task, _, _ = extended_task(setup, query, sources=["sales", "documents"])
    model = ScriptedDecisionModel(
        tool_proposal("search_rule_documents", {"query": "库存"}), stop("evidence_gap")
    )
    done = await run(setup, task, model)
    assert done["report"]["stop_reason"] == reason
    assert (APPLICABILITY_GAP in done["report"]["open_questions"]) is (
        "适用" in query and "尚待核验" not in query
    )


async def test_v9_declared_gap_survives_unsupported_question(setup):
    setup[0].diagnosis_runtime = "v9"
    task, _ = new_diagnosis(setup, "请按这些现有记录计算利润金额并解释利润变化")
    model = ScriptedDecisionModel(stop("evidence_gap"))
    done = await run(setup, task, model)
    assert done["report"]["stop_reason"] == "evidence_gap"
    assert not model.requests
