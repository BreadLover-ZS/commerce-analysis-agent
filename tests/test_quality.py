import json
from datetime import date
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import delete, select

from app.commerce.service import inventory_risks, sales_summary, search_documents
from app.research.context import evidence_package
from app.research.evidence import (
    deterministic_report,
    render_bound_metrics,
    unique_limitations,
    validate_report,
)
from app.research.schemas import Claim, MetricReference, ResearchReport, ResearchSynthesis
from app.research.synthesis import validate_synthesis
from app.research.tools import ToolGateway
from app.storage.models import Chunk, DailySale, Document, Product
from app.tasks.worker import Worker, checkpoint_store
from tests.conftest import new_task
from tests.fake_model import ScriptedCommerceModel


def test_limitation_dedup_preserves_negation_numbers_and_internal_space():
    notes = ["已有库存。", "已有库存", "未有库存。", "阈值为60天。", "阈值为61天。", "1 0", "10"]
    assert unique_limitations(notes) == [notes[0], *notes[2:]]


@pytest.mark.parametrize(
    "dimension,value",
    [
        ("entity_id", "BAG-02"),
        ("unit", "percent"),
        ("period_start", "2026-08-01"),
        ("period_end", "2026-10-01"),
        ("snapshot_as_of", "2026-01-01"),
        ("value", "180.00"),
    ],
)
async def test_metric_tuple_rejects_entity_unit_period_snapshot_and_value(setup, dimension, value):
    settings, repo, *_ = setup
    task = new_task(setup, sources=["inventory"])
    ctx = repo.claim()
    await ToolGateway(repo, settings, ctx, task["parameters"]).metrics("inventory")
    evidence = repo.evidence(task["id"])
    report = deterministic_report(evidence, [])
    assert validate_report(report, evidence)["passed"]
    claim = report.sections[1].claims[0]
    assert claim.metrics[0].entity_id == "CUP-01"
    setattr(claim.metrics[0], dimension, value)
    assert not validate_report(report, evidence)["passed"]
    render_bound_metrics(report, evidence)
    assert not validate_report(report, evidence)["passed"]


async def test_correct_number_with_wrong_product_prose_is_rejected(setup):
    settings, repo, *_ = setup
    task = new_task(setup, sources=["inventory"])
    ctx = repo.claim()
    await ToolGateway(repo, settings, ctx, task["parameters"]).metrics("inventory")
    evidence = repo.evidence(task["id"])
    report = deterministic_report(evidence, [])
    bag = next(c for c in report.sections[1].claims if c.metrics[0].entity_id == "BAG-02")
    bag.text = "保温杯库存覆盖天数为180.00天。"
    assert not validate_report(report, evidence)["passed"]
    # Rendering replaces the model's prose with the verified tuple's actual entity.
    render_bound_metrics(report, evidence)
    assert "通勤收纳包" in bag.text and "保温杯" not in bag.text
    assert validate_report(report, evidence)["passed"]


@pytest.mark.parametrize("kind", ["analysis", "recommendation"])
async def test_non_fact_cannot_carry_even_a_valid_metric_reference(setup, kind):
    settings, repo, *_ = setup
    task = new_task(setup, sources=["inventory"])
    ctx = repo.claim()
    await ToolGateway(repo, settings, ctx, task["parameters"]).metrics("inventory")
    report = deterministic_report(repo.evidence(task["id"]), [])
    claim = report.sections[1].claims[0]
    assert Claim.model_validate(claim.model_dump()).metrics
    data = {**claim.model_dump(), "kind": kind}
    with pytest.raises(ValidationError) as caught:
        Claim.model_validate(data)
    assert caught.value.errors(include_input=False)[0]["type"] == "non_fact_metrics"
    repo.finish(ctx, "cancelled", 0)


def test_document_threshold_can_be_quoted_but_not_disguised_as_computed_metric():
    source = {
        "id": "doc-rule",
        "kind": "document",
        "excerpt": "覆盖超过60天时检查滞销。",
        "payload": {},
    }
    report = ResearchReport.model_validate(
        {
            "title": "规则核对",
            "sections": [
                {"name": "经营表现", "claims": []},
                {
                    "name": "库存风险",
                    "claims": [
                        {
                            "id": "rule",
                            "kind": "fact",
                            "text": source["excerpt"],
                            "evidence_ids": [source["id"]],
                        }
                    ],
                },
                {"name": "竞品观察", "claims": []},
                {"name": "行动建议", "claims": []},
            ],
            "limitations": ["经营表现无数据", "竞品观察无数据", "行动建议未生成"],
        }
    )
    assert validate_report(report, [source])["passed"]
    report.sections[1].claims[0].metrics = [
        MetricReference(
            evidence_id=source["id"],
            key="threshold",
            value="60",
            entity_id="demo",
            unit="days",
            period_start="2026-09-01",
            period_end="2026-09-30",
        )
    ]
    source["payload"] = {
        "metrics": {"threshold": "60"},
        "fact_catalog": {
            "threshold": {
                "value": "60",
                "entity_id": "demo",
                "unit": "days",
                "period_start": "2026-09-01",
                "period_end": "2026-09-30",
                "snapshot_as_of": None,
            }
        },
    }
    assert not validate_report(report, [source])["passed"]


def test_partial_days_and_old_snapshot_are_explicit(setup):
    _, repo, *_ = setup
    with repo.session() as db:
        cup = db.scalar(select(Product).where(Product.tenant_id == "demo", Product.sku == "CUP-01"))
        db.execute(
            delete(DailySale).where(
                DailySale.product_id == cup.id, DailySale.day == date(2026, 9, 2)
            )
        )
        cup.stock_as_of = date(2026, 8, 1)
        db.commit()
    sales = sales_summary(repo, "demo", date(2026, 9, 1), date(2026, 9, 30))
    assert sales["coverage"]["missing_records"] == 1
    assert not sales["coverage"]["complete"]
    assert "net_revenue_change_pct" not in sales["metrics"]
    risks = inventory_risks(repo, "demo", date(2026, 9, 1), date(2026, 9, 30))
    cup = next(v for v in risks["items"] if v["sku"] == "CUP-01")
    assert cup["missing_days"] == 1 and cup["coverage_days"] is None
    assert cup["risk"] == "incomplete" and not cup["snapshot_aligned"]
    assert any("不完整" in gap for gap in risks["limitations"])


def test_risk_is_ranked_before_the_hundred_product_cap(setup):
    _, repo, *_ = setup
    with repo.session() as db:
        for i in range(110):
            product = Product(
                id=str(uuid4()),
                tenant_id="demo",
                sku=f"EXTRA-{i:03}",
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
            sku="ZZZ-CRITICAL",
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
    assert result["total_products"] == 114 and result["truncated"]
    assert result["items"][0]["sku"] == "ZZZ-CRITICAL"
    assert len(result["items"]) == 100


def test_retrieval_can_find_authorized_chunk_after_old_limit(setup):
    _, repo, *_ = setup
    new_task(setup)
    ctx = repo.claim()
    with repo.session() as db:
        doc = Document(
            id=str(uuid4()), tenant_id="demo", name="扩展语料", content_hash="a" * 64, sample=True
        )
        db.add(doc)
        db.flush()
        db.add_all(
            [
                Chunk(
                    id=str(uuid4()),
                    document_id=doc.id,
                    locator=f"chunk={i}",
                    text="普通无关资料" if i < 2100 else "蓝鲸检索目标：库存交期须人工核对",
                )
                for i in range(2101)
            ]
        )
        db.commit()
    found = search_documents(repo, ctx, "蓝鲸检索目标")
    assert any("蓝鲸检索目标" in c["excerpt"] for c in found)


def test_context_balances_late_sources_and_preserves_window_offsets():
    sources = [
        {
            "id": f"E-{i}",
            "kind": "document",
            "title": "内部规则",
            "locator": f"document://doc{i}/chars=0",
            "excerpt": "规则" * 1500 + "目标关键条件：需要人工批准",
            "payload": {},
            "content_hash": "hash",
            "retrieved_at": "2026-10-14",
        }
        for i in range(20)
    ]
    sources.append(
        {
            **sources[0],
            "id": "E-web",
            "kind": "web",
            "locator": "https://example.com/contradiction",
            "excerpt": "目标关键条件：公开页面有不同限制",
        }
    )
    package = evidence_package(sources, "目标关键条件")
    assert "E-web" in package["manifest"]["selected_ids"]
    assert package["manifest"]["excluded_ids"] and package["manifest"]["truncations"]
    assert package["manifest"]["characters"] <= package["manifest"]["max_characters"]
    by_id = {s["id"]: s for s in sources}
    for source in package["sources"]:
        start, end = source["payload"]["excerpt_start"], source["payload"]["excerpt_end"]
        assert source["excerpt"] == by_id[source["id"]]["excerpt"][start:end]


def test_findings_require_real_quotes_and_distinct_conflict_sources():
    source = {
        "id": "E-a",
        "kind": "web",
        "locator": "https://example.com/a",
        "excerpt": "优惠仅适用于指定地区",
    }
    finding = {
        "id": "conflict",
        "kind": "conflict",
        "statement": "资料存在差异",
        "support": [{"evidence_id": "E-a", "quote": "优惠仅适用于指定地区"}],
    }
    synthesis = ResearchSynthesis(findings=[finding])
    assert validate_synthesis(synthesis, [source])
    second = {
        **source,
        "id": "E-b",
        "locator": "https://example.com/b",
        "excerpt": "优惠适用于全部地区",
    }
    synthesis.findings[0].support.append(
        type(synthesis.findings[0].support[0])(evidence_id="E-b", quote=second["excerpt"])
    )
    assert not validate_synthesis(synthesis, [source, second])
    synthesis.findings[0].support[0].quote = "伪造的原文摘录"
    assert validate_synthesis(synthesis, [source, second])


@pytest.mark.parametrize("strategy", ["workflow", "single", "multi"])
async def test_findings_are_forwarded_to_draft_and_exported(setup, monkeypatch, strategy):
    settings, repo, *_ = setup
    settings.agent_mode = "live"
    settings.openai_api_key = SecretStr("offline-protocol-test")
    settings.tavily_api_key = SecretStr("offline-search-test")

    async def search(*args, **kwargs):
        return {
            "results": [
                {
                    "title": "公开资料",
                    "url": "https://example.com/source",
                    "content": "保温杯采用便携定位。",
                }
            ]
        }

    monkeypatch.setattr(ToolGateway, "_post", search)
    task = new_task(setup, strategy=strategy)
    model = ScriptedCommerceModel()
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver, model).run_once()
    done = repo.get_task(task["id"])
    assert done["status"] in {"succeeded", "degraded"}
    assert model._research_forwarded
    artifact = next(a for a in repo.artifacts(task["id"]) if a["name"] == "research.json")
    with repo.session() as db:
        from app.storage.models import Artifact

        storage = db.get(Artifact, artifact["id"]).storage_key
    research = json.loads((settings.data_dir / "artifacts" / storage).read_text())
    assert research["research"]["findings"] and research["evidence_selection"]["selected_ids"]
    if strategy == "workflow":
        assert not any("read_file" in names or "task" in names for names in model._tool_sets)
