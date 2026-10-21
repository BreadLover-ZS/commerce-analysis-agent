"""Single metered tool boundary: authorize, reserve, compute, then atomically publish."""

import copy
import json
import time
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select

from app.diagnosis.contracts import is_extended, tool_schemas
from app.diagnosis.extensions import inventory_risk, search_rules
from app.diagnosis.inputs import bundle_info, digest, owned_bundle
from app.diagnosis.metrics import DiagnosticMetrics
from app.diagnosis.schemas import METRIC_VERSION, Filters
from app.storage.models import Event, Evidence, ToolInvocation, ToolQueryResult, utcnow
from app.storage.repository import Conflict, NotFound

MAX_MODEL_RESULT_BYTES = 32 * 1024


def bound_extensions(db, task, bundle):
    """Read only task-selected fixed sources, after normal bundle authorization."""
    from app.diagnosis.sources import manifest_chunks, owned_inventory, owned_manifest

    result = {}
    sources = task.parameters["sources"]
    if "inventory" in sources:
        result["inventory_payload"] = owned_inventory(
            db, bundle.inventory_dataset_id, task.user_id, task.tenant_id
        ).payload
    if "documents" in sources:
        manifest = owned_manifest(
            db,
            bundle.document_manifest_id,
            task.user_id,
            task.tenant_id,
            conversation_id=task.conversation_id,
            enforce_conversation=True,
        )
        result["document_chunks"] = manifest_chunks(db, manifest)
    return result


def source_hashes(name, info):
    if name == "search_rule_documents":
        return {"documents": info["document_manifest_hash"]}
    values = {"sales": info["sales_content_hash"]}
    if name == "inventory_risk":
        values["inventory"] = info["inventory_content_hash"]
    return values


def model_summary(result):
    """Full result remains in authorized Evidence; bound what is sent to a model."""
    value = copy.deepcopy(result)
    value.pop("all_contributors", None)
    if len(json.dumps(value, ensure_ascii=False).encode()) <= MAX_MODEL_RESULT_BYTES:
        return value
    for key, maximum in (("series", 10), ("products", 10), ("contributors", 10)):
        if key in value:
            value[key] = value[key][:maximum]
    referenced = [*value.get("fact_keys", {}).values(), *value.get("ratio_fact_keys", {}).values()]
    for row in value.get("contributors", []):
        referenced.extend([row["fact_key"], row.get("share_fact_key")])
    for row in value.get("series", []):
        referenced.extend(row.get("fact_keys", {}).values())
    catalog = value.get("fact_catalog", {})
    referenced.extend(k for k in ("contribution.other", "contribution.total") if k in catalog)
    keys = list(dict.fromkeys(k for k in referenced if k in catalog)) or list(catalog)[:20]
    value["fact_catalog"] = {k: value["fact_catalog"][k] for k in keys}
    value["summary_truncated"] = True
    value["limitations"] = value.get("limitations", []) + ["模型摘要已截断；完整结果见授权证据。"]
    if len(json.dumps(value, ensure_ascii=False).encode()) > MAX_MODEL_RESULT_BYTES:
        value = {
            k: value[k]
            for k in (
                "call_id",
                "status",
                "bundle_id",
                "tool",
                "query_spec_hash",
                "evidence_ids",
                "coverage",
            )
            if k in value
        }
        value.update(summary_truncated=True, limitations=["模型摘要超限，完整结果见授权证据。"])
    return value


class DiagnosisToolGateway:
    def __init__(self, repo, ctx):
        self.repo, self.ctx = repo, ctx

    def _authorize(self, db):
        task = self.repo._fenced(db, self.ctx)
        if task.user_id != self.ctx.user_id or task.tenant_id != self.ctx.tenant_id:
            raise NotFound()
        parameters = task.parameters
        if parameters.get("profile") != "commerce_diagnosis" or not parameters.get("bundle_id"):
            raise Conflict("任务未绑定经营诊断输入包")
        bundle, sales = owned_bundle(db, parameters["bundle_id"], task.user_id, task.tenant_id)
        if (task.bundle_id is not None and task.bundle_id != bundle.id) or (
            parameters.get("bundle_hash") and parameters["bundle_hash"] != bundle.bundle_hash
        ):
            raise Conflict("任务输入身份已变化")
        sources = parameters.get("sources", [])
        if (
            not sources
            or len(sources) != len(set(sources))
            or "sales" not in sources
            or set(sources) - set(bundle.payload["sources"])
            or bundle.payload["metric_version"] != METRIC_VERSION
        ):
            raise Conflict("任务来源或指标版本不可用")
        if "documents" in sources:
            from app.diagnosis.sources import owned_manifest

            owned_manifest(
                db,
                bundle.document_manifest_id,
                task.user_id,
                task.tenant_id,
                conversation_id=task.conversation_id,
                enforce_conversation=True,
            )
        return task, bundle, sales

    def catalog(self):
        # Catalogs and cached results have the same live authorization as execution.
        with self.repo.session() as db:
            task, _, _ = self._authorize(db)
            schemas = tool_schemas(
                {"graph_version": task.graph_version, "parameters": task.parameters}
            )
        return {name: schema.model_json_schema() for name, schema in schemas.items()}

    def invoke(self, name, arguments, step_id):
        if not isinstance(step_id, str) or not 1 <= len(step_id) <= 80:
            raise ValueError("需要有效步骤身份")
        # Check access even for invalid tools/parameters; no metadata side channel.
        with self.repo.session() as db:
            task, bundle, _ = self._authorize(db)
            schemas = tool_schemas(
                {"graph_version": task.graph_version, "parameters": task.parameters}
            )
            version = task.tool_registry_version
            rejected = {
                "bundle_id": bundle.id,
                "bundle_hash": bundle.bundle_hash,
                "source_hashes": {"sales": bundle.payload["sales_content_hash"]},
                "tool": name if name in schemas else None,
                "tool_version": version,
                "metric_version": METRIC_VERSION,
                "query_spec_hash": None,
                "evidence_ids": [],
                "fact_catalog": {},
                "coverage": {},
                "limitations": [],
            }
        schema = schemas.get(name)
        if not schema:
            return {**rejected, "status": "unavailable", "error_code": "tool_not_available"}
        try:
            args = schema.model_validate(arguments)
        except ValidationError as exc:
            unsupported = any(e["loc"] == ("metric",) for e in exc.errors(include_input=False))
            return {
                **rejected,
                "status": "unsupported_metric" if unsupported else "invalid_argument",
                "error_code": "unsupported_metric" if unsupported else "invalid_argument",
            }
        started = time.monotonic()
        with self.repo.session() as db:
            task, bundle, sales = self._authorize(db)
            info = bundle_info(bundle)
            extras = (
                bound_extensions(db, task, bundle)
                if is_extended({"graph_version": task.graph_version})
                else {}
            )
            try:
                calculation = DiagnosticMetrics(
                    sales.payload, info, getattr(args, "filters", Filters())
                )
            except ValueError:
                return {
                    **rejected,
                    "status": "invalid_argument",
                    "error_code": "filter_not_authorized",
                }
            normalized = args.model_dump(mode="json")
            if "filters" in normalized:
                normalized["filters"] = {"skus": calculation.skus, "categories": []}
            spec = {
                "task_id": task.id,
                "bundle_id": bundle.id,
                "bundle_hash": bundle.bundle_hash,
                "source_hashes": source_hashes(name, info),
                "sources": sorted(task.parameters["sources"]),
                "tool": name,
                "tool_version": version,
                "metric_version": METRIC_VERSION,
                "arguments": normalized,
                "period": info["period"],
                "comparison_period": info["comparison_period"],
            }
            spec_hash = digest(spec)
            query = db.scalar(
                select(ToolQueryResult).where(
                    ToolQueryResult.task_id == task.id, ToolQueryResult.spec_hash == spec_hash
                )
            )
            if query and query.result is not None:
                if digest(query.result) != query.result_digest:
                    raise Conflict("缓存结果版本校验失败")
                return {**query.result, "reused": True}
            if not query:
                query = ToolQueryResult(
                    id=str(uuid4()),
                    task_id=task.id,
                    spec_hash=spec_hash,
                    tool_name=name,
                    status="PLANNED",
                    query_spec=spec,
                )
                db.add(query)
                db.flush()
            prior = list(
                db.scalars(
                    select(ToolInvocation)
                    .where(ToolInvocation.task_id == task.id, ToolInvocation.spec_hash == spec_hash)
                    .order_by(ToolInvocation.attempt)
                )
            )
            for previous in prior:
                if previous.status == "STARTED":
                    if previous.lease_token == self.ctx.lease_token:
                        return {
                            **rejected,
                            "status": "unavailable",
                            "query_spec_hash": spec_hash,
                            "error_code": "call_in_progress",
                        }
                    previous.status, previous.error_code = "UNKNOWN", "interrupted"
                    previous.completed_at = utcnow()
            self.repo._consume_locked(db, task, "tool", 10)
            call_id = str(uuid4())
            db.add(
                ToolInvocation(
                    call_id=call_id,
                    task_id=task.id,
                    query_result_id=query.id,
                    spec_hash=spec_hash,
                    attempt=len(prior) + 1,
                    step_id=step_id,
                    status="STARTED",
                    lease_token=self.ctx.lease_token,
                )
            )
            db.add(
                Event(
                    task_id=task.id,
                    kind="diagnosis_tool_started",
                    message="已授权诊断工具调用",
                    data={"call_id": call_id, "step_id": step_id, "tool": name},
                )
            )
            db.commit()
        try:
            # No task row lock while the business tool runs.
            result = (
                self._calculate(name, args, calculation, extras, registry_version=version)
                if version == "3"
                else self._calculate(name, args, calculation, extras)
            )
        except Exception:
            # Persist a typed, safe failure; never send database/input exception text to a model.
            self._failed(call_id, "tool_execution_failed", started)
            return {
                **rejected,
                "status": "unavailable",
                "call_id": call_id,
                "error_code": "tool_execution_failed",
            }
        return self._publish(call_id, spec, spec_hash, result, started)

    @staticmethod
    def _calculate(name, args, calculation, extras=None, *, registry_version="2"):
        if name == "inventory_risk":
            return inventory_risk(
                calculation.cube.payload,
                extras["inventory_payload"],
                calculation.bundle,
                args.filters,
            )
        if name == "search_rule_documents":
            retrieve = search_rules
            if registry_version == "3":
                from app.diagnosis.rule_retrieval_v2 import search_rules_v2

                retrieve = search_rules_v2
            return retrieve(
                extras["document_chunks"],
                calculation.bundle["document_manifest_id"],
                args.query,
                args.limit,
            )
        if name == "inspect_dataset":
            return calculation.inspect()
        if name == "compare_periods":
            return calculation.compare(args.metric)
        if name == "rank_contributors":
            return calculation.rank(args.dimension, args.top_k)
        if name == "drilldown_series":
            return calculation.series()
        return calculation.refunds()

    def _failed(self, call_id, error_code, started):
        with self.repo.session() as db:
            self._authorize(db)
            call = db.get(ToolInvocation, call_id)
            call.status, call.error_code = "FAILED", error_code
            call.elapsed_ms = int((time.monotonic() - started) * 1000)
            call.completed_at = utcnow()
            db.commit()

    def _publish(self, call_id, spec, spec_hash, result, started):
        with self.repo.session() as db:
            task, bundle, _ = self._authorize(db)
            call = db.get(ToolInvocation, call_id)
            query = db.get(ToolQueryResult, call.query_result_id)
            evidence_id = "E-" + digest({"task": task.id, "spec": spec_hash})[:24]
            envelope = {
                **result,
                "call_id": call_id,
                "bundle_id": bundle.id,
                "bundle_hash": bundle.bundle_hash,
                "source_hashes": spec["source_hashes"],
                "tool": spec["tool"],
                "tool_version": spec["tool_version"],
                "query_spec_hash": spec_hash,
                "evidence_ids": [evidence_id],
                "metric_version": METRIC_VERSION,
            }
            for fact in envelope.get("fact_catalog", {}).values():
                fact["evidence_id"] = evidence_id
            raw = json.dumps(envelope, ensure_ascii=False, sort_keys=True)
            # This bypasses the historical metric_scope writer. A query spec owns one result.
            db.add(
                Evidence(
                    id=evidence_id,
                    task_id=task.id,
                    source_key=spec_hash,
                    kind="diagnosis_metrics",
                    title=f"诊断工具：{spec['tool']}",
                    locator=f"diagnosis://{bundle.id}/{spec_hash}",
                    excerpt=raw[:8000],
                    payload=envelope,
                    content_hash=digest(envelope),
                )
            )
            query.result, query.result_digest, query.status = (
                envelope,
                digest(envelope),
                "SUCCEEDED",
            )
            call.status, call.completed_at = "SUCCEEDED", utcnow()
            call.elapsed_ms = int((time.monotonic() - started) * 1000)
            db.add(
                Event(
                    task_id=task.id,
                    kind="diagnosis_tool_completed",
                    message="诊断证据已登记",
                    data={"call_id": call_id, "tool": spec["tool"], "evidence_ids": [evidence_id]},
                )
            )
            db.commit()
            return envelope
