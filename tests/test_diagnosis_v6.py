"""Independent lexical recall fixtures; original registry-2 results remain stable."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.commerce.service import add_document
from app.diagnosis.inputs import create_bundle
from app.diagnosis.schemas import InputBundleRequest
from app.diagnosis.source_schemas import ManifestRequest
from app.diagnosis.sources import create_manifest
from app.diagnosis.store import DiagnosisStore
from app.diagnosis.tools import DiagnosisToolGateway
from app.diagnosis.validation import verified_evidence
from app.research.schemas import TaskRequest
from app.storage.models import ToolQueryResult
from tests.test_diagnosis_extensions import run
from tests.test_diagnosis_tools import bundle_request, committed


def rule_task(setup, runtime, sources=None):
    settings, repo, user, _ = setup
    settings.diagnosis_runtime = runtime
    conversation = repo.create_conversation(user.id)
    docs = [
        add_document(repo, user.tenant_id, name, [("正文", text)], user.id, conversation)
        for name, text in (
            ("候选甲", "覆盖少于四天时应人工核对。"),
            ("候选乙", "覆盖低于八天时需人工复核。"),
        )
    ]
    add_document(
        repo,
        user.tenant_id,
        "未入清单",
        [("正文", "库存覆盖低于十五天。清单外片段不可引用。")],
        user.id,
        conversation,
    )
    manifest = create_manifest(
        repo, user, ManifestRequest(document_ids=docs, conversation_id=conversation)
    )
    sales = committed(repo, user)
    request = bundle_request(sales["id"], document_manifest_id=manifest["id"])
    bundle = create_bundle(repo, user, InputBundleRequest.model_validate(request))
    task = repo.create_task(
        user,
        TaskRequest(
            conversation_id=conversation,
            query="查阅库存规则原文",
            profile="commerce_diagnosis",
            bundle_id=bundle["bundle_id"],
            sources=sources or ["sales", "documents"],
        ),
        str(uuid4()),
    )
    return task, bundle


@pytest.mark.parametrize("runtime,version,quotes", [("v5", "2", 0), ("v6", "3", 1)])
def test_registry_version_preserves_old_recall_cache_and_source_validation(
    setup, runtime, version, quotes
):
    settings, repo, *_ = setup
    task, _ = rule_task(setup, runtime)
    settings.diagnosis_runtime = "v6"  # Saved tasks, not the process default, own the algorithm.
    ctx = repo.claim()
    gateway = DiagnosisToolGateway(repo, ctx)
    args = {"query": "库存", "limit": 1}
    result = gateway.invoke("search_rule_documents", args, "first")
    assert result["tool_version"] == version
    assert len(result["quotes"]) == quotes
    again = gateway.invoke("search_rule_documents", args, "second")
    assert again["reused"] and again["call_id"] == result["call_id"]
    assert repo.get_task(task["id"])["metrics"]["tool_calls"] == 1
    if runtime == "v6":
        assert result["retrieval_method"] == "manifest_lexical_v2" and result["truncated"]
        assert "覆盖" in result["expanded_terms"]
    data = DiagnosisStore(repo).snapshot(ctx)
    source = data["evidence"][0]
    with repo.session() as db:
        query = db.scalar(select(ToolQueryResult))
        stored = {
            "query_spec": query.query_spec,
            "result_digest": query.result_digest,
            "result": query.result,
        }
    assert verified_evidence(
        source,
        stored,
        data["bundle"],
        data["sales_payload"],
        task["id"],
        task=data["task"],
        extras=data,
    )


async def test_worker_registry3_keeps_distinct_literal_quotes_and_unverified_semantics(setup):
    task, _ = rule_task(setup, "v6")
    done = await run(setup, task)
    assert done["validation"]["passed"], done
    report = done["report"]
    assert report["versions"]["tool_registry_version"] == "3"
    assert report["stop_reason"] == "target_covered"
    assert {q["quote"] for q in report["rule_quotes"]} == {
        "覆盖少于四天时应人工核对。",
        "覆盖低于八天时需人工复核。",
    }
    assert report["rule_conflicts"][0]["status"] == "unverified_conflict_candidate"
    assert done["validation"]["rule_semantics"] == "unverified"
    assert "清单外片段" not in str(report["rule_quotes"])
    assert done["metrics"]["tool_calls"] == 3 and done["metrics"]["model_calls"] == 0


def test_registry3_expansion_does_not_enable_unselected_documents(setup):
    task, _ = rule_task(setup, "v6", sources=["sales"])
    repo = setup[1]
    gateway = DiagnosisToolGateway(repo, repo.claim())
    assert "search_rule_documents" not in gateway.catalog()
    result = gateway.invoke("search_rule_documents", {"query": "库存"}, "unselected")
    assert result["status"] == "unavailable" and not result["evidence_ids"]
    assert repo.get_task(task["id"])["metrics"]["tool_calls"] == 0
