"""Read-only diagnosis recovery audit; contains no provider calls or task claims."""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter

from sqlalchemy import inspect, select, text

from app.commerce.review_inputs import REVIEW_BUNDLE_VERSIONS
from app.core.config import Settings
from app.core.files import safe_path
from app.diagnosis.contracts import RUNTIME_VERSIONS, V1
from app.diagnosis.inputs import dataset_digest, digest
from app.storage.models import (
    AnalysisInputBundle,
    Artifact,
    Chunk,
    CommerceDataset,
    Document,
    DocumentManifest,
    InputRequest,
    InventoryDataset,
    ModelInvocation,
    Task,
    ToolInvocation,
)
from app.storage.repository import Repository
from scripts.backup_common import file_hash


def frozen_sources_valid(db, bundle):
    """Check content versions without turning withdrawn access back into access."""
    sales = db.get(CommerceDataset, bundle.sales_dataset_id)
    if (
        not sales
        or dataset_digest(sales.payload) != sales.content_hash
        or sales.content_hash != bundle.payload["sales_content_hash"]
    ):
        return False
    if bundle.inventory_dataset_id:
        stock = db.get(InventoryDataset, bundle.inventory_dataset_id)
        if (
            not stock
            or digest(stock.payload) != stock.content_hash
            or stock.content_hash != bundle.payload.get("inventory_content_hash")
        ):
            return False
    if bundle.document_manifest_id:
        manifest = db.get(DocumentManifest, bundle.document_manifest_id)
        if (
            not manifest
            or digest(manifest.payload) != manifest.content_hash
            or manifest.content_hash != bundle.payload.get("document_manifest_hash")
        ):
            return False
        for frozen in manifest.payload["documents"]:
            doc = db.get(Document, frozen["document_id"])
            if not doc or doc.content_hash != frozen["content_hash"] or doc.name != frozen["name"]:
                return False
            chunks = {c.id: c for c in db.scalars(select(Chunk).where(Chunk.document_id == doc.id))}
            if chunks.keys() != {c["chunk_id"] for c in frozen["chunks"]}:
                return False
            for chunk in frozen["chunks"]:
                actual = chunks[chunk["chunk_id"]]
                if (
                    hashlib.sha256(actual.text.encode()).hexdigest() != chunk["content_hash"]
                    or actual.locator != chunk["locator"]
                ):
                    return False
    return True


def audit(repo, *, require_stopped=False):
    required = {"analysis_input_bundles", "model_invocations", "tool_invocations", "input_requests"}
    tables = set(inspect(repo.engine).get_table_names())
    if not required <= tables:
        return {
            "passed": False,
            "issues": ["diagnosis_schema_not_migrated"],
            "format": "diagnosis-recovery-audit-v1",
        }
    issues, snapshot = [], []
    with repo.session() as db:
        tasks = list(db.scalars(select(Task).order_by(Task.id)))
        models = list(db.scalars(select(ModelInvocation).order_by(ModelInvocation.call_id)))
        tools = list(db.scalars(select(ToolInvocation)))
        artifacts = list(db.scalars(select(Artifact).order_by(Artifact.id)))
        waiting_ids = []
        for task in tasks:
            if require_stopped and task.status in {"running", "cancelling"}:
                issues.append("active_task:" + task.id)
            profile = task.parameters.get("profile")
            diagnostic = profile == "commerce_diagnosis"
            bound_fixed = profile == "commerce_review" and task.bundle_id
            if diagnostic or bound_fixed:
                allowed = (
                    list(RUNTIME_VERSIONS.values()) if diagnostic else [REVIEW_BUNDLE_VERSIONS]
                )
                versions = {k: getattr(task, k) for k in V1}
                if versions not in allowed or any(
                    task.parameters.get(k) != v for k, v in versions.items()
                ):
                    issues.append("unsupported_or_mismatched_version:" + task.id)
                bundle = db.get(AnalysisInputBundle, task.bundle_id)
                if (
                    not bundle
                    or digest(bundle.payload) != bundle.bundle_hash
                    or task.parameters.get("bundle_hash") != bundle.bundle_hash
                ):
                    issues.append("bundle_integrity:" + task.id)
                elif not frozen_sources_valid(db, bundle):
                    issues.append("frozen_source_integrity:" + task.id)
            if diagnostic:
                calls = [c for c in models if c.task_id == task.id]
                attempts = [c for c in tools if c.task_id == task.id]
                if len(calls) != task.model_calls or len(attempts) != task.tool_calls:
                    issues.append("call_reservation_count:" + task.id)
                if any(c.status == "STARTED" for c in calls):
                    issues.append("unreconciled_model_start:" + task.id)
                if any(
                    c.status not in {"SUCCEEDED", "FAILED", "UNKNOWN", "STARTED"} for c in calls
                ):
                    issues.append("unknown_model_status:" + task.id)
                valid = [c for c in calls if c.usage_complete]
                if any(
                    not isinstance(c.usage, dict)
                    or any(
                        type(c.usage.get(k)) is not int or c.usage[k] < 0
                        for k in ("input_tokens", "output_tokens")
                    )
                    for c in valid
                ):
                    issues.append("invalid_usage_receipt:" + task.id)
                else:
                    if any(
                        sum(c.usage[k] for c in valid) != getattr(task, k)
                        for k in ("input_tokens", "output_tokens")
                    ):
                        issues.append("usage_total_mismatch:" + task.id)
                if len(calls) != task.usage_reports or task.usage_complete != all(
                    c.usage_complete for c in calls
                ):
                    issues.append("usage_completeness_mismatch:" + task.id)
                if task.report and digest(task.report) != task.report_hash:
                    issues.append("report_hash_mismatch:" + task.id)
                if task.status in {"waiting_input", "waiting_review"}:
                    pending = db.get(InputRequest, task.pending_interrupt_id)
                    expected_type = (
                        "clarification" if task.status == "waiting_input" else "report_review"
                    )
                    if (
                        not pending
                        or pending.task_id != task.id
                        or pending.interrupt_type != expected_type
                        or pending.expected_version != task.state_version
                        or pending.decision is not None
                        or pending.waiting_until != task.waiting_until
                    ):
                        issues.append("typed_wait_mismatch:" + task.id)
                    waiting_ids.append(task.id)
            snapshot.append(
                {
                    "id": task.id,
                    "status": task.status,
                    "parameters_hash": digest(task.parameters),
                    "graph": task.graph_version,
                    "report_hash": task.report_hash,
                    "pending": task.pending_interrupt_id,
                    "version": task.state_version,
                    "models": task.model_calls,
                    "tools": task.tool_calls,
                    "input_tokens": task.input_tokens,
                    "output_tokens": task.output_tokens,
                    "usage_complete": task.usage_complete,
                }
            )
        for artifact in artifacts:
            try:
                path = safe_path(repo.settings.data_dir / "artifacts", artifact.storage_key)
                if (
                    not path.is_file()
                    or path.stat().st_size != artifact.size
                    or file_hash(path) != artifact.sha256
                ):
                    issues.append("artifact_integrity:" + artifact.id)
            except (ValueError, OSError):
                issues.append("artifact_integrity:" + artifact.id)
        if repo.settings.database_url.startswith("postgresql"):
            checkpoint_ids = (
                set(db.scalars(text("SELECT DISTINCT thread_id FROM checkpoints")))
                if "checkpoints" in tables
                else set()
            )
        else:
            checkpoint = repo.settings.data_dir / "checkpoints.sqlite"
            checkpoint_ids = set()
            if checkpoint.is_file():
                try:
                    with sqlite3.connect(
                        f"file:{checkpoint.resolve()}?mode=ro", uri=True
                    ) as checkpoints:
                        checkpoint_ids = {
                            row[0]
                            for row in checkpoints.execute(
                                "SELECT DISTINCT thread_id FROM checkpoints"
                            )
                        }
                except sqlite3.DatabaseError:
                    issues.append("checkpoint_database_unreadable")
        issues += [
            "waiting_checkpoint_missing:" + task_id
            for task_id in waiting_ids
            if task_id not in checkpoint_ids
        ]
        snapshot += [
            {
                "call_id": c.call_id,
                "task_id": c.task_id,
                "status": c.status,
                "prompt_hash": c.prompt_hash,
                "schema_hash": c.schema_hash,
                "usage": c.usage,
                "response_digest": c.response_digest,
            }
            for c in models
        ]
        snapshot += [
            {
                "artifact_id": a.id,
                "sha256": a.sha256,
                "report_version": a.report_version,
                "report_hash": a.report_hash,
            }
            for a in artifacts
        ]
        return {
            "format": "diagnosis-recovery-audit-v1",
            "passed": not issues,
            "issues": sorted(set(issues)),
            "state_sha256": digest(snapshot),
            "task_statuses": dict(Counter(t.status for t in tasks)),
            "graph_versions": dict(Counter(t.graph_version for t in tasks)),
            "model_statuses": dict(Counter(c.status for c in models)),
            "tool_attempts": len(tools),
            "registered_artifacts": len(artifacts),
            "typed_waits_with_checkpoint": len(waiting_ids),
            "unknown_usage_retained": sum(not c.usage_complete for c in models),
            "scope": "State/ledger/version/file integrity only; not model quality or production acceptance.",
        }


def audit_settings(settings, *, require_stopped=True):
    repo = Repository(settings)
    try:
        return audit(repo, require_stopped=require_stopped)
    finally:
        repo.engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--services-stopped", action="store_true")
    args = parser.parse_args()
    result = audit_settings(Settings(), require_stopped=args.services_stopped)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
