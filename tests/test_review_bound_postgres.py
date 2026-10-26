"""Shared-input fixed baseline runs through the original Worker on isolated PG."""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.storage.models import Task, User
from tests.test_postgres import postgres_setup as postgres_setup
from tests.test_review_bound_inputs import review_task
from tests.test_review_bound_inputs import (
    test_baseline_cache_and_model_start_reauthorize_but_inflight_usage_is_retained as check_cache,
)
from tests.test_review_bound_inputs import (
    test_bound_review_metadata_version_gate_and_api_download_permissions as check_api,
)
from tests.test_review_bound_inputs import (
    test_fixed_and_agent_workers_use_identical_explicit_period_scope_and_source as check_pair,
)

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"), reason="未提供隔离 PostgreSQL 测试服务"
)


@pytest.fixture
def setup(postgres_setup):
    settings, repo, user = postgres_setup
    with repo.session() as db:
        other = db.scalar(select(User).where(User.email == "other@example.com"))
    return settings, repo, user, other


async def test_postgres_same_input_fixed_and_agent_worker_results(setup):
    await check_pair(setup)


async def test_postgres_bound_baseline_cache_revocation_and_usage(setup):
    await check_cache(setup)


async def test_postgres_bound_baseline_version_and_download_permissions(client, setup):
    await check_api(client, setup)


async def test_postgres_bound_baseline_concurrent_idempotent_admission(setup):
    _, repo, user, _ = setup
    _, _, request = review_task(setup)
    key = str(uuid4())
    before = len(repo.list_tasks(user.id))
    results = await asyncio.gather(
        *(asyncio.to_thread(repo.create_task, user, request, key) for _ in range(4))
    )
    assert len({row["id"] for row in results}) == 1
    with repo.session() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == before + 1


async def test_postgres_fixed_v2_answers_survive_runtime_change(setup):
    from tests.test_diagnosis_v10 import (
        test_fixed_v2_worker_keeps_direct_answers_after_runtime_change,
    )

    await test_fixed_v2_worker_keeps_direct_answers_after_runtime_change(setup)


async def test_postgres_v10_stock_gap_and_public_trace(setup):
    from tests.test_diagnosis_v10 import test_v10_worker_stock_snapshot_and_lead_gaps_are_distinct

    await test_v10_worker_stock_snapshot_and_lead_gaps_are_distinct(setup)


async def test_postgres_v11_persisted_contract_and_completion(setup):
    from tests.test_diagnosis_v11 import (
        test_v11_saved_contract_survives_default_change_and_two_call_budget,
    )

    await test_v11_saved_contract_survives_default_change_and_two_call_budget(setup)


@pytest.mark.parametrize("bad_action", [False, True])
async def test_postgres_fixed_v3_model_protocol_and_fallback(setup, bad_action):
    from tests.test_fixed_v3_protocol import (
        test_fixed_v3_protocol_context_and_invalid_action_fallback,
    )

    await test_fixed_v3_protocol_context_and_invalid_action_fallback(setup, bad_action)


async def test_postgres_v12_persisted_contract_and_report_gate(setup):
    from tests.test_diagnosis_v12 import (
        test_v12_saved_contract_survives_runtime_change_and_publishes,
    )

    await test_v12_saved_contract_survives_runtime_change_and_publishes(setup)


async def test_postgres_v13_saved_entity_contract_and_exports(setup):
    from tests.test_diagnosis_v13 import (
        test_v13_persisted_contract_survives_defaults_and_client_retry,
    )

    await test_v13_persisted_contract_survives_defaults_and_client_retry(setup)


@pytest.mark.parametrize("runtime", ["v13", "v14"])
@pytest.mark.parametrize("limit", [1, 12])
async def test_postgres_saved_response_recovers_before_budget(setup, runtime, limit):
    from tests.test_diagnosis_v14 import saved_response_recovery

    await saved_response_recovery(setup, runtime, limit)


async def test_postgres_v14_saved_contract_and_publication(setup):
    from tests.test_diagnosis_v14 import (
        test_v14_bound_contract_and_publishing_survive_default_change,
    )

    await test_v14_bound_contract_and_publishing_survive_default_change(setup)


async def test_postgres_v15_fixed4_saved_contract_and_worker(setup):
    from tests.test_diagnosis_v15 import check_v15_and_fixed4_saved_admission

    await check_v15_and_fixed4_saved_admission(setup)


@pytest.mark.parametrize("limit", [1, 12])
async def test_postgres_v15_model_output_checkpoint_recovery(setup, limit):
    from tests.test_diagnosis_v14 import saved_response_recovery

    await saved_response_recovery(setup, "v15", limit)


@pytest.mark.parametrize("bad_action", [False, True])
async def test_postgres_fixed4_model_review_and_fallback(setup, bad_action):
    from tests.test_diagnosis_v15 import (
        test_fixed4_model_context_full_review_and_fallback_keep_required_facts,
    )

    await test_fixed4_model_context_full_review_and_fallback_keep_required_facts(setup, bad_action)
