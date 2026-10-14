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
from app.storage.models import Artifact, Conversation, Event, Evidence, Task, User, utcnow

TERMINAL = {"succeeded", "degraded", "failed", "cancelled", "rejected"}
ACTIVE = {"queued", "running", "cancelling", "waiting_review"}


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
        "metrics": {
            "runtime_ms": item.runtime_ms,
            "tool_calls": item.tool_calls,
            "model_calls": item.model_calls,
            "input_tokens": item.input_tokens,
            "output_tokens": item.output_tokens,
            "usage_complete": item.usage_complete and item.usage_reports == item.model_calls,
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

    def create_task(self, user, request, idempotency_key):
        payload = request.model_dump(mode="json")
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with self.session() as db:
            # Serialize admission per user in PostgreSQL, including duplicates.
            db.scalar(select(User).where(User.id == user.id).with_for_update())
            prior = db.scalar(
                select(Task).where(Task.user_id == user.id, Task.idempotency_key == idempotency_key)
            )
            if prior:
                if prior.request_hash != fingerprint:
                    raise Conflict("幂等键已用于另一项请求")
                return task_dict(prior)
            conversation = db.get(Conversation, request.conversation_id)
            if not conversation or conversation.user_id != user.id:
                raise NotFound()
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
                return None
            expired_budget = (
                task.status == "running" and task.deadline_at and task.deadline_at <= now
            )
            if (
                task.status == "cancelling"
                or task.attempts >= self.settings.max_attempts
                or expired_budget
            ):
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
                task.deadline_at
                if task.status == "running" and task.deadline_at is not None
                else now
                + timedelta(
                    seconds=max(0, self.settings.max_runtime_seconds - task.runtime_ms / 1000)
                )
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
            if resource == "tool":
                if task.tool_calls >= self.settings.max_tool_calls:
                    raise BudgetExceeded("tool_budget")
                task.tool_calls += 1
            else:
                if not task.usage_complete:
                    raise BudgetExceeded("模型Token使用量不完整，停止后续调用")
                if (
                    task.model_calls >= self.settings.max_model_calls
                    or task.input_tokens + task.output_tokens >= self.settings.max_tokens
                ):
                    raise BudgetExceeded("model_budget")
                task.model_calls += 1
            db.commit()

    def record_usage(self, ctx, usage):
        with self.session() as db:
            task = self._fenced(db, ctx, allow_cancel=True)
            task.usage_reports += 1
            if not usage or "input_tokens" not in usage or "output_tokens" not in usage:
                task.usage_complete = False
            else:
                task.input_tokens += int(usage["input_tokens"])
                task.output_tokens += int(usage["output_tokens"])
            db.commit()

    def account_unreported_usage(self, ctx):
        with self.session() as db:
            task = self._fenced(db, ctx, allow_cancel=True)
            if task.usage_reports < task.model_calls:
                task.usage_complete = False
            db.commit()

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
            task.report, task.validation = report, validation
            db.commit()

    def add_artifact(self, ctx, name, storage_key, content):
        with self.session() as db:
            self._fenced(db, ctx)
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
                {"id": a.id, "name": a.name, "size": a.size, "sha256": a.sha256}
                for a in db.scalars(select(Artifact).where(Artifact.task_id == task_id))
            ]

    def finish(self, ctx, status, elapsed_ms, error_code=None):
        with self.session() as db:
            task = self._fenced(db, ctx, allow_cancel=True)
            task.status = "cancelled" if task.status == "cancelling" else status
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
