"""Diagnostic projections on the existing Repository and its lease/Task row lock."""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select

from app.core.files import safe_path
from app.diagnosis.inputs import bundle_info, digest, owned_bundle
from app.diagnosis.tools import DiagnosisToolGateway, bound_extensions
from app.storage.models import (
    Artifact,
    DiagnosisStep,
    DiagnosticFinding,
    Event,
    Evidence,
    InputRequest,
    Task,
    TaskFeedback,
    ToolQueryResult,
    utcnow,
)
from app.storage.repository import Conflict, NotFound, evidence_dict, task_dict

STEP_FINAL = {"SUCCEEDED", "FAILED", "REUSED", "REJECTED"}


def step_dict(row):
    return {
        k: getattr(row, k)
        for k in (
            "step_id",
            "sequence",
            "plan_version",
            "proposal",
            "validated_args",
            "status",
            "model_call_id",
            "call_id",
            "evidence_ids",
            "assessment",
        )
    }


def input_dict(row):
    return {
        "interrupt_id": row.id,
        "type": row.interrupt_type,
        "expected_version": row.expected_version,
        "payload": row.payload,
        "decision": row.decision,
        "waiting_until": row.waiting_until.isoformat() + "Z",
    }


class DiagnosisStore:
    def __init__(self, repo):
        self.repo = repo

    def _authorized_task(self, db, task_id, user, *, locked=True):
        statement = select(Task).where(Task.id == task_id, Task.user_id == user.id)
        task = db.scalar(statement.with_for_update() if locked else statement)
        if (
            not task
            or task.tenant_id != user.tenant_id
            or task.parameters.get("profile") != "commerce_diagnosis"
        ):
            raise NotFound()
        owned_bundle(db, task.parameters["bundle_id"], user.id, user.tenant_id)
        return task

    def snapshot(self, ctx):
        with self.repo.session() as db:
            task, bundle, sales = DiagnosisToolGateway(self.repo, ctx)._authorize(db)
            sources = [
                evidence_dict(e)
                for e in db.scalars(
                    select(Evidence)
                    .where(Evidence.task_id == ctx.task_id)
                    .order_by(Evidence.created_at, Evidence.id)
                )
            ]
            queries = {
                q.spec_hash: {
                    "query_spec": q.query_spec,
                    "result": q.result,
                    "result_digest": q.result_digest,
                }
                for q in db.scalars(
                    select(ToolQueryResult).where(ToolQueryResult.task_id == ctx.task_id)
                )
            }
            steps = [
                step_dict(s)
                for s in db.scalars(
                    select(DiagnosisStep)
                    .where(DiagnosisStep.task_id == ctx.task_id)
                    .order_by(DiagnosisStep.sequence)
                )
            ]
            return {
                "task": task_dict(task),
                "bundle": bundle_info(bundle),
                "sales_payload": sales.payload,
                "evidence": sources,
                "queries": queries,
                "steps": steps,
                **bound_extensions(db, task, bundle),
            }

    def save_report(self, ctx, report):
        from app.diagnosis.reporting import validate_report
        from app.diagnosis.validation import verified_evidence

        snapshot = self.snapshot(ctx)
        task, bundle, sources = snapshot["task"], snapshot["bundle"], snapshot["evidence"]
        for source in sources:
            query = snapshot["queries"].get(source["payload"].get("query_spec_hash"))
            if not query or not verified_evidence(
                source,
                query,
                bundle,
                snapshot["sales_payload"],
                ctx.task_id,
                task=task,
                extras=snapshot,
            ):
                raise Conflict("诊断事实未通过固定源重算校验")
        validation = validate_report(report, task, bundle, sources, snapshot["steps"])
        if not validation["passed"]:
            from app.core.errors import ReportValidationFailed

            raise ReportValidationFailed()
        with self.repo.session() as db:
            row, _, _ = DiagnosisToolGateway(self.repo, ctx)._authorize(db)
            if row.report == report and row.report_hash == digest(report):
                return {
                    "report": row.report,
                    "validation": row.validation,
                    "report_hash": row.report_hash,
                }
            report = {**report, "report_version": row.report_version + 1}
            row.report_version += 1
            row.report, row.report_hash = report, digest(report)
            row.validation = {**validation, "report_hash": row.report_hash}
            row.review_decision = None
            row.state_version += 1
            if row.pending_interrupt_id:
                old = db.get(InputRequest, row.pending_interrupt_id)
                if old and old.interrupt_type == "report_review":
                    row.pending_interrupt_id, row.waiting_until = None, None
            for finding in db.scalars(
                select(DiagnosticFinding).where(DiagnosticFinding.task_id == ctx.task_id)
            ):
                finding.report_version = row.report_version
            db.commit()
            return {
                "report": row.report,
                "validation": row.validation,
                "report_hash": row.report_hash,
            }

    def publish_artifacts(self, ctx, report_version, report_hash, files):
        import hashlib

        with self.repo.session() as db:
            task, _, _ = DiagnosisToolGateway(self.repo, ctx)._authorize(db)
            expected = {f"report-v{report_version}.{ext}" for ext in ("md", "json", "pdf")} | {
                f"evidence-v{report_version}.json",
                f"diagnosis-trace-v{report_version}.json",
            }
            if set(files) != expected:
                raise Conflict("诊断产物清单不完整或包含未授权文件")
            if (task.report_version, task.report_hash) != (report_version, report_hash) or not (
                task.validation or {}
            ).get("passed"):
                raise Conflict("产物不属于当前有效报告版本")
            if task.parameters["require_review"]:
                approved = db.scalar(
                    select(InputRequest).where(
                        InputRequest.task_id == task.id,
                        InputRequest.interrupt_key == "report:" + report_hash,
                    )
                )
                if (
                    not approved
                    or approved.payload.get("report_version") != report_version
                    or approved.decision != {"approved": True, "report_hash": report_hash}
                ):
                    raise Conflict("该报告版本未获批准")
            for name, item in files.items():
                path = safe_path(self.repo.settings.data_dir / "artifacts", item["storage_key"])
                sha = hashlib.sha256(item["content"]).hexdigest()
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                    raise Conflict("临时产物内容校验失败")
                old = db.scalar(
                    select(Artifact).where(Artifact.task_id == task.id, Artifact.name == name)
                )
                if old:
                    if (old.sha256, old.report_version, old.report_hash) != (
                        sha,
                        report_version,
                        report_hash,
                    ):
                        raise Conflict("禁止覆盖已登记的版本产物")
                    continue
                db.add(
                    Artifact(
                        id=str(uuid4()),
                        task_id=task.id,
                        name=name,
                        storage_key=item["storage_key"],
                        size=len(item["content"]),
                        sha256=sha,
                        report_version=report_version,
                        report_hash=report_hash,
                    )
                )
            db.add(
                Event(
                    task_id=task.id,
                    kind="diagnosis_published",
                    message="版本化诊断产物已发布",
                    data={"report_version": report_version, "report_hash": report_hash},
                )
            )
            db.commit()

    def published(self, ctx, version, report_hash):
        import hashlib

        with self.repo.session() as db:
            task, _, _ = DiagnosisToolGateway(self.repo, ctx)._authorize(db)
            if (task.report_version, task.report_hash) != (version, report_hash):
                raise Conflict("产物报告版本已变化")
            rows = list(
                db.scalars(
                    select(Artifact).where(
                        Artifact.task_id == task.id, Artifact.report_version == version
                    )
                )
            )
            if not rows:
                return False
            expected = {f"report-v{version}.{ext}" for ext in ("md", "json", "pdf")} | {
                f"evidence-v{version}.json",
                f"diagnosis-trace-v{version}.json",
            }
            if {r.name for r in rows} != expected:
                raise Conflict("已发布产物清单不完整")
            for row in rows:
                path = safe_path(self.repo.settings.data_dir / "artifacts", row.storage_key)
                if (
                    row.report_hash != report_hash
                    or not path.is_file()
                    or hashlib.sha256(path.read_bytes()).hexdigest() != row.sha256
                ):
                    raise Conflict("已发布产物校验失败")
            return True

    def steps(self, ctx):
        with self.repo.session() as db:
            DiagnosisToolGateway(self.repo, ctx)._authorize(db)
            return [
                step_dict(s)
                for s in db.scalars(
                    select(DiagnosisStep)
                    .where(DiagnosisStep.task_id == ctx.task_id)
                    .order_by(DiagnosisStep.sequence)
                )
            ]

    def put_step(self, ctx, sequence, proposal, model_call_id=None):
        if type(sequence) is not int or not 1 <= sequence <= 8:
            raise Conflict("诊断步骤达到上限")
        with self.repo.session() as db:
            task, _, _ = DiagnosisToolGateway(self.repo, ctx)._authorize(db)
            row = db.scalar(
                select(DiagnosisStep).where(
                    DiagnosisStep.task_id == ctx.task_id, DiagnosisStep.sequence == sequence
                )
            )
            if row:
                if row.proposal != proposal or row.model_call_id != model_call_id:
                    raise Conflict("步骤身份已绑定不同提议")
                return step_dict(row)
            count = db.scalar(
                select(func.count())
                .select_from(DiagnosisStep)
                .where(DiagnosisStep.task_id == ctx.task_id)
            )
            if sequence != count + 1:
                raise Conflict("步骤序号不连续")
            row = DiagnosisStep(
                id=str(uuid4()),
                task_id=ctx.task_id,
                step_id=f"step-{sequence}",
                sequence=sequence,
                plan_version=1,
                proposal=proposal,
                model_call_id=model_call_id,
                status="PROPOSED",
                evidence_ids=[],
            )
            db.add(row)
            task.state_version += 1
            db.add(
                Event(
                    task_id=ctx.task_id,
                    kind="diagnosis_step",
                    message="已登记诊断提议",
                    data={"step_id": row.step_id, "sequence": sequence, "status": "PROPOSED"},
                )
            )
            db.commit()
            return step_dict(row)

    def update_step(
        self,
        ctx,
        sequence,
        *,
        status=None,
        validated_args=None,
        call_id=None,
        evidence_ids=None,
        assessment=None,
    ):
        with self.repo.session() as db:
            task, bundle, _ = DiagnosisToolGateway(self.repo, ctx)._authorize(db)
            row = db.scalar(
                select(DiagnosisStep).where(
                    DiagnosisStep.task_id == ctx.task_id, DiagnosisStep.sequence == sequence
                )
            )
            if not row:
                raise Conflict("步骤不存在")
            changes = {
                "status": status,
                "validated_args": validated_args,
                "call_id": call_id,
                "evidence_ids": evidence_ids,
                "assessment": assessment,
            }
            if all(v is None or getattr(row, k) == v for k, v in changes.items()):
                return step_dict(row)
            transitions = {
                "PROPOSED": {"AUTHORIZED", "REJECTED", "SUCCEEDED"},
                "AUTHORIZED": {"RUNNING", "SUCCEEDED", "REJECTED"},
                "RUNNING": {"SUCCEEDED", "FAILED", "REUSED", "REJECTED"},
            }
            if status and status != row.status and status not in transitions.get(row.status, set()):
                raise Conflict("非法步骤状态转换")
            if evidence_ids is not None:
                for eid in evidence_ids:
                    evidence = db.get(Evidence, eid)
                    if (
                        not evidence
                        or evidence.task_id != ctx.task_id
                        or evidence.payload.get("bundle_id") != bundle.id
                    ):
                        raise Conflict("步骤证据与任务输入不一致")
                row.evidence_ids = evidence_ids
            if status:
                row.status = status
            if validated_args is not None:
                row.validated_args = validated_args
            if call_id is not None:
                row.call_id = call_id
            if assessment is not None:
                row.assessment = assessment
            task.state_version += 1
            db.add(
                Event(
                    task_id=ctx.task_id,
                    kind="diagnosis_step",
                    message="诊断步骤状态已更新",
                    data={
                        "step_id": row.step_id,
                        "status": row.status,
                        "assessment": row.assessment or {},
                    },
                )
            )
            db.commit()
            return step_dict(row)

    def save_findings(self, ctx, findings):
        with self.repo.session() as db:
            DiagnosisToolGateway(self.repo, ctx)._authorize(db)
            for value in findings:
                row = db.get(DiagnosticFinding, value["finding_id"])
                if row:
                    if row.task_id != ctx.task_id or row.payload != value:
                        raise Conflict("候选判断身份已绑定不同证据")
                    continue
                for eid in value["supporting_evidence"] + value["contradicting_evidence"]:
                    source = db.get(Evidence, eid)
                    if not source or source.task_id != ctx.task_id:
                        raise Conflict("候选判断引用越界")
                db.add(
                    DiagnosticFinding(
                        id=value["finding_id"],
                        task_id=ctx.task_id,
                        hypothesis_code=value["hypothesis_code"],
                        status=value["status"],
                        payload=value,
                        report_version=0,
                    )
                )
            db.commit()

    def ensure_input(self, ctx, key, kind, payload):
        if kind not in {"clarification", "report_review"} or not key or len(key) > 80:
            raise ValueError("非法中断类型或身份")
        with self.repo.session() as db:
            task, _, _ = DiagnosisToolGateway(self.repo, ctx)._authorize(db)
            existing = db.scalar(
                select(InputRequest).where(
                    InputRequest.task_id == ctx.task_id, InputRequest.interrupt_key == key
                )
            )
            if existing:
                if existing.payload != payload or existing.interrupt_type != kind:
                    raise Conflict("中断身份已绑定不同输入")
                return input_dict(existing)
            if task.pending_interrupt_id:
                pending = db.get(InputRequest, task.pending_interrupt_id)
                if pending and pending.decision is None:
                    raise Conflict("已有未答复中断")
            task.state_version += 1
            until = utcnow() + timedelta(seconds=self.repo.settings.diagnosis_wait_ttl_seconds)
            row = InputRequest(
                id=str(uuid4()),
                task_id=task.id,
                interrupt_key=key,
                interrupt_type=kind,
                expected_version=task.state_version,
                payload=payload,
                waiting_until=until,
            )
            db.add(row)
            task.pending_interrupt_id, task.waiting_until = row.id, until
            db.add(
                Event(
                    task_id=task.id,
                    kind="input_requested",
                    message="已登记人工输入请求",
                    data={
                        "interrupt_id": row.id,
                        "type": kind,
                        "expected_version": row.expected_version,
                    },
                )
            )
            db.commit()
            return input_dict(row)

    def resume_decision(self, ctx, interrupt_id):
        with self.repo.session() as db:
            DiagnosisToolGateway(self.repo, ctx)._authorize(db)
            row = db.get(InputRequest, interrupt_id)
            if not row or row.task_id != ctx.task_id:
                raise Conflict("检查点中断身份不一致")
            return row.decision

    def close_input(self, ctx, interrupt_id, decision):
        with self.repo.session() as db:
            task, _, _ = DiagnosisToolGateway(self.repo, ctx)._authorize(db)
            row = db.get(InputRequest, interrupt_id)
            if (
                not row
                or row.task_id != ctx.task_id
                or row.decision is None
                or row.decision != decision
            ):
                raise Conflict("恢复决定未登记或不一致")
            if task.pending_interrupt_id == interrupt_id:
                task.pending_interrupt_id, task.waiting_until = None, None
                task.state_version += 1
            db.commit()

    def _answer(self, task_id, user, interrupt_id, expected_version, decision, kind):
        with self.repo.session() as db:
            task = self._authorized_task(db, task_id, user)
            row = db.get(InputRequest, interrupt_id)
            if not row or row.task_id != task.id or row.interrupt_type != kind:
                raise NotFound()
            if row.expected_version != expected_version:
                raise Conflict("输入版本不一致")
            if kind == "clarification" and decision.get("choice") not in row.payload["options"]:
                raise Conflict("请选择原授权范围内的选项")
            if kind == "report_review" and (
                row.payload["report_version"] != task.report_version
                or row.payload["report_hash"] != task.report_hash
                or decision.get("report_hash") != task.report_hash
            ):
                raise Conflict("复核对象已过期")
            if row.decision is not None:
                if row.decision != decision:
                    raise Conflict("该输入已有不同答复")
                return task_dict(task)
            expected_status = "waiting_input" if kind == "clarification" else "waiting_review"
            if (
                task.status != expected_status
                or task.pending_interrupt_id != row.id
                or task.state_version != expected_version
            ):
                raise Conflict("任务当前未等待该输入")
            if row.waiting_until <= utcnow():
                task.status, task.error_code = "expired", "waiting_expired"
                task.lease_token, task.lease_until = None, None
                task.state_version += 1
                db.add(Event(task_id=task.id, kind="expired", message="人工输入等待已过期"))
                db.commit()
                raise Conflict("输入请求已过期")
            row.decision, row.resolved_at = decision, utcnow()
            task.status, task.waiting_until = "queued", None
            task.state_version += 1
            task.updated_at = utcnow()
            db.add(
                Event(
                    task_id=task.id,
                    kind="input_resolved",
                    message="人工输入已登记，等待继续执行",
                    data={
                        "interrupt_id": row.id,
                        "type": kind,
                        "state_version": task.state_version,
                    },
                )
            )
            db.commit()
            return task_dict(task)

    def answer(self, task_id, user, body):
        return self._answer(
            task_id,
            user,
            str(body.interrupt_id),
            body.expected_version,
            {"choice": body.choice},
            "clarification",
        )

    def review(self, task_id, user, body):
        if not body.interrupt_id or body.expected_version is None or not body.report_hash:
            raise Conflict("诊断复核须绑定中断、版本与报告哈希")
        return self._answer(
            task_id,
            user,
            str(body.interrupt_id),
            body.expected_version,
            {"approved": body.approved, "report_hash": body.report_hash},
            "report_review",
        )

    def projection(self, task_id, user, after=0, after_version=None):
        with self.repo.session() as db:
            task = self._authorized_task(db, task_id, user, locked=False)
            steps = [
                step_dict(r)
                for r in db.scalars(
                    select(DiagnosisStep)
                    .where(DiagnosisStep.task_id == task_id, DiagnosisStep.sequence > after)
                    .order_by(DiagnosisStep.sequence)
                    .limit(8)
                )
            ]
            pending = (
                db.get(InputRequest, task.pending_interrupt_id)
                if task.pending_interrupt_id
                else None
            )
            if after_version is not None and task.state_version <= after_version:
                steps = []
            findings = [
                r.payload
                for r in db.scalars(
                    select(DiagnosticFinding).where(DiagnosticFinding.task_id == task_id)
                )
            ]
            return {
                "task_id": task_id,
                "state_version": task.state_version,
                "state_cursor": task.state_version,
                "bundle_id": task.bundle_id,
                "graph_version": task.graph_version,
                "steps": steps,
                "cursor": steps[-1]["sequence"] if steps else after,
                "findings": findings,
                "pending_input": input_dict(pending) if pending else None,
                "stop_reason": (task.report or {}).get("stop_reason"),
                "report_version": task.report_version,
                "report_hash": task.report_hash,
                "budgets": {
                    "tool_calls": task.tool_calls,
                    "model_calls": task.model_calls,
                    "max_steps": 8,
                    "max_tool_calls": min(10, self.repo.task_limit(task, "max_tool_calls")),
                    "max_model_calls": min(12, self.repo.task_limit(task, "max_model_calls")),
                    "max_tokens": self.repo.task_limit(task, "max_tokens"),
                    "max_runtime_seconds": self.repo.task_limit(task, "max_runtime_seconds"),
                },
            }

    def feedback(self, task_id, user, body):
        with self.repo.session() as db:
            task = self._authorized_task(db, task_id, user)
            if task.report is None or task.state_version != body.expected_version:
                raise Conflict("报告不存在或反馈版本已过期")
            if body.finding_id:
                finding = db.get(DiagnosticFinding, body.finding_id)
                if not finding or finding.task_id != task_id:
                    raise NotFound()
            if (
                db.scalar(
                    select(func.count())
                    .select_from(TaskFeedback)
                    .where(TaskFeedback.task_id == task_id)
                )
                >= 50
            ):
                raise Conflict("反馈达到任务上限")
            row = TaskFeedback(
                id=str(uuid4()),
                task_id=task_id,
                actor_id=user.id,
                finding_id=body.finding_id,
                category=body.category,
                comment=body.comment,
            )
            db.add(row)
            db.commit()
            return {"id": row.id, "task_id": task_id, "report_version": task.report_version}
