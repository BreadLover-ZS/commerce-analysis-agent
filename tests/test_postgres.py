"""Requires a disposable PostgreSQL server; each test uses its own random schema."""

import asyncio
import os
import secrets
from datetime import date, timedelta
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from langgraph.checkpoint.base import empty_checkpoint
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.engine import make_url

from app.commerce.service import inventory_risks, sales_summary, seed_commerce
from app.core.auth import bootstrap_user
from app.core.config import Settings
from app.research.schemas import TaskRequest
from app.research.tools import ToolGateway
from app.storage.models import DailySale, Product, Task, User, utcnow
from app.storage.repository import BudgetExceeded, Repository, StaleLease
from app.tasks.checkpoints import LeaseFencedSaver
from app.tasks.worker import Worker, checkpoint_store

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"), reason="未提供隔离 PostgreSQL 测试服务"
)


@pytest.mark.parametrize("source", ["sales", "inventory"])
async def test_postgres_recovery_pins_metrics_despite_changed_business_data(postgres_setup, source):
    settings, repo, user = postgres_setup
    request = TaskRequest(
        conversation_id=repo.create_conversation(user.id),
        query="恢复期间指标快照",
        sources=[source],
    )
    task = repo.create_task(user, request, str(uuid4()))
    ctx = repo.claim()
    first = (await ToolGateway(repo, settings, ctx, task["parameters"]).metrics(source))[
        "evidence"
    ][0]
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
    assert repo.get_task(task["id"])["status"] == "degraded"
    assert [e["id"] for e in repo.evidence(task["id"])] == [first["id"]]


async def test_postgres_first_metric_writer_wins_atomically(postgres_setup):
    _, repo, user = postgres_setup
    task = repo.create_task(
        user,
        TaskRequest(conversation_id=repo.create_conversation(user.id), query="并发指标登记"),
        str(uuid4()),
    )
    ctx = repo.claim()

    def store(value):
        return repo.add_evidence(
            ctx,
            "metrics",
            "sales",
            "commerce://sales",
            str(value),
            {
                "tool": "sales",
                "period": {"start": "2026-09-01", "end": "2026-09-30"},
                "value": value,
            },
        )

    results = await asyncio.gather(asyncio.to_thread(store, 1), asyncio.to_thread(store, 2))
    assert results[0]["id"] == results[1]["id"]
    assert results[0]["payload"] == results[1]["payload"]
    assert len(repo.evidence(task["id"])) == 1


@pytest.mark.parametrize("stock,risk", [(999, "stockout"), (60001, "overstock")])
def test_postgres_inventory_uses_exact_threshold_comparisons(postgres_setup, stock, risk):
    _, repo, _ = postgres_setup
    day = date(2026, 9, 30)
    with repo.session() as db:
        product = db.scalar(
            select(Product).where(Product.tenant_id == "demo", Product.sku == "CUP-01")
        )
        product.stock, product.lead_days = stock, 1
        sale = db.scalar(
            select(DailySale).where(DailySale.product_id == product.id, DailySale.day == day)
        )
        sale.paid_units, sale.refunded_units = 1000, 0
        db.commit()
    result = inventory_risks(repo, "demo", day, day)
    cup = next(item for item in result["items"] if item["sku"] == "CUP-01")
    assert cup["risk"] == risk
    assert any("未舍入" in note for note in result["limitations"])


@pytest.fixture
def postgres_setup(tmp_path):
    base = os.environ["TEST_POSTGRES_URL"]
    schema = "test_" + uuid4().hex
    role = "role_" + uuid4().hex
    password = secrets.token_hex(16)
    admin = create_engine(base)
    with admin.begin() as db:
        db.execute(
            text(
                f"CREATE ROLE \"{role}\" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '{password}'"
            )
        )
        db.execute(text(f'CREATE SCHEMA "{schema}" AUTHORIZATION "{role}"'))
    url = (
        make_url(base)
        .set(username=role, password=password)
        .update_query_dict({"options": "-csearch_path=" + schema})
        .render_as_string(hide_password=False)
    )
    settings = Settings(
        _env_file=None,
        app_env="test",
        diagnosis_runtime="legacy",
        database_url=url,
        data_dir=tmp_path,
    )
    config = Config("alembic.ini")
    config.attributes["settings"] = settings
    command.upgrade(config, "head")
    repo = Repository(settings)
    bootstrap_user(repo, settings)
    seed_commerce(repo)
    with repo.session() as db:
        user = db.scalar(select(User).where(User.email == "demo@example.com"))
    yield settings, repo, user
    repo.engine.dispose()
    with admin.begin() as db:
        db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        db.execute(text(f'DROP ROLE "{role}"'))
    admin.dispose()


async def test_postgres_queue_row_locks_checkpoints_and_business_read_transaction(postgres_setup):
    settings, repo, user = postgres_setup
    with repo.session() as db:
        assert db.execute(
            text(
                "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = current_user"
            )
        ).one() == (False, False, False)
    request = TaskRequest(
        conversation_id=repo.create_conversation(user.id),
        query="分析经营数据",
        sources=["sales", "inventory"],
        require_review=True,
    )
    task = repo.create_task(user, request, str(uuid4()))
    claims = await asyncio.gather(*[asyncio.to_thread(repo.claim) for _ in range(4)])
    leases = [c for c in claims if c]
    assert len(leases) == 1
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).process(leases[0])
    waiting = repo.get_task(task["id"])
    assert waiting["status"] == "waiting_review"
    assert waiting["report"]["sections"][0]["claims"][0]["metrics"][0]["value"] == "83766.00"
    repo.review(task["id"], user.id, True)
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    assert repo.get_task(task["id"])["status"] in {"succeeded", "degraded"}
    assert len(repo.artifacts(task["id"])) == 5


async def test_postgres_concurrent_budget_updates_are_not_lost(postgres_setup):
    settings, repo, user = postgres_setup
    task = repo.create_task(
        user,
        TaskRequest(conversation_id=repo.create_conversation(user.id), query="分析库存"),
        str(uuid4()),
    )
    ctx = repo.claim()
    await asyncio.gather(*[asyncio.to_thread(repo.consume, ctx, "tool") for _ in range(8)])
    assert repo.get_task(task["id"])["metrics"]["tool_calls"] == 8
    repo.finish(ctx, "cancelled", 0)


@pytest.mark.parametrize("invalid_count", [-100, 2_147_483_648])
def test_postgres_invalid_usage_never_credits_or_overflows_database(postgres_setup, invalid_count):
    _, repo, user = postgres_setup
    task = repo.create_task(
        user,
        TaskRequest(conversation_id=repo.create_conversation(user.id), query="验证用量回执"),
        str(uuid4()),
    )
    ctx = repo.claim()
    repo.consume(ctx, "model")
    assert not repo.record_usage(ctx, {"input_tokens": invalid_count, "output_tokens": 5})
    metrics = repo.get_task(task["id"])["metrics"]
    assert not metrics["usage_complete"]
    assert metrics["input_tokens"] == metrics["output_tokens"] == 0
    with pytest.raises(BudgetExceeded):
        repo.consume(ctx, "model")
    repo.finish(ctx, "cancelled", 0)


async def test_postgres_checkpoint_write_lock_serializes_takeover(postgres_setup, monkeypatch):
    settings, repo, user = postgres_setup
    task = repo.create_task(
        user,
        TaskRequest(conversation_id=repo.create_conversation(user.id), query="验证检查点接管"),
        str(uuid4()),
    )
    old = repo.claim()
    config = {"configurable": {"thread_id": task["id"], "checkpoint_ns": ""}}
    entered, release = asyncio.Event(), asyncio.Event()
    async with checkpoint_store(settings) as raw:
        original = raw.aput

        async def delayed_put(*args):
            entered.set()
            await release.wait()
            return await original(*args)

        monkeypatch.setattr(raw, "aput", delayed_put)
        async with LeaseFencedSaver(raw, repo, old) as guarded:
            with repo.session() as db:
                db.get(Task, task["id"]).lease_until = utcnow() + timedelta(seconds=0.5)
                db.commit()
            writing = asyncio.create_task(
                guarded.aput(config, empty_checkpoint(), {"source": "update", "step": 0}, {})
            )
            try:
                await asyncio.wait_for(entered.wait(), timeout=5)
                await asyncio.sleep(0.6)
                assert await asyncio.to_thread(repo.claim) is None
            finally:
                release.set()
                saved = await writing
            current = await asyncio.to_thread(repo.claim)
            assert current is not None and current.lease_token != old.lease_token
            with pytest.raises(StaleLease):
                await guarded.aput_writes(saved, [("late", "old-worker")], "late-node")
            result = await raw.aget_tuple(config)
            assert result.pending_writes == []
    repo.finish(current, "cancelled", 0)


def test_postgres_partial_day_and_priority_before_limit(postgres_setup):
    _, repo, _ = postgres_setup
    with repo.session() as db:
        cup = db.scalar(select(Product).where(Product.tenant_id == "demo", Product.sku == "CUP-01"))
        db.execute(
            delete(DailySale).where(
                DailySale.product_id == cup.id, DailySale.day == date(2026, 9, 30)
            )
        )
        for i in range(105):
            product = Product(
                id=str(uuid4()),
                tenant_id="demo",
                sku=f"PG-{i:03}",
                name="正常商品",
                category="测试",
                price_cents=100,
                stock=25,
                lead_days=1,
                stock_as_of=date(2026, 9, 30),
                sample=True,
            )
            db.add(product)
            db.flush()
            db.add(
                DailySale(
                    tenant_id="demo",
                    product_id=product.id,
                    day=date(2026, 9, 30),
                    paid_units=1,
                    refunded_units=0,
                    revenue_cents=100,
                    refund_cents=0,
                    sample=True,
                )
            )
        critical = Product(
            id=str(uuid4()),
            tenant_id="demo",
            sku="ZZZ-PG-RISK",
            name="缺货商品",
            category="测试",
            price_cents=100,
            stock=0,
            lead_days=7,
            stock_as_of=date(2026, 9, 30),
            sample=True,
        )
        db.add(critical)
        db.flush()
        db.add(
            DailySale(
                tenant_id="demo",
                product_id=critical.id,
                day=date(2026, 9, 30),
                paid_units=1,
                refunded_units=0,
                revenue_cents=100,
                refund_cents=0,
                sample=True,
            )
        )
        db.commit()
    result = inventory_risks(repo, "demo", date(2026, 9, 30), date(2026, 9, 30))
    assert result["truncated"] and any(i["sku"] == "ZZZ-PG-RISK" for i in result["items"])
    cup = next(i for i in result["items"] if i["sku"] == "CUP-01")
    assert cup["missing_days"] == 1 and cup["coverage_days"] is None
    sales = sales_summary(repo, "demo", date(2026, 9, 30), date(2026, 9, 30))
    assert not sales["coverage"]["complete"] and sales["coverage"]["missing_records"] == 1


async def test_postgres_dataset_profile_migration_version_and_worker(postgres_setup):
    from app.commerce.datasets import commit_dataset, list_datasets, scenario_payload, stage_dataset
    from tests.test_commerce_review import request

    settings, repo, user = postgres_setup
    payload = scenario_payload("normal")

    def stage():
        return stage_dataset(repo, user, payload, "并发经营导入", True, "0" * 64)

    infos = await asyncio.gather(*(asyncio.to_thread(stage) for _ in range(4)))
    assert len({i["id"] for i in infos}) == 1
    dataset = commit_dataset(repo, user, infos[0]["id"])
    assert len(list_datasets(repo, user)) == 1
    task = repo.create_task(user, request(repo, user, dataset["id"]), str(uuid4()))
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    done = repo.get_task(task["id"])
    assert done["validation"]["passed"] and done["status"] == "degraded"
    assert repo.evidence(task["id"])[0]["payload"]["dataset_hash"] == dataset["content_hash"]
