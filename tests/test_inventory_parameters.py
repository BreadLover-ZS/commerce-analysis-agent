import json
from datetime import date

import pytest
from pydantic import PrivateAttr, SecretStr
from sqlalchemy import delete, select

from app.commerce import service
from app.research.evidence import (
    deterministic_report,
    metric_reference,
    render_bound_metrics,
    validate_report,
)
from app.research.schemas import Claim
from app.research.tools import ToolGateway
from app.storage.models import DailySale, Product
from app.tasks.worker import Worker, checkpoint_store
from tests.conftest import new_task
from tests.fake_model import ScriptedCommerceModel


async def test_inventory_parameters_are_bound_even_without_sales(setup):
    settings, repo, *_ = setup
    with repo.session() as db:
        product = db.scalar(select(Product).where(Product.sku == "CUP-01"))
        product.lead_days = 23
        db.execute(delete(DailySale).where(DailySale.product_id == product.id))
        db.commit()
    task = new_task(setup, sources=["inventory"])
    ctx = repo.claim()
    await ToolGateway(repo, settings, ctx, task["parameters"]).metrics("inventory")
    evidence = repo.evidence(task["id"])
    source = evidence[0]
    catalog = source["payload"]["fact_catalog"]
    assert "CUP-01.coverage_days" not in catalog
    assert catalog["CUP-01.lead_days"]["value"] == "23"
    assert catalog["CUP-01.overstock_threshold_days"]["value"] == "60"
    assert "配置" in catalog["CUP-01.lead_days"]["label"]
    assert "非企业规则" in catalog["CUP-01.overstock_threshold_days"]["label"]
    report = deterministic_report(evidence, [])
    claim = Claim(
        id="configured-parameters",
        kind="fact",
        text="待程序渲染",
        evidence_ids=[source["id"]],
        metrics=[
            metric_reference(source, f"CUP-01.{key}")
            for key in ("lead_days", "overstock_threshold_days")
        ],
    )
    report.sections[1].claims.append(claim)
    render_bound_metrics(report, evidence)
    assert validate_report(report, evidence)["passed"]
    assert "23 天" in claim.text and "60 天" in claim.text
    claim.metrics[0].entity_id = "BAG-02"
    assert not validate_report(report, evidence)["passed"]


def test_inventory_threshold_catalog_classification_and_sort_use_same_parameter(setup, monkeypatch):
    _, repo, *_ = setup
    monkeypatch.setattr(service, "OVERSTOCK_THRESHOLD_DAYS", 200, raising=False)
    result = service.inventory_risks(repo, "demo", date(2026, 9, 1), date(2026, 9, 30))
    # With the changed algorithm parameter, BAG is normal and sorts after LAMP.
    assert [item["sku"] for item in result["items"]] == ["CUP-01", "LAMP-03", "BAG-02"]
    assert [item["risk"] for item in result["items"]] == ["stockout", "normal", "normal"]
    assert "200" in result["definitions"]["overstock"]
    for item in result["items"]:
        key = f"{item['sku']}.overstock_threshold_days"
        assert result["metrics"][key] == result["fact_catalog"][key]["value"] == "200"


@pytest.mark.parametrize(
    "stock,risk", [(19, "stockout"), (20, "normal"), (600, "normal"), (601, "overstock")]
)
def test_inventory_risk_boundaries_remain_strict(setup, stock, risk):
    _, repo, *_ = setup
    day = date(2026, 9, 30)
    with repo.session() as db:
        product = db.scalar(select(Product).where(Product.sku == "CUP-01"))
        product.stock, product.lead_days = stock, 2
        sale = db.scalar(
            select(DailySale).where(DailySale.product_id == product.id, DailySale.day == day)
        )
        sale.paid_units, sale.refunded_units = 10, 0
        db.commit()
    result = service.inventory_risks(repo, "demo", day, day)
    item = next(item for item in result["items"] if item["sku"] == "CUP-01")
    assert item["risk"] == risk


async def test_unbound_inventory_parameters_receive_actionable_single_repair(setup):
    class ParameterRepairModel(ScriptedCommerceModel):
        _drafts: int = PrivateAttr(default=0)

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            calls = result.generations[0].message.tool_calls
            if not calls or calls[0]["name"] != "ResearchReport":
                return result
            self._drafts += 1
            metadata, raw_sources = messages[-1].content.split("\n证据：\n", 1)
            metadata, sources = json.loads(metadata), json.loads(raw_sources)
            source = sources[0]
            report = calls[0]["args"]
            if self._drafts == 1:
                report["sections"][1]["claims"].append(
                    {
                        "id": "unbound-lead",
                        "kind": "fact",
                        "text": "保温杯配置补货提前期为7天。",
                        "evidence_ids": [source["id"]],
                        "metrics": [],
                    }
                )
            else:
                assert self._drafts == 2
                assert any("unbound-lead" in issue for issue in metadata["prior_issues"])
                assert any("完整metrics引用" in issue for issue in metadata["prior_issues"])
                assert metadata["prior_report"]["sections"][1]["claims"][-1]["metrics"] == []
                report["sections"][1]["claims"].append(
                    {
                        "id": "bound-lead",
                        "kind": "fact",
                        "text": "待程序渲染",
                        "evidence_ids": [source["id"]],
                        "metrics": [metric_reference(source, "CUP-01.lead_days").model_dump()],
                    }
                )
            return result

    settings, repo, *_ = setup
    settings.agent_mode, settings.llm_model = "live", "fake-commerce"
    settings.openai_api_key = SecretStr("offline-inventory-parameters")
    task = new_task(setup, sources=["inventory"])
    model = ParameterRepairModel()
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver, model).run_once()
    done = repo.get_task(task["id"])
    assert model._drafts == 2
    assert done["validation"]["passed"] and not done["validation"].get("fallback_used")
    assert done["validation"]["semantic_status"] == "model_reviewed"
    rejected = [event for event in repo.events(task["id"]) if event["kind"] == "draft_rejected"]
    assert len(rejected) == 1
    claims = done["report"]["sections"][1]["claims"]
    assert any(claim["id"] == "bound-lead" and "7 天" in claim["text"] for claim in claims)
