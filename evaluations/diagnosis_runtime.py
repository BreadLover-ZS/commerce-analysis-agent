"""Isolated fixtures and actual Worker execution for the diagnosis evaluation CLI."""

import hashlib
import shutil
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

from app.commerce.datasets import commit_dataset, csv_bytes, stage_dataset
from app.commerce.service import add_document
from app.core.auth import bootstrap_user
from app.core.config import Settings
from app.core.files import safe_path
from app.diagnosis.inputs import create_bundle, digest
from app.diagnosis.schemas import InputBundleRequest
from app.diagnosis.source_schemas import InventoryInput, ManifestRequest
from app.diagnosis.sources import commit_inventory, create_manifest, stage_inventory
from app.research.schemas import TaskRequest
from app.storage.models import (
    Artifact,
    Chunk,
    DiagnosisStep,
    Document,
    InputRequest,
    ModelInvocation,
    Task,
    ToolInvocation,
    ToolQueryResult,
    User,
)
from app.storage.repository import NotFound, Repository
from app.tasks.worker import Worker, checkpoint_store
from evaluations.diagnosis_cases import business_input

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ("graph_version", "schema_version", "prompt_version", "tool_registry_version")


class EvaluationSettings(Settings):
    """Only explicit constructor values and code defaults; no process env or .env."""

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (init_settings,)


def columns(row):
    return {
        c.name: (
            value.isoformat()
            if isinstance(value := getattr(row, c.name), (date, datetime))
            else value
        )
        for c in row.__table__.columns
    }


@contextmanager
def sandbox(**configuration):
    with TemporaryDirectory(prefix="diagnosis-evaluation-") as directory:
        root = Path(directory)
        values = dict(
            _env_file=None,
            app_env="test",
            data_dir=root,
            database_url="sqlite:///" + str(root / "app.sqlite"),
            seed_demo=False,
            openai_api_key="",
            tavily_api_key="",
            ragflow_api_key="",
            knowledge_provider="local",
            max_tool_calls=10,
            max_model_calls=12,
            max_tokens=60000,
            max_runtime_seconds=180,
            lease_seconds=30,
        )
        values.update(configuration)
        settings = EvaluationSettings(**values)
        config = Config(str(ROOT / "alembic.ini"))
        config.attributes["settings"] = settings
        command.upgrade(config, "head")
        repo = Repository(settings)
        try:
            bootstrap_user(repo, settings)
            with repo.session() as db:
                user = db.scalar(select(User).where(User.email == settings.bootstrap_email))
                other = User(
                    id=str(uuid4()),
                    tenant_id="foreign-evaluation",
                    email="foreign@example.invalid",
                    password_hash="unusable",
                )
                db.add(other)
                db.commit()
            yield settings, repo, user, other
        finally:
            repo.engine.dispose()


def fixtures(repo, user, case):
    """Allowlisted business data only. Case IDs, labels, expectations never enter Tasks."""
    business = business_input(case)
    conversation = repo.create_conversation(user.id)
    sales = stage_dataset(
        repo,
        user,
        case.sales,
        "固定经营资料",
        True,
        hashlib.sha256(csv_bytes(case.sales)).hexdigest(),
    )
    commit_dataset(repo, user, sales["id"])
    inventory = manifest = None
    if business["inventory"] is not None:
        raw_stock = business["inventory"]
        stock_input = {
            "schema_version": 1,
            "items": [
                {**row, "snapshot_day": row.get("snapshot_day") or raw_stock.get("snapshot_day")}
                for row in raw_stock["items"]
            ],
        }
        inventory = stage_inventory(
            repo,
            user,
            InventoryInput.model_validate(stock_input),
            "固定库存资料",
            True,
            digest(business["inventory"]),
        )
        commit_inventory(repo, user, inventory["id"])
    if business["documents"]:
        ids = [
            add_document(
                repo,
                user.tenant_id,
                doc["name"],
                [("正文", doc["text"])],
                user.id,
                conversation,
                sample=True,
            )
            for doc in business["documents"]
        ]
        manifest = create_manifest(
            repo, user, ManifestRequest(document_ids=ids, conversation_id=conversation)
        )
    request = {
        "sales_dataset_id": sales["id"],
        "period": business["period"],
        "comparison_period": business["comparison_period"],
        "allowed_skus": business["scope_skus"],
        "inventory_dataset_id": inventory["id"] if inventory else None,
        "document_manifest_id": manifest["id"] if manifest else None,
    }
    return conversation, request


def admit(repo, user, other, case):
    owner = other if case.harness.mode == "foreign_bundle" else user
    try:
        conversation, request = fixtures(repo, owner, case)
    except Exception as exc:
        with repo.session() as db:
            count = db.scalar(select(func.count()).select_from(Task))
        return (
            None,
            None,
            {
                "group": "gate",
                "task": None,
                "admission_status": None,
                "task_count": count,
                "preparation_error": type(exc).__name__,
            },
        )
    bundle, status = None, None
    try:
        bundle = create_bundle(repo, owner, InputBundleRequest(**request))
        if case.execution == "admission":
            repo.create_task(
                user,
                TaskRequest(
                    conversation_id=repo.create_conversation(user.id),
                    query=case.query,
                    profile="commerce_diagnosis",
                    bundle_id=bundle["bundle_id"],
                    sources=case.sources,
                ),
                str(uuid4()),
            )
    except NotFound:
        status = 404
    except ValueError:
        status = 400
    if case.execution != "admission" and status is not None:
        raise ValueError("diagnostic_fixture_rejected")
    with repo.session() as db:
        count = db.scalar(select(func.count()).select_from(Task))
    return (
        conversation,
        bundle,
        {"group": "gate", "task": None, "admission_status": status, "task_count": count},
    )


def new_task(repo, user, conversation, bundle, query, group, *, require_review=False, sources=None):
    return repo.create_task(
        user,
        TaskRequest(
            conversation_id=conversation,
            query=query,
            bundle_id=bundle["bundle_id"],
            profile="commerce_review" if group == "fixed" else "commerce_diagnosis",
            strategy="workflow" if group == "fixed" else "diagnostic",
            sources=["sales"] if group == "fixed" else sources or bundle["sources"],
            require_review=require_review,
        ),
        str(uuid4()),
    )


async def execute(repo, settings, *, model=None, ctx=None):
    async with checkpoint_store(settings) as saver:
        worker = Worker(repo, settings, saver, model=model)
        if ctx is not None:
            await worker.process(ctx)
        elif not await worker.run_once():
            raise RuntimeError("no_runnable_evaluation_task")


def collect(repo, task_id, group):
    # Close each session before Repository reads (SQLite uses BEGIN IMMEDIATE).
    with repo.session() as db:
        tables = {
            name: [
                columns(row) for row in db.scalars(select(table).where(table.task_id == task_id))
            ]
            for name, table in (
                ("steps", DiagnosisStep),
                ("queries", ToolQueryResult),
                ("model_invocations", ModelInvocation),
                ("tool_invocations", ToolInvocation),
                ("input_requests", InputRequest),
            )
        }
        task = db.get(Task, task_id)
        chunks = [
            {
                "document_id": doc.id,
                "document_name": doc.name,
                "document_hash": doc.content_hash,
                "chunk_id": chunk.id,
                "chunk_hash": hashlib.sha256(chunk.text.encode()).hexdigest(),
                "locator": chunk.locator,
                "text": chunk.text,
            }
            for doc, chunk in db.execute(
                select(Document, Chunk)
                .join(Chunk, Chunk.document_id == Document.id)
                .where(Document.conversation_id == task.conversation_id)
            )
        ]
    return {
        "group": group,
        "task": repo.get_task(task_id),
        "evidence": repo.evidence(task_id),
        "events": repo.events(task_id, limit=500),
        "document_chunks": chunks,
        **tables,
    }


def export_artifacts(repo, task_id, output):
    output.mkdir()
    with repo.session() as db:
        files = [
            (a.name, a.storage_key, a.sha256)
            for a in db.scalars(select(Artifact).where(Artifact.task_id == task_id))
        ]
    for name, key, expected in files:
        source = safe_path(repo.settings.data_dir / "artifacts", key)
        if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError("artifact_digest_mismatch")
        shutil.copyfile(source, safe_path(output, name))
