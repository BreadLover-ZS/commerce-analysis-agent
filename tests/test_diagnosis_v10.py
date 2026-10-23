"""Actual Worker regressions for public statements; all models are protocol stubs."""

import copy

import pytest

from app.diagnosis.planner_v10 import missing_coverage, public_proposal
from app.diagnosis.reporting import validate_report
from app.storage.models import Task
from evaluations.diagnosis_cases import load_cases
from evaluations.diagnosis_grading import grade
from evaluations.diagnosis_oracle import fixed_numeric_errors, numeric_errors
from evaluations.diagnosis_runtime import admit, collect, execute, new_task, sandbox
from tests.test_diagnosis_graph import tool_proposal
from tests.test_diagnosis_model_calls import ScriptedDecisionModel
from tests.test_diagnosis_v3 import stop

CASES = {c.case_id: c for c in load_cases()[0] if c.split != "holdout"}
FALSE_GAP = "同时缺少完整库存快照、采购在途、多仓及退款回库信息"


@pytest.mark.parametrize("runtime", ["v9", "v10"])
async def test_missing_lead_never_becomes_missing_snapshot_in_new_public_trace(runtime):
    case = CASES["R18"]
    with sandbox(diagnosis_runtime=runtime) as (settings, repo, user, other):
        conversation, bundle, _ = admit(repo, user, other, case)
        task = new_task(
            repo, user, conversation, bundle, case.query, "additional", sources=case.sources
        )
        tool = tool_proposal("inventory_risk", {})
        tool["question"] = FALSE_GAP
        tool["expected_evidence"] = FALSE_GAP
        finish = stop("evidence_gap")
        finish["question"] = FALSE_GAP
        model = ScriptedDecisionModel(tool, finish)
        await execute(repo, settings, model=model)
        record = collect(repo, task["id"], "additional")
        report = record["task"]["report"]
        assert record["task"]["validation"]["passed"], record["task"]
        assert record["task"]["metrics"]["model_calls"] == 2
        payload = next(
            e["payload"] for e in record["evidence"] if e["payload"]["tool"] == "inventory_risk"
        )
        assert not payload["coverage"]["complete"]
        assert all(i["reason"] == "missing_lead_days" for i in payload["items"])
        assert record["model_invocations"][-1]["validated_output"]["question"] == FALSE_GAP
        if runtime == "v9":
            assert "snapshot_complete" not in payload["coverage"]
            assert report["steps"][-1]["proposal"]["question"] == FALSE_GAP
        else:
            assert payload["coverage"]["snapshot_complete"]
            assert payload["coverage"]["sales_window_complete"]
            assert payload["coverage"]["proxy_calculable"]
            assert not payload["coverage"]["lead_days_complete"]
            assert not payload["coverage"]["threshold_comparable"]
            assert all("缺少配置提前期" in g for g in report["open_questions"])
            assert all(FALSE_GAP not in str(s["proposal"]) for s in report["steps"])
            assert not numeric_errors(
                case.sales.model_dump(mode="json"),
                bundle,
                "inventory_risk",
                {"filters": {"skus": case.scope_skus, "categories": []}},
                payload,
                case.inventory,
            )
            changed = copy.deepcopy(payload)
            changed["coverage"]["snapshot_complete"] = False
            assert numeric_errors(
                case.sales.model_dump(mode="json"),
                bundle,
                "inventory_risk",
                {"filters": {"skus": case.scope_skus, "categories": []}},
                changed,
                case.inventory,
            )
            assert grade(case, bundle, record)["automatic_passed"]
            tampered = copy.deepcopy(report)
            tampered["steps"][-1]["proposal"]["question"] = FALSE_GAP
            checked = validate_report(
                tampered, record["task"], bundle, record["evidence"], tampered["steps"]
            )
            assert "unverified_step_description" in checked["errors"]


@pytest.mark.parametrize("case_id", ["D02", "D08", "D15"])
@pytest.mark.parametrize("version", [1, 2])
async def test_fixed_answers_are_program_bound_and_old_task_version_is_preserved(case_id, version):
    case = CASES[case_id]
    with sandbox(commerce_review_answer_version=version) as (settings, repo, user, other):
        conversation, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conversation, bundle, case.query, "fixed")
        # Recovery must use the admitted parameter, even after defaults change.
        settings.commerce_review_answer_version = 1 if version == 2 else 2
        await execute(repo, settings)
        record = collect(repo, task["id"], "fixed")
        done = record["task"]
        assert done["validation"]["passed"], done
        assert done["graph_version"] == f"commerce-review-bundle-v{version}"
        source = record["evidence"][0]["payload"]
        assert not fixed_numeric_errors(case.sales.model_dump(mode="json"), bundle, source)
        text = str(done["report"])
        if version == 2:
            assert "启用前的日期不适用" in text
            assert "不能计算两期利润、转化率" in text
            assert "不能用净销售额变化率替代转化率" in text
            assert "comparison_paid_units" in text
            assert (
                source["fact_catalog"]["comparison_paid_units"]["period_start"]
                == bundle["comparison_period"]["start"]
            )
            if case_id == "D02":
                assert "支付分母收缩效应" in text
                assert source["metrics"]["paid_units_delta"] == "-170"
            changed = copy.deepcopy(source)
            changed["fact_catalog"]["comparison_paid_units"]["value"] = "1"
            assert fixed_numeric_errors(case.sales.model_dump(mode="json"), bundle, changed)
            assert grade(case, bundle, record)["automatic_passed"]
        else:
            assert "answer_version" not in source
            assert "comparison_paid_units" not in source["metrics"]


def test_public_proposal_copy_does_not_change_model_ledger_or_bound_arguments():
    proposal = tool_proposal("inventory_risk", {"filters": {"skus": ["A"]}})
    proposal["question"] = FALSE_GAP
    original = copy.deepcopy(proposal)
    visible = public_proposal(proposal)
    assert proposal == original and visible["question"] != proposal["question"]
    assert visible["arguments"] == proposal["arguments"]
    visible["arguments"]["filters"]["skus"].append("B")
    assert proposal == original


def test_inventory_gap_does_not_claim_missing_snapshot_before_query_or_after_complete_comparison():
    scope = {"skus": ["A", "B"], "objective": None}
    generic = missing_coverage("核对库存", [], scope)
    assert "授权范围内的完整库存快照、完整销售窗口与提前期代理核对" in generic
    evidence = [
        {
            "payload": {
                "tool": "inventory_risk",
                "status": "ok",
                "scope": {"skus": ["A", "B"]},
                "coverage": {"complete": True},
                "items": [{"sku": s, "reason": None} for s in ["A", "B"]],
            }
        }
    ]
    assert not missing_coverage("核对库存", evidence, scope)


def test_retry_of_old_fixed_request_does_not_rebind_after_answer_default_changes(setup):
    from tests.test_review_bound_inputs import review_task

    settings, repo, user, _ = setup
    settings.commerce_review_answer_version = 1
    old, _, request = review_task(setup)
    with repo.session() as db:
        key = db.get(Task, old["id"]).idempotency_key
    settings.commerce_review_answer_version = 2
    retried = repo.create_task(user, request, key)
    assert retried["id"] == old["id"]
    assert retried["graph_version"] == "commerce-review-bundle-v1"
    assert retried["parameters"]["review_answer_version"] == 1


async def test_fixed_v2_worker_keeps_direct_answers_after_runtime_change(setup):
    from tests.test_diagnosis_extensions import run
    from tests.test_review_bound_inputs import review_task

    settings, repo, _, _ = setup
    settings.commerce_review_answer_version = 2
    task, _, _ = review_task(setup, payload=CASES["D02"].sales)
    settings.commerce_review_answer_version = 1
    done = await run(setup, task)
    assert done["validation"]["passed"]
    assert done["graph_version"] == "commerce-review-bundle-v2"
    assert "支付分母收缩效应" in str(done["report"])
    assert "comparison_paid_units" in str(done["report"])
    assert "启用前的日期不适用" in str(done["report"])
    assert "不能计算两期利润、转化率" in str(done["report"])


async def test_v10_worker_stock_snapshot_and_lead_gaps_are_distinct(setup):
    from uuid import uuid4

    from app.research.schemas import TaskRequest
    from tests.test_diagnosis_extensions import run
    from tests.test_diagnosis_sources import combined, stock_payload

    settings, repo, user, _ = setup
    settings.diagnosis_runtime = "v10"
    bundle, _ = combined(setup, stock_payload(lead_days=None))
    task = repo.create_task(
        user,
        TaskRequest(
            conversation_id=repo.create_conversation(user.id),
            profile="commerce_diagnosis",
            query="核对库存覆盖代理和提前期缺口",
            sources=["sales", "inventory"],
            bundle_id=bundle["bundle_id"],
        ),
        str(uuid4()),
    )
    settings.diagnosis_runtime = "legacy"
    finish = stop("evidence_gap")
    finish["question"] = FALSE_GAP
    done = await run(
        setup, task, ScriptedDecisionModel(tool_proposal("inventory_risk", {}), finish)
    )
    assert done["validation"]["passed"], done
    assert done["graph_version"] == "diagnosis-v10"
    assert all("缺少配置提前期" in q for q in done["report"]["open_questions"])
    assert FALSE_GAP not in str(done["report"]["steps"])
