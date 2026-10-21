"""Owner-scoped local inventory versions and immutable document/chunk manifests."""

import csv
import hashlib
import io
from datetime import timedelta
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import delete, func, or_, select

from app.diagnosis.inputs import digest
from app.diagnosis.source_schemas import InventoryInput, InventoryRow
from app.storage.models import (
    AnalysisInputBundle,
    Chunk,
    Conversation,
    Document,
    DocumentManifest,
    InventoryDataset,
    User,
    utcnow,
)
from app.storage.repository import Conflict, NotFound

INVENTORY_FIELDS = ["sku", "snapshot_day", "available_units", "lead_days"]
MAX_SOURCE_BYTES = 2 * 1024 * 1024


def inventory_payload(value):
    data = InventoryInput.model_validate(value).model_dump(mode="json")
    data["items"].sort(key=lambda item: item["sku"])
    return data


def parse_inventory_csv(content):
    if not content or len(content) > MAX_SOURCE_BYTES:
        raise ValueError("库存CSV不能为空且不得超过2 MiB")
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")), strict=True)
        if reader.fieldnames != INVENTORY_FIELDS:
            raise ValueError("库存CSV列名或顺序不匹配")
        rows = []
        for row in reader:
            if len(rows) >= 100:
                raise ValueError("库存CSV最多100条SKU快照")
            try:
                if None in row or any(v is None for v in row.values()):
                    raise ValueError()
                rows.append(
                    InventoryRow(
                        sku=row["sku"],
                        snapshot_day=row["snapshot_day"],
                        available_units=int(row["available_units"]),
                        lead_days=int(row["lead_days"]) if row["lead_days"].strip() else None,
                    )
                )
            except (ValueError, ValidationError):
                raise ValueError(f"第{reader.line_num}行：SKU、日期或非负整数校验失败") from None
        return InventoryInput(items=rows)
    except (UnicodeError, csv.Error):
        raise ValueError("需要有效的UTF-8库存CSV") from None
    except ValidationError:
        raise ValueError("库存须含1–100个唯一SKU，每个SKU一条快照") from None


def owned_inventory(db, dataset_id, user_id, tenant_id, *, committed=True):
    row = db.get(InventoryDataset, dataset_id)
    if (
        not row
        or row.user_id != user_id
        or row.tenant_id != tenant_id
        or (committed and not row.committed)
    ):
        raise NotFound()
    if digest(row.payload) != row.content_hash:
        raise Conflict("库存版本校验失败")
    return row


def inventory_info(row):
    days = [i["snapshot_day"] for i in row.payload["items"]]
    return {
        "id": row.id,
        "name": row.name,
        "sample": row.sample,
        "committed": row.committed,
        "content_hash": row.content_hash,
        "file_hash": row.file_hash,
        "schema_version": 1,
        "rows": len(days),
        "snapshot_start": min(days),
        "snapshot_end": max(days),
        "created_at": row.created_at.isoformat() + "Z",
    }


def stage_inventory(repo, user, payload, name, sample, file_hash):
    data = inventory_payload(payload)
    hashed = digest(data)
    with repo.session() as db:
        db.scalar(select(User).where(User.id == user.id).with_for_update())
        referenced = (
            select(AnalysisInputBundle.id)
            .where(AnalysisInputBundle.inventory_dataset_id == InventoryDataset.id)
            .exists()
        )
        db.execute(
            delete(InventoryDataset).where(
                InventoryDataset.user_id == user.id,
                InventoryDataset.committed.is_(False),
                InventoryDataset.created_at < utcnow() - timedelta(days=1),
                ~referenced,
            )
        )
        prior = db.scalar(
            select(InventoryDataset).where(
                InventoryDataset.user_id == user.id,
                InventoryDataset.content_hash == hashed,
                InventoryDataset.sample == sample,
            )
        )
        if prior:
            if prior.tenant_id != user.tenant_id:
                raise NotFound()
            db.commit()
            return inventory_info(prior)
        count = db.scalar(
            select(func.count())
            .select_from(InventoryDataset)
            .where(InventoryDataset.user_id == user.id)
        )
        if count >= 50:
            raise Conflict("库存版本达到50个上限，请联系管理员归档")
        row = InventoryDataset(
            id=str(uuid4()),
            user_id=user.id,
            tenant_id=user.tenant_id,
            name=name[:120],
            content_hash=hashed,
            file_hash=file_hash,
            sample=sample,
            committed=False,
            payload=data,
        )
        db.add(row)
        db.commit()
        return inventory_info(row)


def commit_inventory(repo, user, dataset_id):
    with repo.session() as db:
        db.scalar(select(User).where(User.id == user.id).with_for_update())
        row = owned_inventory(db, dataset_id, user.id, user.tenant_id, committed=False)
        row.committed = True
        db.commit()
        return inventory_info(row)


def list_inventory(repo, user):
    with repo.session() as db:
        return [
            inventory_info(row)
            for row in db.scalars(
                select(InventoryDataset)
                .where(
                    InventoryDataset.user_id == user.id,
                    InventoryDataset.tenant_id == user.tenant_id,
                    InventoryDataset.committed.is_(True),
                )
                .order_by(InventoryDataset.created_at.desc())
            )
        ]


def visible_document(db, document_id, user_id, tenant_id, conversation_id=None):
    row = db.get(Document, document_id)
    if not row or row.tenant_id != tenant_id or row.user_id not in (None, user_id):
        raise NotFound()
    if row.conversation_id:
        conversation = db.get(Conversation, row.conversation_id)
        if (
            row.conversation_id != conversation_id
            or not conversation
            or conversation.user_id != user_id
        ):
            raise NotFound()
    return row


def document_snapshot(db, row):
    chunks = list(db.scalars(select(Chunk).where(Chunk.document_id == row.id).order_by(Chunk.id)))
    if not chunks or len(chunks) > 250:
        raise Conflict("文档片段为空或超过本地清单上限")
    return {
        "document_id": row.id,
        "name": row.name,
        "content_hash": row.content_hash,
        "sample": row.sample,
        "scope": {
            "tenant_id": row.tenant_id,
            "user_id": row.user_id,
            "conversation_id": row.conversation_id,
        },
        "chunks": [
            {
                "chunk_id": c.id,
                "locator": c.locator,
                "content_hash": hashlib.sha256(c.text.encode()).hexdigest(),
            }
            for c in chunks
        ],
    }


def manifest_info(row):
    return {
        "id": row.id,
        "content_hash": row.content_hash,
        "sample": row.sample,
        "conversation_id": row.conversation_id,
        "documents": row.payload["documents"],
        "created_at": row.created_at.isoformat() + "Z",
    }


def create_manifest(repo, user, request):
    conversation_id = str(request.conversation_id) if request.conversation_id else None
    if conversation_id:
        repo.conversation(conversation_id, user.id)
    with repo.session() as db:
        db.scalar(select(User).where(User.id == user.id).with_for_update())
        documents = [
            document_snapshot(
                db, visible_document(db, str(doc), user.id, user.tenant_id, conversation_id)
            )
            for doc in sorted(request.document_ids)
        ]
        if sum(len(d["chunks"]) for d in documents) > 2000:
            raise ValueError("单个文档清单最多2000个片段")
        payload = {"schema_version": 1, "conversation_id": conversation_id, "documents": documents}
        hashed = digest(payload)
        prior = db.scalar(
            select(DocumentManifest).where(
                DocumentManifest.user_id == user.id, DocumentManifest.content_hash == hashed
            )
        )
        if prior:
            if prior.tenant_id != user.tenant_id:
                raise NotFound()
            return manifest_info(prior)
        if (
            db.scalar(
                select(func.count())
                .select_from(DocumentManifest)
                .where(DocumentManifest.user_id == user.id)
            )
            >= 100
        ):
            raise Conflict("文档清单达到100个上限，请联系管理员归档")
        row = DocumentManifest(
            id=str(uuid4()),
            user_id=user.id,
            tenant_id=user.tenant_id,
            conversation_id=conversation_id,
            content_hash=hashed,
            sample=any(d["sample"] for d in documents),
            payload=payload,
        )
        db.add(row)
        db.commit()
        return manifest_info(row)


def owned_manifest(
    db, manifest_id, user_id, tenant_id, *, conversation_id=None, enforce_conversation=False
):
    row = db.get(DocumentManifest, manifest_id)
    if not row or row.user_id != user_id or row.tenant_id != tenant_id:
        raise NotFound()
    if enforce_conversation and row.conversation_id and row.conversation_id != conversation_id:
        raise NotFound()
    if digest(row.payload) != row.content_hash:
        raise Conflict("文档清单版本校验失败")
    expected = row.payload.get("documents", [])
    if not expected or len(expected) > 10:
        raise Conflict("文档清单结构不合法")
    for member in expected:
        doc = visible_document(db, member["document_id"], user_id, tenant_id, row.conversation_id)
        if document_snapshot(db, doc) != member:
            raise Conflict("文档内容、片段或所属范围已变化")
    if row.sample != any(d["sample"] for d in expected):
        raise Conflict("文档清单模拟标识不一致")
    return row


def manifest_chunks(db, row):
    # Caller has just verified owned_manifest. Only pinned chunk identities are read.
    return [
        {
            "document_id": d["document_id"],
            "document_name": d["name"],
            "document_hash": d["content_hash"],
            "chunk_id": c["chunk_id"],
            "chunk_hash": c["content_hash"],
            "locator": c["locator"],
            "text": db.get(Chunk, c["chunk_id"]).text,
        }
        for d in row.payload["documents"]
        for c in d["chunks"]
    ]


def list_documents(repo, user, conversation_id):
    repo.conversation(conversation_id, user.id)
    with repo.session() as db:
        rows = db.scalars(
            select(Document)
            .where(
                Document.tenant_id == user.tenant_id,
                or_(Document.user_id.is_(None), Document.user_id == user.id),
                or_(
                    Document.conversation_id.is_(None), Document.conversation_id == conversation_id
                ),
            )
            .order_by(Document.id)
            .limit(201)
        ).all()
        return {
            "documents": [
                {
                    "id": row.id,
                    "name": row.name,
                    "content_hash": row.content_hash,
                    "sample": row.sample,
                    "conversation_id": row.conversation_id,
                }
                for row in rows[:200]
            ],
            "truncated": len(rows) > 200,
        }
