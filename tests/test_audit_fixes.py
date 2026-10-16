import asyncio
import copy
import subprocess
import sys
import time
from datetime import date, timedelta

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

import app.research.graph as graph_module
from app.commerce.service import add_document, inventory_risks
from app.research.context import evidence_package
from app.research.evidence import deterministic_report, validate_report
from app.research.schemas import ResearchReport
from app.research.tools import ToolGateway
from app.storage.models import DailySale, Evidence, Product, utcnow
from app.tasks.worker import Worker, checkpoint_store
from tests.conftest import expire_lease, new_task


@pytest.mark.parametrize("source", ["sales", "inventory"])
async def test_sigkill_after_metric_commit_recovers_original_snapshot(setup, source):
    settings, repo, *_ = setup
    task = new_task(setup, sources=[source])
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.fault_worker", str(settings.data_dir), "evidence-committed"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        marker = settings.data_dir / "fault-evidence-committed.json"
        deadline = time.monotonic() + 15
        while not marker.exists() and time.monotonic() < deadline:
            assert process.poll() is None
            await asyncio.sleep(0.05)
        assert marker.exists()
        before = repo.evidence(task["id"])[0]
        process.kill()
        await asyncio.to_thread(process.wait, 5)
        with repo.session() as db:
            product = db.scalar(
                select(Product).where(Product.tenant_id == "demo", Product.sku == "CUP-01")
            )
            product.stock += 10
            sale = db.scalar(
                select(DailySale).where(
                    DailySale.tenant_id == "demo", DailySale.day == date(2026, 9, 1)
                )
            )
            sale.revenue_cents += 100
            db.commit()
        expire_lease(repo, task["id"])
        async with checkpoint_store(settings) as saver:
            await Worker(repo, settings, saver).run_once()
        assert repo.get_task(task["id"])["status"] == "degraded"
        assert [e["id"] for e in repo.evidence(task["id"])] == [before["id"]]
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()


@pytest.mark.parametrize("source", ["sales", "inventory"])
async def test_recovery_reuses_first_metrics_after_business_data_changes(setup, source):
    settings, repo, *_ = setup
    task = new_task(setup, sources=[source])
    ctx = repo.claim()
    first = await ToolGateway(repo, settings, ctx, task["parameters"]).metrics(source)
    original = first["evidence"][0]
    with repo.session() as db:
        if source == "sales":
            sale = db.scalar(
                select(DailySale).where(
                    DailySale.tenant_id == "demo", DailySale.day == date(2026, 9, 1)
                )
            )
            sale.revenue_cents += 100
        else:
            product = db.scalar(
                select(Product).where(Product.tenant_id == "demo", Product.sku == "CUP-01")
            )
            product.stock += 10
        db.commit()
    repo.release(ctx)
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    done = repo.get_task(task["id"])
    assert done["status"] == "degraded", done["error_code"]
    assert done["validation"]["passed"]
    saved = repo.evidence(task["id"])
    assert len(saved) == 1
    assert saved[0]["id"] == original["id"]
    assert saved[0]["payload"]["metrics"] == original["payload"]["metrics"]
    assert len(repo.artifacts(task["id"])) == 5


async def test_legacy_metric_versions_remain_in_ledger_but_only_first_is_authoritative(setup):
    settings, repo, *_ = setup
    task = new_task(setup, sources=["sales"])
    ctx = repo.claim()
    original = (await ToolGateway(repo, settings, ctx, task["parameters"]).metrics("sales"))[
        "evidence"
    ][0]
    payload = copy.deepcopy(original["payload"])
    payload["metrics"]["net_revenue"] = "83767.00"
    payload["fact_catalog"]["net_revenue"]["value"] = "83767.00"
    with repo.session() as db:
        db.add(
            Evidence(
                id="E-legacy-newer-version",
                task_id=task["id"],
                source_key="legacy-newer-version",
                kind="metrics",
                title="later snapshot",
                locator=original["locator"],
                excerpt="later snapshot",
                payload=payload,
                content_hash="legacy-hash",
                created_at=utcnow() + timedelta(seconds=1),
            )
        )
        db.commit()
    ledger = repo.evidence(task["id"])
    assert len(ledger) == 2
    report = deterministic_report(ledger, [])
    assert validate_report(report, ledger)["passed"]
    package = evidence_package(ledger)
    assert [e["id"] for e in package["sources"]] == [original["id"]]
    assert (
        package["manifest"]["exclusion_reasons"]["E-legacy-newer-version"] == "superseded_metrics"
    )
    claim = report.sections[0].claims[0]
    claim.evidence_ids = ["E-legacy-newer-version"]
    claim.metrics[0].evidence_id = "E-legacy-newer-version"
    assert not validate_report(report, ledger)["passed"]


async def test_invalid_deterministic_fallback_terminates_without_graph_recursion(
    setup, monkeypatch
):
    monkeypatch.setattr(
        graph_module,
        "deterministic_report",
        lambda *_: ResearchReport(title="invalid fallback", sections=[]),
    )
    settings, repo, *_ = setup
    task = new_task(setup, sources=["sales"])
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    done = repo.get_task(task["id"])
    assert done["status"] == "failed"
    assert done["error_code"] == "report_validation_failed"
    events = repo.events(task["id"])
    assert sum(event["kind"] == "draft_rejected" for event in events) == 2
    assert any(event["kind"] == "report_validation_failed" for event in events)


@pytest.mark.parametrize(
    "stock,units,lead,risk",
    [
        (999, 1000, 1, "stockout"),
        (1000, 1000, 1, "normal"),
        (1001, 1000, 1, "normal"),
        (60001, 1000, 1, "overstock"),
        (60000, 1000, 1, "normal"),
    ],
)
def test_inventory_risk_uses_unrounded_ratio(setup, stock, units, lead, risk):
    _, repo, *_ = setup
    day = date(2026, 9, 30)
    with repo.session() as db:
        product = db.scalar(
            select(Product).where(Product.sku == "CUP-01", Product.tenant_id == "demo")
        )
        product.stock, product.lead_days = stock, lead
        sale = db.scalar(
            select(DailySale).where(DailySale.product_id == product.id, DailySale.day == day)
        )
        sale.paid_units, sale.refunded_units = units, 0
        db.commit()
    result = inventory_risks(repo, "demo", day, day)
    cup = next(item for item in result["items"] if item["sku"] == "CUP-01")
    assert cup["risk"] == risk
    if stock == 999:
        assert cup["coverage_days"] == "1.00"
        assert result["items"][0]["sku"] == "CUP-01"
        assert any("未舍入" in note for note in result["limitations"])


@pytest.mark.parametrize("remote", ["empty", "unconfigured", "failed"])
async def test_ragflow_preserves_private_attachments_without_other_scope_leaks(setup, remote):
    settings, repo, user, other = setup
    task = new_task(setup, sources=["documents"])
    marker = "蓝鲸附件独特标识"
    own = add_document(
        repo, user.tenant_id, "own.md", [("lines=1-1", marker)], user.id, task["conversation_id"]
    )
    add_document(
        repo,
        user.tenant_id,
        "other-conversation.md",
        [("lines=1-1", marker)],
        user.id,
        repo.create_conversation(user.id),
    )
    add_document(
        repo,
        user.tenant_id,
        "other-user.md",
        [("lines=1-1", marker)],
        other.id,
        task["conversation_id"],
    )
    organization = add_document(repo, user.tenant_id, "local-org.md", [("lines=1-1", marker)])
    settings.knowledge_provider = "ragflow"
    if remote != "unconfigured":
        settings.ragflow_api_url = "https://offline-adapter.invalid"
        settings.ragflow_api_key = SecretStr("offline-only")
        settings.ragflow_datasets = {user.tenant_id: ["allowed"]}
    gateway = ToolGateway(repo, settings, repo.claim(), task["parameters"])

    async def response(*_):
        if remote == "failed":
            raise httpx.ConnectError("private provider message")
        return {"code": 0, "data": {"chunks": []}}

    gateway._post = response
    result = await gateway.documents(marker)
    assert result["status"] == "ok"
    assert len(result["evidence"]) == 1
    source = result["evidence"][0]
    assert source["payload"]["document_id"] == own
    assert organization not in source["locator"]
    assert source["payload"]["provider"] == "local"
    assert result["limitations"] and "RAGFlow" in " ".join(result["limitations"])
    assert "private provider message" not in str(repo.events(task["id"]))


@pytest.mark.parametrize("format", ["md", "pdf", "docx", "xlsx"])
async def test_document_quota_uses_stable_document_identity(setup, format):
    settings, repo, user, _ = setup
    task = new_task(setup, sources=["documents"])
    text = "蓝鲸独特规则" * 300
    if format == "docx":
        fragments = [("paragraphs-and-tables", text * 3)]
    else:
        prefix = {"md": "lines", "pdf": "page", "xlsx": "sheet"}[format]
        fragments = [(f"{prefix}={i}", text) for i in range(5)]
    document_id = add_document(repo, user.tenant_id, f"rules.{format}", fragments)
    result = await ToolGateway(repo, settings, repo.claim(), task["parameters"]).documents(
        "蓝鲸独特规则"
    )
    package = evidence_package(result["evidence"], "蓝鲸独特规则")
    assert len(package["sources"]) == 2
    assert all(source["payload"]["document_id"] == document_id for source in package["sources"])
    assert "document_quota" in package["manifest"]["exclusion_reasons"].values()


def test_legacy_pdf_identity_and_distinct_ragflow_datasets_are_not_merged():
    def source(i, locator, payload=None):
        return {
            "id": f"E-{i}",
            "kind": "document",
            "title": "rules",
            "locator": locator,
            "excerpt": "需要人工复核",
            "payload": payload or {},
            "content_hash": "hash",
            "retrieved_at": "2026-10-16",
        }

    legacy = [source(i, f"document://one/page={i};chars=0-10") for i in range(5)]
    package = evidence_package(legacy)
    assert len(package["sources"]) == 2
    assert package["manifest"]["inferred_identity_ids"]
    remote = [
        source(
            f"{dataset}-{i}",
            f"ragflow://{dataset}/same/{i}",
            {
                "provider": "ragflow",
                "dataset_id": dataset,
                "document_id": "same",
                "chunk_id": str(i),
            },
        )
        for dataset in ["a", "b"]
        for i in range(3)
    ]
    assert len(evidence_package(remote)["sources"]) == 4
