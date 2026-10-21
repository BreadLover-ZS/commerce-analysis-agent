"""G1 true PostgreSQL row locks, migrations and takeover evidence (disposable schema)."""

import asyncio
import os

import pytest
from sqlalchemy import func, select

from app.diagnosis.inputs import create_bundle
from app.diagnosis.model_calls import DiagnosisModelGateway
from app.diagnosis.tools import DiagnosisToolGateway
from app.storage.models import (
    AnalysisInputBundle,
    CommerceDataset,
    Evidence,
    ModelInvocation,
    Task,
    ToolInvocation,
    ToolQueryResult,
)
from app.storage.repository import BudgetExceeded, Conflict, NotFound, StaleLease
from tests.conftest import expire_lease
from tests.test_diagnosis_model_calls import MESSAGES, Choice, ScriptedDecisionModel
from tests.test_diagnosis_tools import bundle_request, committed, diagnosis_task
from tests.test_postgres import postgres_setup as postgres_setup

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"), reason="未提供隔离 PostgreSQL 测试服务"
)


async def test_postgres_diagnosis_bundle_dedup_and_scoped_result_uniqueness(postgres_setup):
    _, repo, user = postgres_setup
    dataset = committed(repo, user)
    inputs = await asyncio.gather(
        *(
            asyncio.to_thread(create_bundle, repo, user, bundle_request(dataset["id"]))
            for _ in range(4)
        )
    )
    assert len({item["bundle_id"] for item in inputs}) == 1
    task, ctx = diagnosis_task(repo, user, inputs[0])
    gw = DiagnosisToolGateway(repo, ctx)
    values = await asyncio.gather(
        *(
            asyncio.to_thread(
                gw.invoke, "compare_periods", {"filters": {"skus": [sku]}}, "step-" + sku
            )
            for sku in ("A", "B", "A", "B")
        )
    )
    successes = [v for v in values if v["status"] == "ok"]
    assert {v["fact_catalog"]["net_revenue.delta"]["raw_value"] for v in successes} == {
        -40000,
        20000,
    }
    for sku in ("A", "B"):
        cached = gw.invoke("compare_periods", {"filters": {"skus": [sku]}}, "repeat")
        assert cached["reused"]
    assert repo.get_task(task["id"])["metrics"]["tool_calls"] == 2
    with repo.session() as db:
        assert db.scalar(select(func.count()).select_from(AnalysisInputBundle)) == 1
        assert db.scalar(select(func.count()).select_from(ToolQueryResult)) == 2
        assert db.scalar(select(func.count()).select_from(ToolInvocation)) == 2
        assert db.scalar(select(func.count()).select_from(Evidence)) == 2


async def test_postgres_diagnosis_budget_is_atomic_across_different_specs(postgres_setup):
    _, repo, user = postgres_setup
    dataset = committed(repo, user)
    bundle = create_bundle(repo, user, bundle_request(dataset["id"]))
    task, ctx = diagnosis_task(repo, user, bundle)
    gw = DiagnosisToolGateway(repo, ctx)

    def invoke(top_k):
        try:
            return gw.invoke("rank_contributors", {"top_k": top_k}, str(top_k))
        except BudgetExceeded:
            return "budget"

    results = await asyncio.gather(*(asyncio.to_thread(invoke, k) for k in range(1, 13)))
    assert results.count("budget") == 2
    assert repo.get_task(task["id"])["metrics"]["tool_calls"] == 10
    with repo.session() as db:
        assert db.scalar(select(func.count()).select_from(ToolInvocation)) == 10
        assert db.scalar(select(func.count()).select_from(Evidence)) == 10


def test_postgres_diagnosis_late_writer_fenced_and_success_reused(postgres_setup, monkeypatch):
    _, repo, user = postgres_setup
    dataset = committed(repo, user)
    bundle = create_bundle(repo, user, bundle_request(dataset["id"]))
    task, ctx = diagnosis_task(repo, user, bundle)
    gw = DiagnosisToolGateway(repo, ctx)
    saved = gw.invoke("compare_periods", {}, "baseline")
    original = gw._calculate
    next_ctx = []

    def takeover(*args):
        value = original(*args)
        expire_lease(repo, task["id"])
        next_ctx.append(repo.claim())
        return value

    monkeypatch.setattr(gw, "_calculate", takeover)
    with pytest.raises(StaleLease):
        gw.invoke("analyze_refunds", {}, "interrupted")
    current = DiagnosisToolGateway(repo, next_ctx[0])
    replay = current.invoke("compare_periods", {}, "baseline")
    assert replay["reused"] and replay["call_id"] == saved["call_id"]
    assert current.invoke("analyze_refunds", {}, "interrupted")["status"] == "ok"
    with repo.session() as db:
        statuses = list(db.scalars(select(ToolInvocation.status)))
        assert statuses.count("UNKNOWN") == 1
        assert statuses.count("SUCCEEDED") == 2


def test_postgres_diagnosis_revoked_access_rejects_cached_result(postgres_setup):
    _, repo, user = postgres_setup
    dataset = committed(repo, user)
    bundle = create_bundle(repo, user, bundle_request(dataset["id"]))
    _, ctx = diagnosis_task(repo, user, bundle)
    gw = DiagnosisToolGateway(repo, ctx)
    gw.invoke("compare_periods", {}, "before")
    with repo.session() as db:
        db.get(CommerceDataset, dataset["id"]).committed = False
        db.commit()
    with pytest.raises(NotFound):
        gw.invoke("compare_periods", {}, "cache-after-revoke")
    with pytest.raises(NotFound):
        gw.catalog()


async def test_postgres_model_output_usage_and_identity_survive_takeover(postgres_setup):
    _, repo, user = postgres_setup
    dataset = committed(repo, user)
    bundle = create_bundle(repo, user, bundle_request(dataset["id"]))
    task, ctx = diagnosis_task(repo, user, bundle)
    model = ScriptedDecisionModel({"action": "rank"})
    before = await DiagnosisModelGateway(repo, ctx, model, "script-v1").invoke(
        Choice, MESSAGES, step_id="step-1"
    )
    expire_lease(repo, task["id"])
    current = repo.claim()
    repo.account_unreported_usage(current)
    after = await DiagnosisModelGateway(repo, current, model, "script-v1").invoke(
        Choice, MESSAGES, step_id="step-1"
    )
    assert after["reused"] and after["call_id"] == before["call_id"]
    assert len(model.requests) == 1
    metrics = repo.get_task(task["id"])["metrics"]
    assert (
        metrics["model_calls"] == 1 and metrics["input_tokens"] == 10 and metrics["usage_complete"]
    )
    with repo.session() as db:
        assert db.scalar(select(func.count()).select_from(ModelInvocation)) == 1
        assert db.get(Task, task["id"]).usage_reports == 1


async def test_postgres_model_concurrent_identity_and_unknown_reservation(postgres_setup):
    _, repo, user = postgres_setup
    dataset = committed(repo, user)
    bundle = create_bundle(repo, user, bundle_request(dataset["id"]))
    task, ctx = diagnosis_task(repo, user, bundle)
    model = ScriptedDecisionModel()
    calls = DiagnosisModelGateway(repo, ctx, model, "script-v1")

    def reserve():
        try:
            return calls._prepare(Choice, MESSAGES, "step-1", "propose", 1)
        except Conflict:
            return "in_progress"

    results = await asyncio.gather(*(asyncio.to_thread(reserve) for _ in range(4)))
    assert results.count("in_progress") == 3
    expire_lease(repo, task["id"])
    current = repo.claim()
    repo.account_unreported_usage(current)
    recovered = DiagnosisModelGateway(repo, current, model, "script-v1")
    unknown = await recovered.invoke(Choice, MESSAGES, step_id="step-1")
    assert unknown["status"] == "UNKNOWN"
    with pytest.raises(BudgetExceeded):
        await recovered.invoke(Choice, MESSAGES, step_id="step-2")
    assert model.requests == []
    with repo.session() as db:
        task_row = db.get(Task, task["id"])
        assert task_row.model_calls == task_row.usage_reports == 1
        assert not task_row.usage_complete
        assert db.scalar(select(ModelInvocation.status)) == "UNKNOWN"
