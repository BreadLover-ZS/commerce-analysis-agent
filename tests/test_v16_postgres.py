"""v16 accounting and recovery on real isolated PostgreSQL, no provider traffic."""

import os

import pytest

from tests.test_diagnosis_v16 import (
    test_budget_stopped_worker_keeps_facts_and_does_not_claim_completion as check_stop,
)
from tests.test_diagnosis_v16 import (
    test_denial_crash_recovery_never_retries_and_old_lease_is_fenced as check_recovery,
)
from tests.test_diagnosis_v16 import (
    test_full_sdk_denial_is_zero_once_for_both_call_entries as check_denial,
)
from tests.test_diagnosis_v16 import (
    test_new_entry_recovery_reauthorizes_before_reusing_a_denial as check_auth,
)
from tests.test_diagnosis_v16 import (
    test_sent_response_usage_and_schema_are_independently_accounted as check_response,
)
from tests.test_diagnosis_v16 import (
    test_v16_successful_response_checkpoint_recovery as check_success,
)
from tests.test_review_bound_postgres import postgres_setup as postgres_setup
from tests.test_review_bound_postgres import setup as setup

pytestmark = pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="需要隔离PostgreSQL")


@pytest.mark.parametrize("fixed", [False, True])
async def test_pg_both_entry_denials(setup, tmp_path, fixed):
    await check_denial(setup, tmp_path, fixed)


@pytest.mark.parametrize("crash_after_proof", [False, True])
async def test_pg_denial_recovery_fencing(setup, tmp_path, monkeypatch, crash_after_proof):
    await check_recovery(setup, tmp_path, monkeypatch, crash_after_proof)


@pytest.mark.parametrize("mode", ["valid", "bad_parse", "missing_usage", "negative_usage"])
async def test_pg_response_accounting(setup, mode):
    await check_response(setup, mode)


@pytest.mark.parametrize("group", ["fixed", "agent"])
async def test_pg_worker_budget_stop_exports(setup, tmp_path, group):
    await check_stop(setup, tmp_path, group)


@pytest.mark.parametrize("limit", [1, 12])
async def test_pg_v16_saved_response_no_paid_retry(setup, limit):
    await check_success(setup, limit)


@pytest.mark.parametrize("fixed", [False, True])
@pytest.mark.parametrize("deny", ["revoked", "cancelled", "stale"])
async def test_pg_denial_recovery_authorization(setup, tmp_path, fixed, deny):
    await check_auth(setup, tmp_path, fixed, deny)
