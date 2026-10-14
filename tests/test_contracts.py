import asyncio
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from pypdf import PdfReader

from app.commerce.service import inventory_risks, sales_summary, search_documents
from app.core.config import Settings
from app.core.files import safe_path
from app.research.evidence import deterministic_report, validate_report
from app.research.tools import ToolGateway
from app.storage.repository import BudgetExceeded, StaleLease
from app.tasks.worker import Worker, checkpoint_store
from tests.conftest import authenticate, expire_lease, new_task


def test_known_commerce_numbers_and_missing_period(setup):
    _, repo, _, _ = setup
    report = sales_summary(repo, "demo", date(2026, 9, 1), date(2026, 9, 30))
    # Independent hand calculation from 30 days of each SKU's fixture unit pattern.
    assert report["metrics"]["net_revenue"] == "83766.00"
    assert report["metrics"]["refund_rate_pct"] == "0.91"
    assert report["metrics"]["paid_units"] == "660"
    risks = inventory_risks(repo, "demo", date(2026, 9, 1), date(2026, 9, 30))
    assert [(i["coverage_days"], i["risk"]) for i in risks["items"]] == [
        ("1.85", "stockout"),
        ("180.00", "overstock"),
        ("25.00", "normal"),
    ]
    absent = sales_summary(repo, "demo", date(2030, 1, 1), date(2030, 1, 30))
    assert absent["row_count"] == 0 and absent["limitations"]
    assert "refund_rate_pct" not in absent["metrics"]


def test_auth_csrf_origin_and_idempotency(client):
    assert client.get("/api/tasks").status_code == 401
    assert (
        client.post(
            "/api/auth/login",
            headers={"Origin": "https://evil.example"},
            json={"email": "demo@example.com", "password": "demo-commerce-2026"},
        ).status_code
        == 403
    )
    response = authenticate(client)
    token = response.json()["csrf_token"]
    assert (
        "HttpOnly" in response.headers["set-cookie"]
        and "SameSite=strict" in response.headers["set-cookie"]
    )
    client.headers.pop("X-CSRF-Token")
    assert client.post("/api/conversations").status_code == 403
    client.headers["X-CSRF-Token"] = token
    conversation = client.post("/api/conversations").json()["id"]
    body = {"conversation_id": conversation, "query": "分析销售", "sources": ["sales"]}
    key = str(uuid4())
    first = client.post("/api/tasks", json=body, headers={"Idempotency-Key": key})
    second = client.post("/api/tasks", json=body, headers={"Idempotency-Key": key})
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert (
        client.post(
            "/api/tasks", json={**body, "query": "另一项请求"}, headers={"Idempotency-Key": key}
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/tasks",
            json={**body, "sources": ["sales", "sales"]},
            headers={"Idempotency-Key": str(uuid4())},
        ).status_code
        == 422
    )


async def test_complete_pipeline_pdf_and_cross_user_isolation(setup, client):
    settings, repo, user, other = setup
    task = new_task(setup)
    async with checkpoint_store(settings) as saver:
        assert await Worker(repo, settings, saver).run_once()
    result = repo.get_task(task["id"])
    assert result["status"] == "degraded" and result["validation"]["passed"]
    assert result["metrics"]["model_calls"] == 0
    assert len(repo.evidence(task["id"])) >= 5
    authenticate(client)
    artifacts = client.get(f"/api/tasks/{task['id']}").json()["artifacts"]
    pdf = next(a for a in artifacts if a["name"] == "report.pdf")
    response = client.get(f"/api/artifacts/{pdf['id']}")
    assert response.status_code == 200 and response.content.startswith(b"%PDF")
    from io import BytesIO

    text = "\n".join(p.extract_text() for p in PdfReader(BytesIO(response.content)).pages)
    assert "83766.00" in text and "0.91" in text
    own_events = client.get(f"/api/tasks/{task['id']}/events").json()
    replay = client.get(
        f"/api/tasks/{task['id']}/stream",
        headers={"Last-Event-ID": str(own_events["events"][0]["id"])},
    )
    assert replay.status_code == 200 and "text/event-stream" in replay.headers["content-type"]
    assert "报告已生成" in replay.text
    assert (
        client.get(f"/api/tasks/{task['id']}/events?after={own_events['cursor']}").json()["events"]
        == []
    )
    authenticate(client, other.email, "other-commerce-2026")
    for path in (
        f"/api/tasks/{task['id']}",
        f"/api/tasks/{task['id']}/evidence",
        f"/api/tasks/{task['id']}/events",
        f"/api/artifacts/{pdf['id']}",
    ):
        assert client.get(path).status_code == 404
    assert client.post(f"/api/tasks/{task['id']}/cancel").status_code == 404


def test_upload_traversal_and_scope(client, setup, tmp_path):
    _, repo, user, _ = setup
    authenticate(client)
    conversation = client.post("/api/conversations").json()["id"]
    assert (
        client.post(
            "/api/documents",
            data={"conversation_id": conversation},
            files={"files": ("../escape.md", b"private content")},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/documents",
            data={"conversation_id": conversation},
            files={"files": ("secret.md", "只对当前会话可见：暗号蓝鲸星河".encode())},
        ).status_code
        == 201
    )
    new_task(setup, sources=["documents"])
    ctx = repo.claim()
    assert not any("蓝鲸星河" in c["excerpt"] for c in search_documents(repo, ctx, "蓝鲸星河"))
    repo.finish(ctx, "cancelled", 0)
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    for candidate in ("../outside/a.md", str(outside / "a.md"), "link/a.md"):
        with pytest.raises(ValueError):
            safe_path(root, candidate)


async def test_review_survives_restart_without_repeating_tools(setup):
    settings, repo, user, _ = setup
    task = new_task(setup, require_review=True)
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    waiting = repo.get_task(task["id"])
    assert waiting["status"] == "waiting_review" and waiting["report"]
    assert not repo.artifacts(task["id"])
    repo.review(task["id"], user.id, True)
    # A new saver connection and new worker reconstruct the workflow from disk.
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    done = repo.get_task(task["id"])
    assert done["status"] == "degraded"
    assert done["metrics"]["tool_calls"] == waiting["metrics"]["tool_calls"]
    assert sum(e["kind"] == "planning" for e in repo.events(task["id"])) == 1


async def test_rejected_and_cancelled_tasks_do_not_export(setup):
    settings, repo, user, _ = setup
    cancelled = new_task(setup)
    repo.cancel(cancelled["id"], user.id)
    assert repo.claim() is None and repo.get_task(cancelled["id"])["status"] == "cancelled"
    rejected = new_task(setup, require_review=True)
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
        repo.review(rejected["id"], user.id, False)
        await Worker(repo, settings, saver).run_once()
    assert repo.get_task(rejected["id"])["status"] == "rejected"
    assert not repo.artifacts(rejected["id"])


async def test_budget_is_atomic_and_old_lease_cannot_write(setup):
    settings, repo, user, _ = setup
    settings.max_tool_calls = 2
    task = new_task(setup)
    ctx = repo.claim()
    responses = await asyncio.gather(
        *[asyncio.to_thread(repo.consume, ctx, "tool") for _ in range(6)], return_exceptions=True
    )
    assert sum(isinstance(r, BudgetExceeded) for r in responses) == 4
    assert repo.get_task(task["id"])["metrics"]["tool_calls"] == 2
    expire_lease(repo, task["id"])
    resumed = repo.claim()
    assert resumed.lease_token != ctx.lease_token
    with pytest.raises(StaleLease):
        repo.emit(ctx, "late", "过期写入")
    with pytest.raises(StaleLease):
        repo.add_evidence(ctx, "web", "过期", "https://example.com", "late")
    assert repo.remaining_runtime(resumed) < settings.max_runtime_seconds
    repo.finish(resumed, "cancelled", 0)


async def test_private_content_cannot_be_sent_as_public_query(setup):
    settings, repo, _, _ = setup
    task = new_task(setup)
    ctx = repo.claim()
    gateway = ToolGateway(repo, settings, ctx, task["parameters"])
    with pytest.raises(ValueError):
        await gateway.web("客户秘密订单与内部销售明细")
    assert repo.get_task(task["id"])["metrics"]["tool_calls"] == 0
    repo.finish(ctx, "cancelled", 0)


async def test_repeated_public_sources_are_deduplicated_and_hashes_match(setup):
    import hashlib

    settings, repo, user, _ = setup
    from app.research.schemas import TaskRequest

    request = TaskRequest(
        conversation_id=repo.create_conversation(user.id),
        query="比较公开资料",
        sources=["web"],
        public_queries=["保温杯 品牌 官网", "收纳包 品牌 官网"],
    )
    task = repo.create_task(user, request, str(uuid4()))
    ctx = repo.claim()
    gateway = ToolGateway(repo, settings, ctx, task["parameters"])
    for query in request.public_queries:
        await gateway.web(query)
    evidence = repo.evidence(task["id"])
    assert len(evidence) == 2
    assert all(len(e["payload"]["queries"]) == 2 for e in evidence)
    assert all(
        e["content_hash"] == hashlib.sha256(e["excerpt"].encode()).hexdigest() for e in evidence
    )
    repo.finish(ctx, "cancelled", 0)


async def test_unknown_citations_and_wrong_numbers_fail(setup):
    settings, repo, _, _ = setup
    task = new_task(setup, sources=["sales"])
    ctx = repo.claim()
    gateway = ToolGateway(repo, settings, ctx, task["parameters"])
    await gateway.metrics("sales")
    evidence = repo.evidence(task["id"])
    report = deterministic_report(evidence, [])
    assert validate_report(report, evidence)["passed"]
    blank = report.model_copy(deep=True)
    for section in blank.sections:
        section.claims = []
    blank.limitations = []
    assert not validate_report(blank, evidence)["passed"]
    negative = report.model_copy(deep=True)
    negative.sections[0].claims[0].text = "本周期净销售额为 -12345 元。"
    assert not validate_report(negative, evidence)["passed"]
    claim = report.sections[0].claims[0]
    claim.metrics[0].value = str(Decimal(claim.metrics[0].value) + 1)
    assert not validate_report(report, evidence)["passed"]
    claim.evidence_ids = ["E-fabricated"]
    assert any("未知证据" in issue for issue in validate_report(report, evidence)["issues"])
    repo.finish(ctx, "cancelled", 0)


def test_production_configuration_fails_closed():
    with pytest.raises(ValueError):
        Settings(_env_file=None, app_env="production")
    with pytest.raises(ValueError):
        Settings(_env_file=None, agent_mode="live")


async def test_import_is_idempotent_and_product_names_with_numbers_validate(setup):
    from pathlib import Path

    from app.commerce.importer import CommerceImport, import_commerce

    settings, repo, _, _ = setup
    payload = CommerceImport.model_validate_json(Path("data/import-example.json").read_text())
    payload.products[0].name = "教学2026版商品"
    import_commerce(repo, "demo", payload, sample=True)
    import_commerce(repo, "demo", payload, sample=True)
    summary = sales_summary(repo, "demo", date(2026, 9, 1), date(2026, 9, 30))
    assert summary["metrics"]["net_revenue"] == "84657.00"
    task = new_task(setup, sources=["inventory"])
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    assert repo.get_task(task["id"])["validation"]["passed"]


async def test_tool_failure_preserves_explicit_gaps(setup, monkeypatch):
    settings, repo, _, _ = setup

    async def unavailable(*_):
        raise ConnectionError("sensitive-provider-details")

    monkeypatch.setattr(ToolGateway, "documents", unavailable)
    task = new_task(setup, sources=["sales", "documents"])
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    result = repo.get_task(task["id"])
    assert result["status"] == "degraded"
    assert "ConnectionError" in str(result["report"]["limitations"])
    assert "sensitive-provider-details" not in str(repo.events(task["id"]))
