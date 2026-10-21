"""G2/G3 graph, human CAS and real SIGKILL recovery on isolated PostgreSQL."""

import asyncio
import os

import pytest
from sqlalchemy import func, select

from app.diagnosis.schemas import ClarificationAnswer
from app.diagnosis.store import DiagnosisStore
from app.storage.models import InputRequest, Task, User
from app.storage.repository import Conflict, NotFound
from app.tasks.worker import Worker, checkpoint_store
from tests.test_diagnosis_graph import (
    new_diagnosis,
)
from tests.test_diagnosis_graph import (
    test_clarification_then_exact_report_review_and_download as check_api_review,
)
from tests.test_diagnosis_graph import (
    test_diagnostic_worker_report_has_independently_expected_facts as check_worker,
)
from tests.test_diagnosis_recovery import (
    test_sigkill_committed_tool_or_artifacts_reuses_identity as check_committed,
)
from tests.test_diagnosis_recovery import (
    test_sigkill_model_windows_preserve_cost_and_reuse_success as check_model_window,
)
from tests.test_diagnosis_recovery import (
    test_sigkill_wait_record_and_checkpoint_reconcile_without_losing_input as check_wait_window,
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


async def test_postgres_diagnostic_worker_and_report_truth(setup):
    await check_worker(setup)


async def test_postgres_diagnostic_api_clarification_review_pdf(client, setup):
    await check_api_review(client, setup)


@pytest.mark.parametrize("phase", ["tool_committed", "artifact_committed"])
async def test_postgres_sigkill_success_identity_is_reused(setup, phase, monkeypatch):
    await check_committed(setup, phase, monkeypatch)


@pytest.mark.parametrize(
    "phase", ["model_before_send", "model_before_commit", "model_output_committed"]
)
async def test_postgres_sigkill_model_budget_output_and_unknown(setup, phase):
    await check_model_window(setup, phase)


@pytest.mark.parametrize("phase", ["input_before_checkpoint", "input_after_checkpoint"])
async def test_postgres_sigkill_wait_and_checkpoint_reconciliation(setup, phase):
    await check_wait_window(setup, phase)


async def test_postgres_duplicate_human_answer_and_worker_claim_advance_once(setup):
    settings, repo, user, other = setup
    task, _ = new_diagnosis(setup, "那个商品的净销售额变化", require_review=True)
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    store = DiagnosisStore(repo)
    pending = store.projection(task["id"], user)["pending_input"]
    answer = ClarificationAnswer(
        interrupt_id=pending["interrupt_id"],
        expected_version=pending["expected_version"],
        choice="A",
    )
    before = repo.get_task(task["id"])["state_version"]
    replies = await asyncio.gather(
        *(asyncio.to_thread(store.answer, task["id"], user, answer) for _ in range(4))
    )
    assert {r["state_version"] for r in replies} == {before + 1}
    with pytest.raises(NotFound):
        store.answer(task["id"], other, answer)
    with pytest.raises(Conflict):
        store.answer(task["id"], user, answer.model_copy(update={"choice": "B"}))
    contexts = await asyncio.gather(*(asyncio.to_thread(repo.claim) for _ in range(4)))
    claimed = [ctx for ctx in contexts if ctx]
    assert len(claimed) == 1
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).process(claimed[0])
    assert repo.get_task(task["id"])["status"] == "waiting_review"
    assert repo.get_task(task["id"])["metrics"]["tool_calls"] == 3
    with repo.session() as db:
        assert db.scalar(select(func.count()).select_from(InputRequest)) == 2
        assert (
            db.scalar(
                select(func.count())
                .select_from(InputRequest)
                .where(InputRequest.resolved_at.is_not(None))
            )
            == 1
        )


async def test_postgres_unknown_graph_version_is_not_loaded_into_new_builder(setup):
    settings, repo, _, _ = setup
    task, _ = new_diagnosis(setup)
    with repo.session() as db:
        db.get(Task, task["id"]).graph_version = "diagnosis-incompatible-version"
        db.commit()
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    result = repo.get_task(task["id"])
    assert result["status"] == "failed" and result["error_code"] == "unsupported_graph_version"
    assert result["metrics"]["tool_calls"] == result["metrics"]["model_calls"] == 0
