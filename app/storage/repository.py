import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import create_engine, event, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.auth import password_hash
from app.storage.evidence import metric_scope
from app.storage.models import (
    Artifact,
    Conversation,
    Event,
    Evidence,
    InputRequest,
    Task,
    User,
    utcnow,
)

TERMINAL = {"succeeded", "degraded", "failed", "cancelled", "rejected", "expired"}
ACTIVE = {"queued", "running", "cancelling", "waiting_review", "waiting_input"}
MAX_STORED_TOKENS = 2_147_483_647  # PostgreSQL INTEGER columns, also enforced on SQLite.


def usage_counters_valid(task):
    return all(
        type(value) is int and 0 <= value <= MAX_STORED_TOKENS
        for value in (task.input_tokens, task.output_tokens)
    )


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


class BudgetExceeded(Exception):
    pass


class StaleLease(Exception):
    pass


class TaskCancelled(Exception):
    pass


@dataclass(frozen=True)
class RunContext:
    task_id: str
    user_id: str
    tenant_id: str
    conversation_id: str
    lease_token: str


def validate_task_lease(task, ctx, allow_cancel=False):
    """Shared admission rule; callers must hold the task row/write lock."""
    if (
        not task
        or task.lease_token != ctx.lease_token
        or not task.lease_until
        or task.lease_until <= utcnow()
    ):
        raise StaleLease("执行租约已失效")
    if task.status == "cancelling" and not allow_cancel:
        raise TaskCancelled()
    if task.status not in {"running", "cancelling"}:
        raise StaleLease("任务已结束")


def evidence_dict(item: Evidence) -> dict:
    return {
        "id": item.id,
        "kind": item.kind,
        "title": item.title,
        "locator": item.locator,
        "excerpt": item.excerpt,
        "payload": item.payload,
        "content_hash": item.content_hash,
        "retrieved_at": item.created_at.isoformat() + "Z",
    }


def task_dict(item: Task) -> dict:
    return {
        "id": item.id,
        "conversation_id": item.conversation_id,
        "query": item.query,
        "parameters": item.parameters,
        "mode": item.mode,
        "status": item.status,
        "created_at": item.created_at.isoformat() + "Z",
        "updated_at": item.updated_at.isoformat() + "Z",
        "attempts": item.attempts,
        "report": item.report,
        "validation": item.validation,
        "error_code": item.error_code,
        "bundle_id": item.bundle_id,
        "parent_task_id": item.parent_task_id,
        "graph_version": item.graph_version,
        "schema_version": item.schema_version,
        "prompt_version": item.prompt_version,
        "tool_registry_version": item.tool_registry_version,
        "state_version": item.state_version,
        "report_version": item.report_version,
        "report_hash": item.report_hash,
        "pending_interrupt_id": item.pending_interrupt_id,
        "waiting_until": item.waiting_until.isoformat() + "Z" if item.waiting_until else None,
        "metrics": {
            "runtime_ms": item.runtime_ms,
            "tool_calls": item.tool_calls,
            "model_calls": item.model_calls,
            "input_tokens": item.input_tokens,
            "output_tokens": item.output_tokens,
            "usage_complete": item.usage_complete
            and item.usage_reports == item.model_calls
            and usage_counters_valid(item),
        },
    }


class Repository:
    def __init__(self, settings):
        self.settings = settings
        settings.prepare_dirs()
        kwargs = {"pool_pre_ping": True}
        if settings.database_url.startswith("sqlite:"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 20}
        self.engine = create_engine(settings.database_url, **kwargs)
        if self.engine.dialect.name == "sqlite":

            @event.listens_for(self.engine, "connect")
            def sqlite_connection(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA busy_timeout=20000")

        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.dummy_password_hash = password_hash("dummy-user-verification")

    @contextmanager
    def session(self):
        with self.sessions() as db:
            if self.engine.dialect.name == "sqlite":
                # SQLite is a local-development backend: serialize short transactions.
                # PostgreSQL uses row locks for concurrent admission and lease fencing.
                db.execute(text("BEGIN IMMEDIATE"))
            yield db

    def conversation(self, conversation_id, user_id):
        with self.session() as db:
            item = db.get(Conversation, conversation_id)
            if not item or item.user_id != user_id:
                raise NotFound()
            return item

    def create_conversation(self, user_id, title="新建经营分析"):
        with self.session() as db:
            item = Conversation(id=str(uuid4()), user_id=user_id, title=title[:120])
            db.add(item)
            db.commit()
            return item.id

    def _bounded_commerce_budget(self):
        return {
            "max_tool_calls": min(10, self.settings.max_tool_calls),
            "max_model_calls": min(12, self.settings.max_model_calls),
            "max_tokens": self.settings.max_tokens,
            "max_runtime_seconds": self.settings.max_runtime_seconds,
            "max_attempts": self.settings.max_attempts,
        }

    def create_task(self, user, request, idempotency_key):
        payload = request.model_dump(mode="json")
        if request.profile != "commerce_diagnosis":
            # Preserve the pre-migration idempotency hash for legacy profiles.
            if not request.bundle_id:
                payload.pop("bundle_id", None)
            payload.pop("parent_task_id", None)
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with self.session() as db:
            # Serialize admission per user in PostgreSQL, including duplicates.
            db.scalar(select(User).where(User.id == user.id).with_for_update())
            versions = {}
            bundle_id = parent_task_id = None
            if request.profile == "commerce_diagnosis":
                from app.diagnosis.contracts import accepted_versions
                from app.diagnosis.inputs import owned_bundle

                bundle_id, parent_task_id = request.bundle_id, request.parent_task_id
                if parent_task_id:
                    parent = db.get(Task, parent_task_id)
                    if (
                        not parent
                        or parent.user_id != user.id
                        or parent.tenant_id != user.tenant_id
                        or parent.parameters.get("profile") != "commerce_diagnosis"
                    ):
                        raise NotFound()
                    bundle_id = bundle_id or parent.bundle_id
                bundle, _ = owned_bundle(db, bundle_id, user.id, user.tenant_id)
                if set(request.sources) - set(bundle.payload["sources"]):
                    raise ValueError("任务来源超出输入包授权")
                period = bundle.payload["period"]
                if any(
                    name in request.model_fields_set and payload[name] != period[key]
                    for name, key in (("start_date", "start"), ("end_date", "end"))
                ):
                    raise ValueError("诊断日期与输入包不一致")
                if "documents" in request.sources:
                    from app.diagnosis.sources import owned_manifest

                    owned_manifest(
                        db,
                        bundle.document_manifest_id,
                        user.id,
                        user.tenant_id,
                        conversation_id=request.conversation_id,
                        enforce_conversation=True,
                    )
                versions = accepted_versions(
                    request.query, request.sources, self.settings.diagnosis_runtime
                )
                payload.update(
                    start_date=period["start"],
                    end_date=period["end"],
                    bundle_id=bundle_id,
                    parent_task_id=parent_task_id,
                    bundle_hash=bundle.bundle_hash,
                    **versions,
                )
                payload["budget"] = self._bounded_commerce_budget()
                fingerprint = hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode()
                ).hexdigest()
            elif request.profile == "commerce_review" and request.bundle_id:
                from app.commerce.review_inputs import REVIEW_BUNDLE_VERSIONS
                from app.diagnosis.inputs import owned_bundle

                bundle, sales = owned_bundle(db, request.bundle_id, user.id, user.tenant_id)
                if request.dataset_id and request.dataset_id != sales.id:
                    raise ValueError("固定复盘数据集与输入组合不一致")
                period = bundle.payload["period"]
                if any(
                    name in request.model_fields_set and payload[name] != period[key]
                    for name, key in (("start_date", "start"), ("end_date", "end"))
                ):
                    raise ValueError("固定复盘日期与输入组合不一致")
                bundle_id, versions = bundle.id, dict(REVIEW_BUNDLE_VERSIONS)
                payload.update(
                    start_date=period["start"],
                    end_date=period["end"],
                    dataset_id=sales.id,
                    bundle_hash=bundle.bundle_hash,
                    **versions,
                )
                payload["budget"] = self._bounded_commerce_budget()
                fingerprint = hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode()
                ).hexdigest()
            prior = db.scalar(
                select(Task).where(Task.user_id == user.id, Task.idempotency_key == idempotency_key)
            )
            if prior:
                # Server version/budget changes must not turn an identical client
                # retry into a new contract. Preserve old stored fingerprints.
                server_fields = {
                    "budget",
                    "bundle_hash",
                    "graph_version",
                    "schema_version",
                    "prompt_version",
                    "tool_registry_version",
                }
                prior_hash_valid = (
                    hashlib.sha256(
                        json.dumps(prior.parameters, sort_keys=True).encode()
                    ).hexdigest()
                    == prior.request_hash
                )
                if bundle_id and not prior_hash_valid:
                    raise Conflict("已登记任务的输入或预算版本校验失败")
                same_bound_request = (
                    bool(bundle_id)
                    and prior_hash_valid
                    and {k: v for k, v in prior.parameters.items() if k not in server_fields}
                    == {k: v for k, v in payload.items() if k not in server_fields}
                )
                if prior.request_hash != fingerprint and not same_bound_request:
                    raise Conflict("幂等键已用于另一项请求")
                return task_dict(prior)
            conversation = db.get(Conversation, request.conversation_id)
            if not conversation or conversation.user_id != user.id:
                raise NotFound()
            if request.profile == "commerce_review":
                from app.commerce.datasets import owned_dataset

                owned_dataset(db, payload["dataset_id"], user.id, user.tenant_id)
            count = db.scalar(
                select(func.count())
                .select_from(Task)
                .where(Task.user_id == user.id, Task.status.in_(ACTIVE))
            )
            if count >= self.settings.max_active_tasks:
                raise Conflict("活跃任务达到上限，请先完成或取消已有任务")
            task = Task(
                id=str(uuid4()),
                conversation_id=conversation.id,
                user_id=user.id,
                tenant_id=user.tenant_id,
                idempotency_key=idempotency_key,
                request_hash=fingerprint,
                query=request.query,
                parameters=payload,
                mode=self.settings.agent_mode,
                bundle_id=bundle_id,
                parent_task_id=parent_task_id,
                **versions,
            )
            db.add(task)
            conversation.title = request.query[:120]
            db.flush()
            db.add(Event(task_id=task.id, kind="queued", message="任务已登记，等待执行"))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                prior = db.scalar(
                    select(Task).where(
                        Task.user_id == user.id, Task.idempotency_key == idempotency_key
                    )
                )
                if not prior or prior.request_hash != fingerprint:
                    raise Conflict("重复请求冲突") from None
                return task_dict(prior)
            return task_dict(task)

    def get_task(self, task_id, user_id=None):
        with self.session() as db:
            task = db.get(Task, task_id)
            if not task or (user_id and task.user_id != user_id):
                raise NotFound()
            if (
                user_id
                and task.parameters.get("profile") == "commerce_review"
                and task.parameters.get("bundle_id")
            ):
                from app.commerce.review_inputs import authorized_review_bundle

                owner = db.get(User, user_id)
                if not owner or owner.tenant_id != task.tenant_id:
                    raise NotFound()
                authorized_review_bundle(db, task)
            return task_dict(task)

    def list_tasks(self, user_id, limit=50):
        with self.session() as db:
            tasks = db.scalars(
                select(Task)
                .where(Task.user_id == user_id)
                .order_by(Task.created_at.desc())
                .limit(limit)
            )
            return [
                {k: v for k, v in task_dict(t).items() if k not in {"report", "validation"}}
                for t in tasks
            ]

    def claim(self):
        now = utcnow()
        with self.session() as db:
            # Waiting rows occupy active quota but no worker slot. Also reconcile
            # the crash window after an input record and before finish(waiting_*).
            waiting = db.scalars(
                select(Task)
                .where(
                    Task.status.in_(ACTIVE),
                    Task.pending_interrupt_id.is_not(None),
                    Task.waiting_until <= now,
                )
                .with_for_update(skip_locked=True)
                .limit(20)
            )
            for row in waiting:
                pending = db.get(InputRequest, row.pending_interrupt_id)
                if pending and pending.decision is None:
                    if row.execution_started_at:
                        ended = min(now, pending.created_at)
                        row.runtime_ms += max(
                            0, int((ended - row.execution_started_at).total_seconds() * 1000)
                        )
                    row.execution_started_at, row.deadline_at = None, None
                    row.status, row.error_code = "expired", "waiting_expired"
                    row.lease_token, row.lease_until = None, None
                    row.state_version += 1
                    db.add(Event(task_id=row.id, kind="expired", message="人工输入等待已过期"))
            db.flush()
            task = db.scalar(
                select(Task)
                .where(
                    or_(
                        Task.status == "queued",
                        Task.status.in_(["running", "cancelling"]) & (Task.lease_until < now),
                    )
                )
                .order_by(Task.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if not task:
                db.commit()
                return None
            diagnosis = task.parameters.get("profile") == "commerce_diagnosis"
            runtime_ms = task.runtime_ms
            if diagnosis and task.execution_started_at is not None:
                pending = (
                    db.get(InputRequest, task.pending_interrupt_id)
                    if task.pending_interrupt_id
                    else None
                )
                segment_end = (
                    min(now, pending.created_at) if pending and pending.decision is None else now
                )
                runtime_ms += max(
                    0, int((segment_end - task.execution_started_at).total_seconds() * 1000)
                )
            runtime_limit = self.task_limit(task, "max_runtime_seconds")
            expired_budget = (
                runtime_ms >= runtime_limit * 1000
                if diagnosis
                else task.status == "running" and task.deadline_at and task.deadline_at <= now
            )
            exhausted = (
                task.status == "running"
                and task.recovery_attempts >= self.task_limit(task, "max_attempts")
                if diagnosis
                else task.attempts >= self.task_limit(task, "max_attempts")
            )
            if task.status == "cancelling" or exhausted or expired_budget:
                task.status = "cancelled" if task.status == "cancelling" else "failed"
                task.error_code = (
                    None
                    if task.status == "cancelled"
                    else "runtime_budget"
                    if expired_budget
                    else "recovery_exhausted"
                )
                task.lease_token, task.lease_until = None, None
                task.updated_at = now
                if diagnosis:
                    task.runtime_ms, task.execution_started_at = runtime_ms, None
                db.add(
                    Event(
                        task_id=task.id,
                        kind=task.status,
                        message="任务已取消"
                        if task.status == "cancelled"
                        else "执行预算或恢复次数达到上限",
                    )
                )
                db.commit()
                return None
            token = str(uuid4())
            prior_token = task.lease_token
            deadline = (
                now + timedelta(seconds=max(0, runtime_limit - runtime_ms / 1000))
                if diagnosis
                else task.deadline_at
                if task.status == "running" and task.deadline_at is not None
                else now + timedelta(seconds=max(0, runtime_limit - task.runtime_ms / 1000))
            )
            # CAS also protects local SQLite's single-worker development path.
            result = db.execute(
                update(Task)
                .where(
                    Task.id == task.id, Task.lease_token == prior_token, Task.status == task.status
                )
                .values(
                    status="running",
                    lease_token=token,
                    lease_until=now + timedelta(seconds=self.settings.lease_seconds),
                    attempts=Task.attempts + 1,
                    deadline_at=deadline,
                    updated_at=now,
                    **(
                        {
                            "runtime_ms": runtime_ms,
                            "execution_started_at": now,
                            "recovery_attempts": task.recovery_attempts
                            + int(task.status == "running"),
                        }
                        if diagnosis
                        else {}
                    ),
                )
            )
            if result.rowcount != 1:
                db.rollback()
                return None
            db.add(
                Event(
                    task_id=task.id,
                    kind="running" if task.attempts == 1 else "recovered",
                    message="开始执行" if task.attempts == 1 else "重新领取任务并恢复检查点",
                )
            )
            db.commit()
            db.refresh(task)
            return RunContext(task.id, task.user_id, task.tenant_id, task.conversation_id, token)

    def _fenced(self, db, ctx, allow_cancel=False):
        task = db.scalar(select(Task).where(Task.id == ctx.task_id).with_for_update())
        validate_task_lease(task, ctx, allow_cancel)
        if (
            not allow_cancel
            and task.parameters.get("profile") == "commerce_review"
            and task.parameters.get("bundle_id")
        ):
            from app.commerce.review_inputs import authorized_review_bundle

            owner = db.get(User, task.user_id)
            if (
                (task.user_id, task.tenant_id, task.conversation_id)
                != (ctx.user_id, ctx.tenant_id, ctx.conversation_id)
                or not owner
                or owner.tenant_id != task.tenant_id
            ):
                raise NotFound()
            authorized_review_bundle(db, task)
        return task

    def heartbeat(self, ctx):
        with self.session() as db:
            task = self._fenced(db, ctx, allow_cancel=True)
            task.lease_until = utcnow() + timedelta(seconds=self.settings.lease_seconds)
            db.commit()
            return task.status

    def remaining_runtime(self, ctx):
        with self.session() as db:
            task = self._fenced(db, ctx, allow_cancel=True)
            return max(0, (task.deadline_at - utcnow()).total_seconds())

    def release(self, ctx):
        with self.session() as db:
            db.execute(
                update(Task)
                .where(Task.id == ctx.task_id, Task.lease_token == ctx.lease_token)
                .values(lease_until=utcnow())
            )
            db.commit()

    def emit(self, ctx, kind, message, data=None):
        with self.session() as db:
            self._fenced(db, ctx)
            db.add(Event(task_id=ctx.task_id, kind=kind, message=message[:500], data=data or {}))
            db.commit()

    def consume(self, ctx, resource):
        with self.session() as db:
            task = self._fenced(db, ctx)
            self._consume_locked(db, task, resource)
            db.commit()

    def task_limit(self, task, name):
        limit = getattr(self.settings, name)
        if task.parameters.get("profile") == "commerce_diagnosis" or (
            task.parameters.get("profile") == "commerce_review" and task.parameters.get("bundle_id")
        ):
            stored = task.parameters.get("budget", {}).get(name, limit)
            if type(stored) is not int or stored <= 0:
                raise Conflict("任务预算契约不合法")
            limit = min(limit, stored)
        return limit

    def _consume_locked(self, db, task, resource, hard_limit=None):
        """Shared reservation used by the legacy meter and durable diagnosis ledgers.

        Caller holds the Task lock and commits STARTED in the same transaction.
        Never call consume again outside that transaction for the same attempt.
        """
        if resource == "tool":
            configured = self.task_limit(task, "max_tool_calls")
            limit = min(configured, hard_limit or configured)
            if task.tool_calls >= limit:
                raise BudgetExceeded("tool_budget")
            task.tool_calls += 1
        else:
            if not task.usage_complete or not usage_counters_valid(task):
                if task.usage_complete:
                    task.usage_complete = False
                    db.commit()
                raise BudgetExceeded("模型Token使用量不完整，停止后续调用")
            configured = self.task_limit(task, "max_model_calls")
            limit = min(configured, hard_limit or configured)
            if (
                task.model_calls >= limit
                or task.input_tokens + task.output_tokens >= self.task_limit(task, "max_tokens")
            ):
                raise BudgetExceeded("model_budget")
            task.model_calls += 1

    @staticmethod
    def _record_usage_locked(task, usage):
        task.usage_reports += 1
        valid = (
            isinstance(usage, dict)
            and usage_counters_valid(task)
            and all(
                type(usage.get(name)) is int
                and 0 <= usage[name] <= MAX_STORED_TOKENS - getattr(task, name)
                for name in ("input_tokens", "output_tokens")
            )
        )
        if not valid:
            task.usage_complete = False
        else:
            task.input_tokens += usage["input_tokens"]
            task.output_tokens += usage["output_tokens"]
        return valid

    def record_usage(self, ctx, usage):
        with self.session() as db:
            task = self._fenced(db, ctx, allow_cancel=True)
            valid = self._record_usage_locked(task, usage)
            db.commit()
            return valid

    def account_unreported_usage(self, ctx):
        with self.session() as db:
            task = self._fenced(db, ctx, allow_cancel=True)
            if task.usage_reports < task.model_calls:
                task.usage_complete = False
            db.commit()

    def _metric_snapshot(self, db, task_id, scope):
        if scope is None:
            return None
        rows = db.scalars(
            select(Evidence)
            .where(Evidence.task_id == task_id, Evidence.kind == "metrics")
            .order_by(Evidence.created_at, Evidence.id)
        )
        return next((item for item in rows if metric_scope(item.payload) == scope), None)

    def metric_snapshot(self, ctx, name, start, end):
        with self.session() as db:
            self._fenced(db, ctx)
            item = self._metric_snapshot(db, ctx.task_id, (name, start, end))
            return evidence_dict(item) if item else None

    def add_evidence(self, ctx, kind, title, locator, excerpt, payload=None):
        excerpt = excerpt[:8000]
        payload = dict(payload or {})
        key_payload = payload if kind == "metrics" else {}
        content = json.dumps(
            {"kind": kind, "locator": locator, "excerpt": excerpt, "payload": key_payload},
            ensure_ascii=False,
            sort_keys=True,
        )
        key = hashlib.sha256(content.encode()).hexdigest()
        with self.session() as db:
            self._fenced(db, ctx)
            # The task row lock serializes first writers, including concurrent replay.
            if kind == "metrics":
                snapshot = self._metric_snapshot(db, ctx.task_id, metric_scope(payload))
                if snapshot:
                    return evidence_dict(snapshot)
            prior = db.scalar(
                select(Evidence).where(Evidence.task_id == ctx.task_id, Evidence.source_key == key)
            )
            if prior:
                if payload.get("query"):
                    queries = list(
                        dict.fromkeys(
                            prior.payload.get("queries", [prior.payload.get("query")])
                            + [payload["query"]]
                        )
                    )
                    prior.payload = {**prior.payload, "queries": [q for q in queries if q]}
                    db.commit()
                return evidence_dict(prior)
            if payload.get("query"):
                payload["queries"] = [payload["query"]]
            item = Evidence(
                id="E-" + hashlib.sha256((ctx.task_id + key).encode()).hexdigest()[:24],
                task_id=ctx.task_id,
                source_key=key,
                kind=kind,
                title=title[:300],
                locator=locator[:2000],
                excerpt=excerpt[:8000],
                payload=payload,
                content_hash=hashlib.sha256(excerpt.encode()).hexdigest(),
            )
            db.add(item)
            db.commit()
            return evidence_dict(item)

    def evidence(self, task_id):
        with self.session() as db:
            return [
                evidence_dict(e)
                for e in db.scalars(
                    select(Evidence)
                    .where(Evidence.task_id == task_id)
                    .order_by(Evidence.created_at, Evidence.id)
                )
            ]

    def events(self, task_id, after=0, limit=100):
        with self.session() as db:
            return [
                {
                    "id": e.id,
                    "kind": e.kind,
                    "message": e.message,
                    "data": e.data,
                    "timestamp": e.created_at.isoformat() + "Z",
                }
                for e in db.scalars(
                    select(Event)
                    .where(Event.task_id == task_id, Event.id > after)
                    .order_by(Event.id)
                    .limit(limit)
                )
            ]

    def save_report(self, ctx, report, validation):
        with self.session() as db:
            task = self._fenced(db, ctx)
            if task.parameters.get("profile") == "commerce_diagnosis":
                raise Conflict("诊断报告须经版本化报告入口登记")
            task.report, task.validation = report, validation
            db.commit()

    def add_artifact(self, ctx, name, storage_key, content):
        with self.session() as db:
            task = self._fenced(db, ctx)
            if task.parameters.get("profile") == "commerce_diagnosis":
                raise Conflict("诊断产物须按批准的报告版本发布")
            artifact = db.scalar(
                select(Artifact).where(Artifact.task_id == ctx.task_id, Artifact.name == name)
            )
            if not artifact:
                artifact = Artifact(id=str(uuid4()), task_id=ctx.task_id, name=name)
                db.add(artifact)
            artifact.storage_key, artifact.size = storage_key, len(content)
            artifact.sha256 = hashlib.sha256(content).hexdigest()
            db.commit()

    def artifacts(self, task_id):
        with self.session() as db:
            return [
                {
                    "id": a.id,
                    "name": a.name,
                    "size": a.size,
                    "sha256": a.sha256,
                    **(
                        {"report_version": a.report_version, "report_hash": a.report_hash}
                        if a.report_version
                        else {}
                    ),
                }
                for a in db.scalars(select(Artifact).where(Artifact.task_id == task_id))
            ]

    def finish(self, ctx, status, elapsed_ms, error_code=None):
        with self.session() as db:
            task = self._fenced(db, ctx, allow_cancel=True)
            task.status = "cancelled" if task.status == "cancelling" else status
            if task.parameters.get("profile") == "commerce_diagnosis":
                now = utcnow()
                pending = (
                    db.get(InputRequest, task.pending_interrupt_id)
                    if task.pending_interrupt_id
                    else None
                )
                ended = (
                    min(now, pending.created_at) if pending and pending.decision is None else now
                )
                if task.execution_started_at:
                    task.runtime_ms += max(
                        0, int((ended - task.execution_started_at).total_seconds() * 1000)
                    )
                task.execution_started_at = None
                task.deadline_at = None
            else:
                task.runtime_ms += elapsed_ms
            task.error_code = error_code
            task.lease_token, task.lease_until = None, None
            task.updated_at = utcnow()
            db.add(
                Event(
                    task_id=task.id,
                    kind=task.status,
                    message={
                        "succeeded": "报告已生成",
                        "degraded": "报告已生成，部分信息缺失",
                        "waiting_review": "草稿已保存，等待人工复核",
                        "waiting_input": "诊断暂停，等待范围内的澄清答复",
                        "expired": "人工等待已过期",
                        "cancelled": "任务已取消",
                        "rejected": "草稿已驳回",
                        "failed": "任务执行失败，请查看错误代码",
                    }[task.status],
                    data={"status": task.status, "error_code": error_code},
                )
            )
            db.commit()

    def cancel(self, task_id, user_id):
        with self.session() as db:
            task = db.scalar(
                select(Task).where(Task.id == task_id, Task.user_id == user_id).with_for_update()
            )
            if not task:
                raise NotFound()
            if task.status in TERMINAL:
                return task_dict(task)
            task.status = "cancelling" if task.status in {"running", "cancelling"} else "cancelled"
            if task.parameters.get("profile") == "commerce_diagnosis":
                task.state_version += 1
            task.updated_at = utcnow()
            db.add(Event(task_id=task.id, kind=task.status, message="取消请求已登记"))
            db.commit()
            return task_dict(task)

    def review(self, task_id, user_id, approved):
        with self.session() as db:
            task = db.scalar(
                select(Task).where(Task.id == task_id, Task.user_id == user_id).with_for_update()
            )
            if not task:
                raise NotFound()
            if task.status != "waiting_review":
                raise Conflict("当前任务未等待复核")
            task.review_decision, task.status = approved, "queued"
            task.updated_at = utcnow()
            db.add(
                Event(
                    task_id=task.id,
                    kind="review",
                    message="复核通过，等待继续" if approved else "草稿驳回，等待归档",
                )
            )
            db.commit()
            return task_dict(task)
