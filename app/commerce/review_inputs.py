"""Explicit fixed-input adapter for the existing commerce_review baseline."""

from app.diagnosis.inputs import bundle_info, owned_bundle
from app.storage.repository import Conflict

REVIEW_BUNDLE_VERSIONS = {
    "graph_version": "commerce-review-bundle-v1",
    "schema_version": "1",
    "prompt_version": "commerce-review-v1",
    "tool_registry_version": "commerce-review-bundle-v1",
}

REVIEW_BUNDLE_V2 = {
    **REVIEW_BUNDLE_VERSIONS,
    "graph_version": "commerce-review-bundle-v2",
    "prompt_version": "commerce-review-v2",
    "tool_registry_version": "commerce-review-bundle-v2",
}
REVIEW_BUNDLE_V3 = {
    **REVIEW_BUNDLE_V2,
    "graph_version": "commerce-review-bundle-v3",
    "prompt_version": "commerce-review-v3",
    "tool_registry_version": "commerce-review-bundle-v3",
}
REVIEW_CONTRACTS = {
    v["graph_version"]: v for v in (REVIEW_BUNDLE_VERSIONS, REVIEW_BUNDLE_V2, REVIEW_BUNDLE_V3)
}


def authorized_review_bundle(db, task):
    parameters = task.parameters
    bundle, sales = owned_bundle(db, parameters["bundle_id"], task.user_id, task.tenant_id)
    if (
        task.bundle_id != bundle.id
        or parameters.get("bundle_hash") != bundle.bundle_hash
        or parameters.get("dataset_id") != sales.id
        or parameters.get("sources") != ["sales"]
        or parameters.get("strategy") != "workflow"
        or parameters.get("start_date") != bundle.payload["period"]["start"]
        or parameters.get("end_date") != bundle.payload["period"]["end"]
    ):
        raise Conflict("固定复盘输入绑定不一致")
    return bundle, sales


def bound_review(repo, ctx):
    """Reauthorize even on cached reads; never trust parameters supplied by a model."""
    with repo.session() as db:
        task = repo._fenced(db, ctx)
        bundle, sales = authorized_review_bundle(db, task)
        info = bundle_info(bundle)
        # Keep the complete validated source: an authorized SKU may legitimately
        # have zero rows. The pure calculation selects scope after validation.
        return info, sales.payload
