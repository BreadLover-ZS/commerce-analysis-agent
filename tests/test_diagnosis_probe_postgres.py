"""First-failure and held-budget boundaries on real isolated PostgreSQL."""

import os

import pytest

from tests.test_diagnosis_probe import (
    test_business_rejected_diagnosis_step_stops_before_a_corrective_proposal as check_step,
)
from tests.test_diagnosis_probe import (
    test_cached_nonprogress_does_not_pay_for_a_second_proposal as check_cache,
)
from tests.test_diagnosis_probe import (
    test_first_fixed_failure_never_spends_on_repair_or_semantic_review as check_fixed,
)
from tests.test_diagnosis_probe import (
    test_frozen_old_unknown_does_not_break_new_proof_of_no_send as check_hold,
)
from tests.test_review_bound_postgres import postgres_setup as postgres_setup
from tests.test_review_bound_postgres import setup as setup

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"), reason="Requires isolated PostgreSQL"
)


@pytest.mark.parametrize("kind", ["schema", "scope"])
async def test_postgres_fixed_first_failure(setup, tmp_path, kind):
    await check_fixed(setup, tmp_path, kind)


@pytest.mark.parametrize("fixed", [False, True])
async def test_postgres_hold_does_not_change_new_not_sent_proof(setup, tmp_path, fixed):
    await check_hold(setup, tmp_path, fixed)


async def test_postgres_rejected_step_stops(setup, tmp_path):
    await check_step(setup, tmp_path)


async def test_postgres_cached_nonprogress_stops(setup, tmp_path):
    await check_cache(setup, tmp_path)


def test_postgres_refund_witness_completes_identity(setup):
    from tests.test_probe_budget_repair import (
        test_one_refund_query_completes_the_scope_identity_after_ranked_components,
    )

    test_one_refund_query_completes_the_scope_identity_after_ranked_components(setup)


def test_postgres_explicit_reserve_is_frozen(setup):
    from tests.test_probe_budget_repair import context

    settings, repo, _, _ = setup
    settings.model_decision_reserve = 0
    settings.max_model_calls = 4
    c = context(setup)
    assert c["remaining_budget"]["decision_calls"] == 4
    from sqlalchemy import select

    from app.storage.models import Task

    with repo.session() as db:
        stored = db.scalar(select(Task).order_by(Task.created_at.desc()).limit(1))
        params = stored.parameters
    assert params["budget"]["max_model_calls"] == 4
    assert params["budget"]["decision_call_reserve"] == 0
