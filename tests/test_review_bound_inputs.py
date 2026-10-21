"""Existing fixed workflow consumes the same immutable input as diagnosis."""

import copy
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.diagnosis.inputs import create_bundle
from app.research.schemas import TaskRequest
from app.research.tools import ToolGateway
from app.storage.models import CommerceDataset, Task, User, utcnow
from app.storage.repository import BudgetExceeded, Conflict, NotFound
from app.tasks.worker import Worker, checkpoint_store
from tests.conftest import authenticate
from tests.test_diagnosis_tools import bundle_request, committed, oracle_payload


def review_task(setup, *, skus=None, payload=None, **changes):
    settings, repo, user, _ = setup
    settings.lease_seconds = 30
    dataset = committed(repo, user, payload)
    bundle = create_bundle(repo, user, bundle_request(dataset["id"], allowed_skus=skus or []))
    request = TaskRequest(
        conversation_id=repo.create_conversation(user.id),
        profile="commerce_review",
        query="核对固定两期的净销售额和退款记账事实",
        strategy="workflow",
        sources=["sales"],
        bundle_id=bundle["bundle_id"],
        **changes,
    )
    task = repo.create_task(user, request, str(uuid4()))
    return task, bundle, request


async def test_fixed_and_agent_workers_use_identical_explicit_period_scope_and_source(setup):
    settings, repo, user, _ = setup
    fixed, bundle, _ = review_task(setup, skus=["B"])
    diagnosis = repo.create_task(
        user,
        TaskRequest(
            conversation_id=repo.create_conversation(user.id),
            profile="commerce_diagnosis",
            query="净销售额变化涉及哪些商品，退款记账变化如何",
            bundle_id=bundle["bundle_id"],
        ),
        str(uuid4()),
    )
    async with checkpoint_store(settings) as saver:
        worker = Worker(repo, settings, saver)
        assert await worker.run_once() and await worker.run_once()
    left, right = repo.get_task(fixed["id"]), repo.get_task(diagnosis["id"])
    assert left["status"] == right["status"] == "degraded", (left, right)
    assert left["validation"]["passed"] and right["validation"]["passed"]
    a = repo.evidence(fixed["id"])[0]["payload"]
    b = next(
        e["payload"]
        for e in repo.evidence(diagnosis["id"])
        if e["payload"]["tool"] == "compare_periods"
    )
    assert a["bundle_id"] == b["bundle_id"] == bundle["bundle_id"]
    assert a["bundle_hash"] == b["bundle_hash"] == bundle["bundle_hash"]
    assert a["source_hashes"] == b["source_hashes"] == {"sales": bundle["sales_content_hash"]}
    assert a["scope"] == b["scope"] == {"skus": ["B"]}
    assert (
        a["metrics"]["net_revenue"] == b["fact_catalog"]["net_revenue.current"]["value"] == "700.00"
    )
    assert (
        a["metrics"]["net_revenue_delta"]
        == b["fact_catalog"]["net_revenue.delta"]["value"]
        == "200.00"
    )
    assert a["previous_period"]["net_revenue"] == "500.00"
    assert a["previous_period"]["start"] == "2026-08-01"
    assert [r["entity"] for r in a["dimensions"]["products"]] == ["B"]
    assert left["parameters"]["budget"] == right["parameters"]["budget"]
    assert left["graph_version"] == "commerce-review-bundle-v1"
    assert len(repo.artifacts(fixed["id"])) == len(repo.artifacts(diagnosis["id"])) == 5


async def test_authorized_sku_with_no_rows_is_not_rejected_by_filtered_dataset_schema(setup):
    payload = oracle_payload()
    payload.daily_sales = [r for r in payload.daily_sales if r.sku == "B"]
    _, repo, _, _ = setup
    task, _, _ = review_task(setup, skus=["A"], payload=payload)
    ctx = repo.claim()
    result = await ToolGateway(repo, setup[0], ctx, task["parameters"]).metrics("sales")
    source = result["evidence"][0]["payload"]
    assert source["coverage"] == {
        "expected_records": 1,
        "observed_records": 0,
        "missing_records": 1,
        "complete": False,
    }
    assert not source["previous_period"]["comparable"]
    assert "net_revenue_delta" not in source["metrics"]
    assert any("不能据此认定真实销售为零" in text for text in source["limitations"])
    assert source["scope"]["skus"] == ["A"]


@pytest.mark.parametrize("profile", ["commerce_review", "commerce_diagnosis"])
def test_idempotent_bound_retry_keeps_original_budget_after_server_limits_change(setup, profile):
    settings, repo, user, _ = setup
    settings.max_tool_calls, settings.max_model_calls = 3, 4
    settings.max_runtime_seconds = 60
    sales = committed(repo, user)
    bundle = create_bundle(repo, user, bundle_request(sales["id"]))
    request = TaskRequest(
        conversation_id=repo.create_conversation(user.id),
        query="核对退款记账变化",
        profile=profile,
        bundle_id=bundle["bundle_id"],
        strategy="workflow" if profile == "commerce_review" else "diagnostic",
        sources=["sales"],
    )
    key = str(uuid4())
    before = repo.create_task(user, request, key)
    settings.max_tool_calls, settings.max_model_calls, settings.max_runtime_seconds = 20, 24, 600
    after = repo.create_task(user, request, key)
    assert (
        after["id"] == before["id"]
        and after["parameters"]["budget"] == before["parameters"]["budget"]
    )
    ctx = repo.claim()
    with repo.session() as db:
        row = db.get(Task, before["id"])
        assert 55 <= (row.deadline_at - utcnow()).total_seconds() <= 60
    for _ in range(3):
        repo.consume(ctx, "tool")
    with pytest.raises(BudgetExceeded):
        repo.consume(ctx, "tool")
    with pytest.raises(Conflict):
        repo.create_task(user, request.model_copy(update={"query": "不同问题"}), key)


@pytest.mark.parametrize("change", ["period", "dataset", "owner"])
def test_bound_review_rejects_inconsistent_or_foreign_request_without_task(setup, change):
    _, repo, user, other = setup
    sales = committed(repo, user)
    bundle = create_bundle(repo, user, bundle_request(sales["id"]))
    params = {
        "conversation_id": repo.create_conversation(user.id),
        "query": "核对经营事实",
        "profile": "commerce_review",
        "strategy": "workflow",
        "sources": ["sales"],
        "bundle_id": bundle["bundle_id"],
    }
    acting = user
    if change == "period":
        params["start_date"] = "2026-09-02"
    elif change == "dataset":
        params["dataset_id"] = str(uuid4())
    else:
        acting = other
    with pytest.raises(NotFound if change == "owner" else ValueError):
        repo.create_task(acting, TaskRequest(**params), str(uuid4()))
    with repo.session() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0


async def test_baseline_cache_and_model_start_reauthorize_but_inflight_usage_is_retained(setup):
    settings, repo, user, _ = setup
    task, bundle, _ = review_task(setup)
    ctx = repo.claim()
    tools = ToolGateway(repo, settings, ctx, task["parameters"])
    await tools.metrics("sales")
    repo.consume(ctx, "model")
    with repo.session() as db:
        db.get(CommerceDataset, bundle["sales_dataset_id"]).committed = False
        db.commit()
    with pytest.raises(NotFound):
        await tools.metrics("sales")
    with pytest.raises(NotFound):
        repo.consume(ctx, "model")
    with pytest.raises(NotFound):
        repo.get_task(task["id"], user.id)
    assert repo.record_usage(ctx, {"input_tokens": 10, "output_tokens": 2})
    actual = repo.get_task(task["id"])
    assert actual["metrics"]["tool_calls"] == actual["metrics"]["model_calls"] == 1
    assert actual["metrics"]["input_tokens"] == 10 and actual["metrics"]["output_tokens"] == 2


async def test_revoked_baseline_fails_inside_worker_without_crashing_dispatcher(setup):
    settings, repo, _, _ = setup
    task, bundle, _ = review_task(setup)
    with repo.session() as db:
        db.get(CommerceDataset, bundle["sales_dataset_id"]).committed = False
        db.commit()
    async with checkpoint_store(settings) as saver:
        assert await Worker(repo, settings, saver).run_once()
    done = repo.get_task(task["id"])
    assert done["status"] == "failed" and done["error_code"] == "NotFound"
    assert done["metrics"]["tool_calls"] == done["metrics"]["model_calls"] == 0


async def test_bound_review_metadata_version_gate_and_api_download_permissions(client, setup):
    settings, repo, user, _ = setup
    task, bundle, _ = review_task(setup)
    with repo.session() as db:
        db.get(Task, task["id"]).graph_version = "incompatible-review-input"
        db.commit()
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    assert repo.get_task(task["id"])["error_code"] == "unsupported_graph_version"
    valid, _, _ = review_task(setup)
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    authenticate(client)
    assert client.get(f"/api/tasks/{valid['id']}").status_code == 200
    artifact = repo.artifacts(valid["id"])[0]
    with repo.session() as db:
        db.get(CommerceDataset, bundle["sales_dataset_id"]).committed = False
        db.commit()
    for suffix in ("", "/evidence", "/events", "/stream"):
        assert client.get(f"/api/tasks/{valid['id']}" + suffix).status_code == 404
    assert client.get("/api/artifacts/" + artifact["id"]).status_code == 404


async def test_bundle_tool_cannot_replace_server_period_from_constructor_parameters(setup):
    settings, repo, _, _ = setup
    task, _, _ = review_task(setup)
    ctx = repo.claim()
    wrong = copy.deepcopy(task["parameters"])
    wrong["start_date"] = "2026-08-01"
    with pytest.raises(Conflict):
        await ToolGateway(repo, settings, ctx, wrong).metrics("sales")
    assert repo.evidence(task["id"]) == []


def test_current_tenant_and_context_identity_are_part_of_baseline_source_permission(setup):
    _, repo, user, _ = setup
    task, _, _ = review_task(setup)
    ctx = repo.claim()
    with repo.session() as db:
        db.get(User, user.id).tenant_id = "moved-tenant"
        db.commit()
    with pytest.raises(NotFound):
        repo.get_task(task["id"], user.id)
    with pytest.raises(NotFound):
        repo.consume(ctx, "tool")


def test_legacy_review_default_comparison_remains_adjacent(setup):
    from app.commerce.review import calculate_review

    raw = oracle_payload().model_dump(mode="json")
    result = calculate_review(
        raw,
        date(2026, 9, 1),
        date(2026, 9, 1),
        dataset_id="legacy",
        content_hash="0" * 64,
        sample=True,
    )
    assert result["previous_period"]["start"] == result["previous_period"]["end"] == "2026-08-31"
    assert not result["previous_period"]["comparable"]


def test_bound_idempotency_does_not_reuse_corrupted_stored_parameters(setup):
    _, repo, user, _ = setup
    task, _, request = review_task(setup)
    with repo.session() as db:
        row = db.get(Task, task["id"])
        key = row.idempotency_key
        row.parameters = {
            **row.parameters,
            "budget": {**row.parameters["budget"], "max_tool_calls": 999},
        }
        db.commit()
    with pytest.raises(Conflict):
        repo.create_task(user, request, key)
