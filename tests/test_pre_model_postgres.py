"""New pre-model runtime boundaries on real isolated PostgreSQL; all SDK I/O is mocked."""

import os

import pytest

from tests.test_pre_model_preflight import (
    test_ambiguous_or_conflicting_constraints_fail_before_task_admission as check_admission,
)
from tests.test_pre_model_preflight import (
    test_semantic_budget_fallback_is_revalidated_before_publication as check_budget,
)
from tests.test_review_bound_postgres import postgres_setup as postgres_setup
from tests.test_review_bound_postgres import setup as setup

pytestmark = pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="需要隔离PostgreSQL")


@pytest.mark.parametrize("corrupt_rebuild", [False, True])
async def test_v6_final_report_revalidation_after_budget_stop(
    setup, tmp_path, monkeypatch, corrupt_rebuild
):
    await check_budget(setup, tmp_path, monkeypatch, corrupt_rebuild)


@pytest.mark.parametrize("query", ["分别解释退款，但不要解释任何原因。", "不要不解释支付原因。"])
def test_v6_conflicting_constraints_fail_before_admission(setup, query):
    check_admission(setup, query)
