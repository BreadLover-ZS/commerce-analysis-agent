"""Pre-task source/period inspection, with no Task or call budget side effects."""

import json

import pytest
from sqlalchemy import func, select

from app.commerce.service import add_document
from app.diagnosis.inputs import bundle_quality, create_bundle
from app.diagnosis.schemas import InputBundleRequest
from app.diagnosis.source_schemas import ManifestRequest
from app.diagnosis.sources import create_manifest
from app.storage.models import (
    CommerceDataset,
    InventoryDataset,
    ModelInvocation,
    Task,
    ToolInvocation,
)
from app.storage.repository import Conflict
from tests.conftest import authenticate
from tests.test_diagnosis_sources import combined
from tests.test_diagnosis_tools import bundle_request, committed


def test_preflight_uses_exact_comparison_and_scope_without_creating_task(setup):
    _, repo, user, _ = setup
    sales = committed(repo, user)
    explicit = create_bundle(repo, user, bundle_request(sales["id"], allowed_skus=["B"]))
    quality = bundle_quality(repo, user, explicit["bundle_id"])
    assert quality["comparable"]
    assert quality["coverage"]["current"] == {
        "expected_records": 1,
        "observed_records": 1,
        "missing_records": 0,
        "complete": True,
    }
    assert quality["coverage"]["comparison"] == quality["coverage"]["current"]
    assert quality["allowed_skus"] == ["B"] and not quality["task_created"]
    assert quality["unsupported_metrics"] == [
        "profit",
        "conversion_rate",
        "roi",
        "cohort_refund_rate",
    ]
    adjacent = create_bundle(
        repo,
        user,
        InputBundleRequest(
            sales_dataset_id=sales["id"], period={"start": "2026-09-01", "end": "2026-09-01"}
        ),
    )
    missing = bundle_quality(repo, user, adjacent["bundle_id"])
    assert missing["comparison_period"] == {"start": "2026-08-31", "end": "2026-08-31"}
    assert not missing["comparable"] and missing["coverage"]["comparison"]["missing_records"] == 2
    assert missing["coverage"]["current"]["observed_records"] == 2
    with repo.session() as db:
        for table in (Task, ToolInvocation, ModelInvocation):
            assert db.scalar(select(func.count()).select_from(table)) == 0


def test_quality_endpoint_ownership_and_revocation(client, setup):
    _, repo, user, _ = setup
    sales = committed(repo, user)
    bundle = create_bundle(repo, user, bundle_request(sales["id"]))
    path = f"/api/analysis-inputs/{bundle['bundle_id']}/quality"
    assert client.get(path).status_code == 401
    authenticate(client)
    response = client.get(path)
    assert response.status_code == 200 and response.json()["comparable"]
    authenticate(client, "other@example.com", "other-commerce-2026")
    assert client.get(path).status_code == 404
    authenticate(client)
    with repo.session() as db:
        db.get(CommerceDataset, sales["id"]).committed = False
        db.commit()
    assert client.get(path).status_code == 404


def test_combined_preflight_reports_stock_gaps_and_document_semantic_boundary(setup):
    _, repo, user, _ = setup
    bundle, _ = combined(setup)
    conversation = repo.create_conversation(user.id)
    private_text = "库存规则原文，预览接口不应该把这段全文发给前端。"
    doc = add_document(
        repo, user.tenant_id, "规则", [("line=1", private_text)], user.id, conversation
    )
    manifest = create_manifest(
        repo, user, ManifestRequest(document_ids=[doc], conversation_id=conversation)
    )
    bundle = create_bundle(
        repo,
        user,
        bundle_request(
            bundle["sales_dataset_id"],
            inventory_dataset_id=bundle["inventory_dataset_id"],
            document_manifest_id=manifest["id"],
        ),
    )
    quality = bundle_quality(repo, user, bundle["bundle_id"])
    assert quality["sample"] and quality["source_samples"] == {
        "sales": True,
        "inventory": True,
        "documents": False,
    }
    assert quality["inventory"]["items"] == [
        {"sku": "A", "snapshot_day": "2026-09-01", "status": "ok", "reason": None},
        {
            "sku": "B",
            "snapshot_day": "2026-09-01",
            "status": "partial",
            "reason": "missing_lead_days",
        },
    ]
    assert quality["documents"]["count"] == quality["documents"]["chunks"] == 1
    assert quality["documents"]["semantic_status"] == "unverified"
    assert private_text not in json.dumps(quality, ensure_ascii=False)
    assert quality["available_tools"][-2:] == ["inventory_risk", "search_rule_documents"]


def test_quality_rechecks_all_source_hashes(setup):
    _, repo, user, _ = setup
    bundle, inventory = combined(setup)
    bundle_quality(repo, user, bundle["bundle_id"])
    with repo.session() as db:
        row = db.get(InventoryDataset, inventory["id"])
        row.payload = {
            **row.payload,
            "items": [{**row.payload["items"][0], "available_units": 999}],
        }
        db.commit()
    with pytest.raises(Conflict):
        bundle_quality(repo, user, bundle["bundle_id"])
