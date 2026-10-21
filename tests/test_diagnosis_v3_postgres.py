"""Version dispatch and repair evidence on actual isolated PostgreSQL."""

import os

import pytest
from sqlalchemy import select

from app.storage.models import User
from tests.conftest import expire_lease
from tests.test_diagnosis_extensions import run
from tests.test_diagnosis_graph import new_diagnosis
from tests.test_diagnosis_model_calls import ScriptedDecisionModel
from tests.test_diagnosis_recovery import crash_at
from tests.test_diagnosis_v3 import (
    stop,
    v3,
)
from tests.test_diagnosis_v3 import (
    test_coverage_question_uses_baseline_and_distinguishes_zero_from_missing as check_coverage,
)
from tests.test_diagnosis_v3 import (
    test_rule_lookup_keeps_literal_manifest_quotes_despite_no_current_sales as check_rules,
)
from tests.test_diagnosis_v3 import (
    test_saved_contract_is_used_after_deployment_default_changes as check_dispatch,
)
from tests.test_diagnosis_v3 import (
    test_schema_feedback_allows_correction_without_echoing_rejected_values as check_correction,
)
from tests.test_diagnosis_v3 import (
    test_v3_clarification_checkpoint_survives_default_rollback as check_clarification,
)
from tests.test_diagnosis_v4 import (
    test_premature_covered_stop_is_corrected_only_for_new_contract as check_stop_correction,
)
from tests.test_diagnosis_v4 import (
    test_repeated_premature_covered_stops_cannot_escape_limits as check_stop_limits,
)
from tests.test_diagnosis_v4 import (
    test_v4_committed_completion_rejection_recovers_without_duplicate_charge as check_stop_recovery,
)
from tests.test_diagnosis_v5 import (
    test_narrowed_net_binding_is_required_only_in_new_version as check_narrowed_net,
)
from tests.test_diagnosis_v6 import (
    test_worker_registry3_keeps_distinct_literal_quotes_and_unverified_semantics as check_registry3,
)
from tests.test_diagnosis_v7 import (
    test_bound_quote_and_model_covered_claim_cannot_certify_applicability as check_rule_goal,
)
from tests.test_diagnosis_v8 import (
    test_series_constraint_feedback_and_correction_preserve_saved_version as check_series_constraint,
)
from tests.test_diagnosis_v9 import (
    test_partitioned_real_series_complete_without_unasked_ranking as check_collective_scope,
)
from tests.test_postgres import postgres_setup as postgres_setup

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"), reason="未提供隔离 PostgreSQL 测试服务"
)


@pytest.fixture
def setup(postgres_setup):
    settings, repo, user = postgres_setup
    with repo.session() as db:
        other = db.scalar(select(User).where(User.email == "other@example.com"))
    return settings, repo, user, other


async def test_postgres_rule_only_with_missing_sales(setup):
    await check_rules(setup)


@pytest.mark.parametrize("missing", [False, True])
async def test_postgres_coverage_zero_and_missing(setup, missing):
    await check_coverage(setup, missing)


async def test_postgres_schema_feedback_and_correction(setup):
    await check_correction(setup)


@pytest.mark.parametrize(
    "runtime,query,version,tools",
    [
        ("legacy", "不计算利润，核对 A 退款", "diagnosis-v1", 2),
        ("legacy", "不计算利润，核对 A 库存", "diagnosis-v2", 2),
        ("v3", "不计算利润，核对 A 退款", "diagnosis-v3", 3),
    ],
)
async def test_postgres_saved_contract_survives_config_change(
    setup, runtime, query, version, tools
):
    await check_dispatch(setup, runtime, query, version, tools)


async def test_postgres_clarification_survives_default_rollback(setup):
    await check_clarification(setup)


async def test_postgres_v4_stop_correction_and_ledger(setup):
    await check_stop_correction(setup, "v4", True)


async def test_postgres_v4_repeated_stop_is_bounded(setup):
    await check_stop_limits(setup)


async def test_postgres_v4_rejected_completion_survives_sigkill_and_rollback(setup):
    await check_stop_recovery(setup)


async def test_postgres_v5_narrowed_net_evidence_survives_default_rollback(setup):
    await check_narrowed_net(setup, "v5")


async def test_postgres_registry3_retrieval_keeps_bound_quotes(setup):
    await check_registry3(setup)


async def test_postgres_literal_quotes_cannot_certify_applicability(setup):
    await check_rule_goal(setup, "确定库存规则是否适用", "evidence_gap")


async def test_postgres_v8_cross_field_correction_preserves_saved_version(setup):
    await check_series_constraint(setup, "v8")


async def test_postgres_v9_collective_scope_keeps_saved_contract(setup):
    await check_collective_scope(setup, "v9", True)


@pytest.mark.parametrize(
    "phase",
    ["tool_committed", "model_output_committed", "model_before_send", "model_before_commit"],
)
async def test_postgres_v3_sigkill_preserves_tool_identity_or_unknown_charge(setup, phase):
    settings, repo, _, _ = v3(setup)
    settings.lease_seconds = 30
    task, _ = new_diagnosis(setup, "核对商品贡献")
    await crash_at(settings, phase)
    before = repo.evidence(task["id"])
    settings.diagnosis_runtime = "legacy"
    expire_lease(repo, task["id"])
    model = ScriptedDecisionModel(stop()) if phase.startswith("model") else None
    done = await run(setup, task, model)
    assert done["validation"]["passed"], done
    assert done["report"]["versions"]["graph_version"] == "diagnosis-v3"
    after = repo.evidence(task["id"])
    assert all(e in after for e in before)
    assert len(after) == done["metrics"]["tool_calls"]
    if phase in {"model_before_send", "model_before_commit"}:
        assert done["report"]["stop_reason"] == "usage_unknown"
        assert not done["metrics"]["usage_complete"]
        assert done["metrics"]["model_calls"] == 1
        assert not model.requests
    else:
        assert done["report"]["stop_reason"] == "target_covered"
        assert done["metrics"]["tool_calls"] == 3
        if model:
            assert len(model.requests) == 1
            assert done["metrics"]["model_calls"] == 2
