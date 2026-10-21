"""G4 source admission on disposable PostgreSQL, without model or external services."""

import asyncio
import os

import pytest
from sqlalchemy import func, select

from app.diagnosis.inputs import create_bundle
from app.diagnosis.sources import commit_inventory, stage_inventory
from app.storage.models import AnalysisInputBundle, InventoryDataset, User
from tests.test_diagnosis_extensions import (
    test_inventory_and_manifest_api_admission_and_versioned_review as check_extension_api,
)
from tests.test_diagnosis_extensions import (
    test_sigkill_committed_inventory_reuses_one_call_and_exact_evidence as check_extension_sigkill,
)
from tests.test_diagnosis_extensions import (
    test_stock_evidence_truth_recomputes_values_and_lease_takeover_reuses_call as check_stock_reuse,
)
from tests.test_diagnosis_extensions import (
    test_worker_inventory_and_rules_have_bound_facts_and_unverified_semantics as check_extensions,
)
from tests.test_diagnosis_sources import (
    stock_payload,
)
from tests.test_diagnosis_sources import (
    test_manifest_conversation_permissions_chunk_version_and_source_intersection as check_manifest,
)
from tests.test_diagnosis_sources import (
    test_revocation_blocks_bound_input_and_cleanup_keeps_referenced_versions as check_retention,
)
from tests.test_diagnosis_sources import (
    test_same_tenant_still_has_owner_and_manifest_boundaries as check_same_tenant,
)
from tests.test_diagnosis_tools import bundle_request, committed
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


async def test_postgres_inventory_import_and_input_bundle_are_unique_under_concurrency(setup):
    _, repo, user, _ = setup
    sales = committed(repo, user)
    results = await asyncio.gather(
        *(
            asyncio.to_thread(
                stage_inventory, repo, user, stock_payload(), "并发库存", True, "0" * 64
            )
            for _ in range(4)
        )
    )
    assert len({row["id"] for row in results}) == 1
    inventory = commit_inventory(repo, user, results[0]["id"])
    request = bundle_request(sales["id"]).model_copy(
        update={"inventory_dataset_id": inventory["id"]}
    )
    bundles = await asyncio.gather(
        *(asyncio.to_thread(create_bundle, repo, user, request) for _ in range(4))
    )
    assert len({row["bundle_id"] for row in bundles}) == 1
    with repo.session() as db:
        assert db.scalar(select(func.count()).select_from(InventoryDataset)) == 1
        assert db.scalar(select(func.count()).select_from(AnalysisInputBundle)) == 1


def test_postgres_document_scope_and_chunk_version(setup):
    check_manifest(setup)


def test_postgres_retention_of_revoked_referenced_sources(setup):
    check_retention(setup)


def test_postgres_same_tenant_source_isolation(setup):
    check_same_tenant(setup)


async def test_postgres_extended_worker_report_truth(setup):
    await check_extensions(setup)


async def test_postgres_inventory_document_api_and_review(client, setup):
    await check_extension_api(client, setup)


def test_postgres_stock_cache_and_takeover(setup):
    check_stock_reuse(setup)


async def test_postgres_sigkill_committed_inventory_tool(setup):
    await check_extension_sigkill(setup)
