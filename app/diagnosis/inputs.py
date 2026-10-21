"""Owner-scoped input admission and execution-time source reauthorization."""

import hashlib
import json
from uuid import uuid4

from sqlalchemy import func, select

from app.commerce.datasets import owned_dataset
from app.diagnosis.schemas import METRIC_VERSION, InputBundleRequest
from app.storage.models import AnalysisInputBundle, User
from app.storage.repository import Conflict, NotFound


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def dataset_digest(payload):
    # Existing sales dataset hash contract; do not silently change its serialization.
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def bundle_info(item):
    return {
        "bundle_id": item.id,
        "bundle_hash": item.bundle_hash,
        **item.payload,
        "created_at": item.created_at.isoformat() + "Z",
    }


def owned_bundle(db, bundle_id, user_id, tenant_id):
    item = db.get(AnalysisInputBundle, bundle_id)
    if not item or item.user_id != user_id or item.tenant_id != tenant_id:
        raise NotFound()
    if digest(item.payload) != item.bundle_hash:
        raise Conflict("输入包版本校验失败")
    sales = owned_dataset(db, item.sales_dataset_id, user_id, tenant_id)
    source_samples = item.payload.get("source_samples", {"sales": item.payload["sample"]})
    if (
        sales.content_hash != item.payload["sales_content_hash"]
        or dataset_digest(sales.payload) != sales.content_hash
        or sales.sample != source_samples.get("sales")
    ):
        raise Conflict("销售数据版本校验失败")
    if item.inventory_dataset_id != item.payload.get(
        "inventory_dataset_id"
    ) or item.document_manifest_id != item.payload.get("document_manifest_id"):
        raise Conflict("输入组合的来源身份不一致")
    if item.inventory_dataset_id:
        from app.diagnosis.sources import owned_inventory

        inventory = owned_inventory(db, item.inventory_dataset_id, user_id, tenant_id)
        if inventory.content_hash != item.payload.get(
            "inventory_content_hash"
        ) or inventory.sample != source_samples.get("inventory"):
            raise Conflict("库存输入版本不一致")
    if item.document_manifest_id:
        from app.diagnosis.sources import owned_manifest

        manifest = owned_manifest(db, item.document_manifest_id, user_id, tenant_id)
        if manifest.content_hash != item.payload.get(
            "document_manifest_hash"
        ) or manifest.sample != source_samples.get("documents"):
            raise Conflict("文档清单输入版本不一致")
    if item.payload["sample"] != any(source_samples.values()) or set(source_samples) != set(
        item.payload["sources"]
    ):
        raise Conflict("输入组合的来源标识不一致")
    return item, sales


def create_bundle(repo, user, request: InputBundleRequest):
    with repo.session() as db:
        db.scalar(select(User).where(User.id == user.id).with_for_update())
        sales = owned_dataset(db, str(request.sales_dataset_id), user.id, user.tenant_id)
        if dataset_digest(sales.payload) != sales.content_hash:
            raise Conflict("销售数据版本校验失败")
        products = {p["sku"]: p for p in sales.payload["products"]}
        categories = {p["category"] for p in products.values()}
        if (
            set(request.allowed_skus) - products.keys()
            or set(request.allowed_categories) - categories
        ):
            raise ValueError("授权范围包含不存在的商品或品类")
        skus = sorted(
            sku
            for sku, p in products.items()
            if (not request.allowed_skus or sku in request.allowed_skus)
            and (not request.allowed_categories or p["category"] in request.allowed_categories)
        )
        if not skus:
            raise ValueError("授权范围没有适用商品")
        payload = {
            "schema_version": 1,
            "sales_dataset_id": sales.id,
            "sales_content_hash": sales.content_hash,
            "inventory_dataset_id": None,
            "inventory_content_hash": None,
            "document_manifest_id": None,
            "document_manifest_hash": None,
            "period": request.period.model_dump(mode="json"),
            "comparison_period": request.comparison_period.model_dump(mode="json"),
            "metric_version": METRIC_VERSION,
            "sample": sales.sample,
            "sources": ["sales"],
            "allowed_skus": skus,
            "allowed_categories": sorted({products[k]["category"] for k in skus}),
        }
        inventory = manifest = None
        if request.inventory_dataset_id:
            from app.diagnosis.sources import owned_inventory

            inventory = owned_inventory(
                db, str(request.inventory_dataset_id), user.id, user.tenant_id
            )
            if {r["sku"] for r in inventory.payload["items"]} - products.keys():
                raise ValueError("库存包含销售版本之外的SKU")
            payload.update(
                inventory_dataset_id=inventory.id,
                inventory_content_hash=inventory.content_hash,
                inventory_policy={
                    "max_age_days": repo.settings.diagnosis_inventory_max_age_days,
                    "algorithm_version": "inventory-proxy-v1",
                },
            )
        if request.document_manifest_id:
            from app.diagnosis.sources import owned_manifest

            manifest = owned_manifest(
                db, str(request.document_manifest_id), user.id, user.tenant_id
            )
            payload.update(
                document_manifest_id=manifest.id,
                document_manifest_hash=manifest.content_hash,
                document_conversation_id=manifest.conversation_id,
            )
        if inventory or manifest:
            samples = {"sales": sales.sample}
            if inventory:
                samples["inventory"] = inventory.sample
            if manifest:
                samples["documents"] = manifest.sample
            payload.update(
                schema_version=2,
                source_samples=samples,
                sources=list(samples),
                sample=any(samples.values()),
            )
        fingerprint = digest(payload)
        existing = db.scalar(
            select(AnalysisInputBundle).where(
                AnalysisInputBundle.user_id == user.id,
                AnalysisInputBundle.bundle_hash == fingerprint,
            )
        )
        if existing:
            # Never return another tenant's metadata even on a corrupt ownership record.
            owned_bundle(db, existing.id, user.id, user.tenant_id)
            return bundle_info(existing)
        count = db.scalar(
            select(func.count())
            .select_from(AnalysisInputBundle)
            .where(AnalysisInputBundle.user_id == user.id)
        )
        if count >= 100:
            raise Conflict("输入包达到100个上限，请联系管理员归档")
        item = AnalysisInputBundle(
            id=str(uuid4()),
            user_id=user.id,
            tenant_id=user.tenant_id,
            sales_dataset_id=sales.id,
            inventory_dataset_id=inventory.id if inventory else None,
            document_manifest_id=manifest.id if manifest else None,
            bundle_hash=fingerprint,
            payload=payload,
        )
        db.add(item)
        db.commit()
        return bundle_info(item)


def get_bundle(repo, user, bundle_id):
    with repo.session() as db:
        item, _ = owned_bundle(db, bundle_id, user.id, user.tenant_id)
        return bundle_info(item)


def bundle_quality(repo, user, bundle_id):
    """Read-only preflight of the exact fixed combination; creates no task/call ledger."""
    from app.diagnosis.extensions import inventory_risk
    from app.diagnosis.metrics import DiagnosticMetrics
    from app.diagnosis.schemas import SALES_TOOLS, Filters
    from app.diagnosis.sources import owned_inventory, owned_manifest

    with repo.session() as db:
        bundle, sales = owned_bundle(db, bundle_id, user.id, user.tenant_id)
        info = bundle_info(bundle)
        inspection = DiagnosticMetrics(sales.payload, info, Filters()).inspect()
        stock = documents = None
        tools = list(SALES_TOOLS)
        if bundle.inventory_dataset_id:
            inventory = owned_inventory(db, bundle.inventory_dataset_id, user.id, user.tenant_id)
            result = inventory_risk(sales.payload, inventory.payload, info, Filters())
            stock = {
                "coverage": result["coverage"],
                "policy": info["inventory_policy"],
                "items": [
                    {k: row[k] for k in ("sku", "status", "reason", "snapshot_day")}
                    for row in result["items"]
                ],
                "limitations": result["limitations"],
            }
            tools.append("inventory_risk")
        if bundle.document_manifest_id:
            manifest = owned_manifest(db, bundle.document_manifest_id, user.id, user.tenant_id)
            documents = {
                "count": len(manifest.payload["documents"]),
                "chunks": sum(len(doc["chunks"]) for doc in manifest.payload["documents"]),
                "conversation_id": manifest.conversation_id,
                "semantic_status": "unverified",
            }
            tools.append("search_rule_documents")
        return {
            "bundle_id": bundle.id,
            "bundle_hash": bundle.bundle_hash,
            "period": info["period"],
            "comparison_period": info["comparison_period"],
            "allowed_skus": info["allowed_skus"],
            "sample": info["sample"],
            "source_samples": info.get("source_samples", {"sales": info["sample"]}),
            "coverage": inspection["coverage"],
            "comparable": inspection["comparable"],
            "supported_metrics": inspection["supported_metrics"],
            "unsupported_metrics": inspection["unsupported_metrics"],
            "available_tools": tools,
            "inventory": stock,
            "documents": documents,
            "limitations": inspection["limitations"],
            "task_created": False,
        }
