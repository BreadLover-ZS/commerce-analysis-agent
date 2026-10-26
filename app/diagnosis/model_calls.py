"""Durable single-request model boundary, sharing the legacy meter's budget primitives.

No automatic retry is allowed. A persisted STARTED without a committed response
is UNKNOWN on takeover, including the window before the network send.
"""

import asyncio
import json
import time
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from app.core.model_boundary import send_attempt, trusted_denial, verify_proof
from app.diagnosis.inputs import digest
from app.diagnosis.tools import DiagnosisToolGateway
from app.research.meter import RuntimeModelMeter
from app.storage.models import Event, ModelInvocation, utcnow
from app.storage.repository import BudgetExceeded, Conflict


def outcome(call, reused=False):
    return {
        "call_id": call.call_id,
        "status": call.status,
        "output": call.validated_output,
        "usage_complete": call.usage_complete,
        "error_code": call.error_code,
        "reused": reused,
    }


class DiagnosisModelGateway:
    def __init__(self, repo, ctx, model, model_version):
        self.repo, self.ctx = repo, ctx
        # The durable reservation/usage transaction replaces the legacy callback for
        # this single SDK request. Cloning preserves the caller's model configuration.
        callbacks = getattr(model, "callbacks", None)
        if callbacks and not isinstance(callbacks, list):
            raise ValueError("诊断模型需要列表形式callbacks")
        filtered = [c for c in (callbacks or []) if not isinstance(c, RuntimeModelMeter)]
        self.model = (
            model.model_copy(update={"callbacks": filtered})
            if isinstance(model, BaseModel)
            else model
        )
        if not isinstance(model, BaseModel) and callbacks != filtered and callbacks:
            raise ValueError("无法隔离旧计量回调")
        retries = getattr(model, "max_retries", 0)
        if retries != 0:
            raise ValueError("诊断模型必须关闭SDK重试")
        if not model_version or len(model_version) > 120:
            raise ValueError("需要有效模型版本")
        self.model_version = model_version
        saved_task = repo.get_task(ctx.task_id)
        parameters = saved_task["parameters"]
        if (
            saved_task["graph_version"]
            in {"diagnosis-v16", "commerce-review-bundle-v5", "commerce-review-bundle-v6"}
            and parameters.get("accounting_protocol") != 2
        ):
            raise Conflict("新版本任务缺少计量发送协议v2")
        self.protocol = parameters.get("accounting_protocol", 1)
        self.fixed = parameters.get("profile") == "commerce_review" and self.protocol == 2

    def _authorize(self, db):
        if not self.fixed:
            return DiagnosisToolGateway(self.repo, self.ctx)._authorize(db)
        from app.commerce.datasets import owned_dataset
        from app.commerce.review_inputs import authorized_review_bundle

        task = self.repo._fenced(db, self.ctx)
        if task.user_id != self.ctx.user_id or task.tenant_id != self.ctx.tenant_id:
            raise Conflict("调用身份与任务所有者不一致")
        if (
            task.parameters.get("review_answer_version") not in {5, 6}
            or task.parameters.get("accounting_protocol") != 2
        ):
            raise Conflict("固定流程计量契约不一致")
        if task.parameters.get("bundle_id"):
            authorized_review_bundle(db, task)
        else:
            owned_dataset(db, task.parameters["dataset_id"], task.user_id, task.tenant_id)
        return task, None, None

    def _call_id(self, step_id, phase, attempt):
        namespace = "fixed" if self.fixed else "diagnosis"
        return str(
            uuid5(NAMESPACE_URL, f"{namespace}:{self.ctx.task_id}:{step_id}:{phase}:{attempt}")
        )

    @staticmethod
    def _identity(call):
        return {
            name: getattr(call, name)
            for name in (
                "call_id",
                "task_id",
                "step_id",
                "phase",
                "attempt",
                "lease_token",
                "prompt_hash",
                "schema_hash",
                "model_version",
            )
        }

    def _saved_denial(self, db, task, call):
        if self.protocol != 2:
            return False
        events = list(
            db.scalars(
                select(Event).where(
                    Event.task_id == task.id,
                    Event.kind == "model_send_denied",
                )
            )
        )
        proofs = [e.data["proof"] for e in events if e.data.get("call_id") == call.call_id]
        if len(proofs) != 1 or not verify_proof(proofs[0], self._identity(call)):
            return False
        if call.status == "STARTED":
            valid = self.repo._record_usage_locked(task, {"input_tokens": 0, "output_tokens": 0})
            if not valid:
                return False
            call.status, call.usage_complete = "NOT_SENT", True
            call.usage = {"input_tokens": 0, "output_tokens": 0}
            call.error_code, call.completed_at = "provider_budget_not_sent", utcnow()
            call.response_digest = proofs[0]["event_sha256"]
            db.flush()
            calls = list(
                db.scalars(select(ModelInvocation).where(ModelInvocation.task_id == task.id))
            )
            # Worker takeover conservatively marks outstanding reservations unknown.
            # Clear that marker only when this ledger accounts for EVERY invocation.
            if (
                len(calls) == task.model_calls == task.usage_reports
                and all(c.usage_complete and c.usage is not None for c in calls)
                and all(c.status in {"SUCCEEDED", "FAILED", "NOT_SENT"} for c in calls)
                and all(
                    sum(c.usage[k] for c in calls) == getattr(task, k)
                    for k in ("input_tokens", "output_tokens")
                )
            ):
                task.usage_complete = True
        return call.status == "NOT_SENT"

    def _persist_denial(self, call_id, proof):
        with self.repo.session() as db:
            task = self.repo._fenced(db, self.ctx, allow_cancel=True)
            call = db.get(ModelInvocation, call_id)
            if (
                call.status != "STARTED"
                or call.lease_token != self.ctx.lease_token
                or not verify_proof(proof, self._identity(call))
            ):
                raise Conflict("发送前拒绝证明与当前调用不一致")
            existing = list(
                db.scalars(
                    select(Event).where(Event.task_id == task.id, Event.kind == "model_send_denied")
                )
            )
            bound = [e for e in existing if e.data.get("call_id") == call_id]
            if bound:
                if len(bound) != 1 or bound[0].data.get("proof") != proof:
                    raise Conflict("发送前拒绝证明冲突")
                return
            db.add(
                Event(
                    task_id=task.id,
                    kind="model_send_denied",
                    message="预算守卫在发送前拒绝请求",
                    data={"call_id": call_id, "proof": proof},
                )
            )
            db.commit()

    def _not_sent(self, call_id, started):
        with self.repo.session() as db:
            task = self.repo._fenced(db, self.ctx, allow_cancel=True)
            call = db.get(ModelInvocation, call_id)
            if not self._saved_denial(db, task, call):
                raise Conflict("发送前拒绝证明不可验证")
            call.elapsed_ms = int((time.monotonic() - started) * 1000)
            db.commit()
            return outcome(call)

    def _recover(self, schema, step_id, phase, attempt):
        """Recover a graph step before rebuilding its mutable budget context.

        This is deliberately separate from invoke(): an explicit new invocation
        with a different prompt must still conflict. Only the graph's saved
        Task/step identity may retrieve a durable response without a new send.
        """
        if (
            not 1 <= len(step_id) <= 80
            or not 1 <= len(phase) <= 40
            or type(attempt) is not int
            or attempt not in (1, 2)
        ):
            raise ValueError("无效模型调用身份")
        call_id = self._call_id(step_id, phase, attempt)
        with self.repo.session() as db:
            task, _, _ = self._authorize(db)
            call = db.get(ModelInvocation, call_id)
            if call is None:
                return None
            if (
                call.schema_hash != digest(schema.model_json_schema())
                or call.model_version != self.model_version
            ):
                raise Conflict("保存的模型调用schema或模型版本不一致")
            if call.status == "STARTED":
                if call.lease_token == self.ctx.lease_token:
                    raise Conflict("模型调用仍在进行")
                if not self._saved_denial(db, task, call):
                    self._unknown_locked(task, call)
                db.commit()
            elif call.status == "NOT_SENT" and not self._saved_denial(db, task, call):
                raise Conflict("保存的发送前拒绝证明已缺失或改变")
            return outcome(call, reused=True)

    async def recover(self, schema, *, step_id, phase="propose", attempt=1):
        return await asyncio.to_thread(self._recover, schema, step_id, phase, attempt)

    def _prepare(self, schema, messages, step_id, phase, attempt):
        if (
            not 1 <= len(step_id) <= 80
            or not 1 <= len(phase) <= 40
            or type(attempt) is not int
            or attempt not in (1, 2)
        ):
            raise ValueError("无效模型调用身份")
        prompt_hash = digest(messages)
        schema_hash = digest(schema.model_json_schema())
        call_id = self._call_id(step_id, phase, attempt)
        with self.repo.session() as db:
            # All calls, catalog reads and cached outputs recheck input permissions.
            task, _, _ = self._authorize(db)
            call = db.get(ModelInvocation, call_id)
            if call:
                if (call.prompt_hash, call.schema_hash, call.model_version) != (
                    prompt_hash,
                    schema_hash,
                    self.model_version,
                ):
                    raise Conflict("模型调用身份已绑定不同输入或版本")
                if call.status == "STARTED":
                    if call.lease_token == self.ctx.lease_token:
                        raise Conflict("模型调用仍在进行")
                    if not self._saved_denial(db, task, call):
                        self._unknown_locked(task, call)
                    db.commit()
                elif call.status == "NOT_SENT" and not self._saved_denial(db, task, call):
                    raise Conflict("保存的发送前拒绝证明已缺失或改变")
                return outcome(call, reused=True), False
            # Another step's unknown attempt forbids spending again, even with a new ID.
            interrupted = list(
                db.scalars(
                    select(ModelInvocation).where(
                        ModelInvocation.task_id == task.id,
                        ModelInvocation.status.in_(
                            ["STARTED", "UNKNOWN", *(["NOT_SENT"] if self.protocol == 2 else [])]
                        ),
                    )
                )
            )
            for previous in interrupted:
                if previous.status == "STARTED" and previous.lease_token != self.ctx.lease_token:
                    if not self._saved_denial(db, task, previous):
                        self._unknown_locked(task, previous)
            if interrupted:
                db.commit()
                raise BudgetExceeded("unreconciled_model_call")
            self.repo._consume_locked(db, task, "model", 12)
            call = ModelInvocation(
                call_id=call_id,
                task_id=task.id,
                step_id=step_id,
                phase=phase,
                attempt=attempt,
                prompt_hash=prompt_hash,
                schema_hash=schema_hash,
                model_version=self.model_version,
                lease_token=self.ctx.lease_token,
                status="STARTED",
                usage_complete=False,
            )
            db.add(call)
            db.add(
                Event(
                    task_id=task.id,
                    kind="model_start",
                    message="诊断模型调用已预占额度",
                    data={"call_id": call_id, "phase": phase},
                )
            )
            db.commit()
            return outcome(call), True

    @staticmethod
    def _unknown_locked(task, call):
        if call.status == "STARTED":
            task.usage_reports += 1
            call.status, call.error_code, call.completed_at = "UNKNOWN", "usage_unknown", utcnow()
        task.usage_complete = False

    def _unknown(self, call_id, started):
        with self.repo.session() as db:
            task = self.repo._fenced(db, self.ctx, allow_cancel=True)
            call = db.get(ModelInvocation, call_id)
            if call.status == "STARTED":
                self._unknown_locked(task, call)
                call.elapsed_ms = int((time.monotonic() - started) * 1000)
                db.add(
                    Event(
                        task_id=task.id,
                        kind="model_usage_incomplete",
                        message="调用结果未知，停止新模型请求",
                        data={"call_id": call_id, "reason": "missing_response"},
                    )
                )
            db.commit()
            return outcome(call)

    def _complete(
        self, call_id, raw, parsed, started, validation_error_code=None, provider_usage_valid=True
    ):
        usage = getattr(raw, "usage_metadata", None)
        metadata = getattr(raw, "response_metadata", {}) or {}
        if not usage:
            tokens = metadata.get("token_usage")
            if isinstance(tokens, dict):
                usage = {
                    "input_tokens": tokens.get("prompt_tokens"),
                    "output_tokens": tokens.get("completion_tokens"),
                }
        if self.protocol == 2 and isinstance(usage, dict) and "total_tokens" in usage:
            if (
                not all(
                    type(usage.get(k)) is int
                    for k in ("input_tokens", "output_tokens", "total_tokens")
                )
                or usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]
            ):
                usage = None
        with self.repo.session() as db:
            task = self.repo._fenced(db, self.ctx, allow_cancel=True)
            call = db.get(ModelInvocation, call_id)
            if call.status != "STARTED":
                return outcome(call, reused=True)
            valid_usage = self.repo._record_usage_locked(
                task, usage if provider_usage_valid else None
            )
            call.usage_complete = valid_usage
            call.usage = (
                {k: usage[k] for k in ("input_tokens", "output_tokens")} if valid_usage else None
            )
            call.status = (
                "UNKNOWN"
                if self.protocol == 2 and not valid_usage
                else "SUCCEEDED"
                if parsed is not None
                else "FAILED"
            )
            call.error_code = (
                None if parsed is not None else validation_error_code or "validation_error"
            )
            if self.protocol == 2 and not valid_usage:
                call.error_code = "usage_unknown"
                parsed = None
            call.validated_output = parsed.model_dump(mode="json") if parsed is not None else None
            # Hash only observable content; do not retain provider reasoning or secrets.
            call.response_digest = digest(
                {
                    "content": getattr(raw, "content", ""),
                    "tool_calls": getattr(raw, "tool_calls", []),
                }
            )
            call.request_id = (
                str(metadata.get("request_id") or getattr(raw, "id", "") or "")[:128] or None
            )
            call.elapsed_ms, call.completed_at = int((time.monotonic() - started) * 1000), utcnow()
            db.add(
                Event(
                    task_id=task.id,
                    kind="model_end",
                    message="诊断模型响应及用量已登记",
                    data={
                        "call_id": call_id,
                        "phase": call.phase,
                        "status": call.status,
                        "usage_complete": valid_usage,
                        "elapsed_ms": call.elapsed_ms,
                    },
                )
            )
            db.commit()
            return outcome(call)

    async def invoke(self, schema, messages, *, step_id, phase="propose", attempt=1):
        reserved, execute = await asyncio.to_thread(
            self._prepare, schema, messages, step_id, phase, attempt
        )
        if not execute:
            return reserved
        started = time.monotonic()
        with self.repo.session() as db:
            call = db.get(ModelInvocation, reserved["call_id"])
            identity = self._identity(call)
        attempt_state = None
        try:
            method = "function_calling"
            if (
                urlsplit(str(getattr(self.model, "openai_api_base", "") or "")).hostname
                == "api.deepseek.com"
            ):
                method = "json_mode"
                messages = [
                    (
                        "system",
                        "仅输出符合此Schema的JSON，不输出解释："
                        + json.dumps(schema.model_json_schema(), ensure_ascii=False),
                    ),
                    *messages,
                ]
            runnable = self.model.with_structured_output(schema, method=method, include_raw=True)
            with send_attempt(identity) as attempt_state:
                result = await runnable.ainvoke(
                    messages, config={"metadata": {"phase": phase, "call_id": reserved["call_id"]}}
                )
        except BaseException as exc:
            # SystemExit simulates a hard process death in tests; its durable STARTED
            # is reconciled by takeover. Cancellation still records unknown usage.
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            proof = trusted_denial(exc, attempt_state) if self.protocol == 2 else None
            if proof is not None and not isinstance(exc, asyncio.CancelledError):
                # Persist proof before finalizing known zero. A crash between these
                # transactions can recover the same proof, never issue a new call.
                await asyncio.to_thread(self._persist_denial, reserved["call_id"], proof)
                return await asyncio.to_thread(self._not_sent, reserved["call_id"], started)
            await asyncio.to_thread(self._unknown, reserved["call_id"], started)
            if isinstance(exc, asyncio.CancelledError) or asyncio.current_task().cancelling():
                raise asyncio.CancelledError() from exc
            return {
                **reserved,
                "status": "UNKNOWN",
                "usage_complete": False,
                "error_code": "provider_error",
            }
        parsed = (
            result.get("parsed")
            if isinstance(result, dict) and not result.get("parsing_error")
            else None
        )
        from app.diagnosis.proposals_v3 import is_v3_schema, validation_code

        validation_error_code = None
        if parsed is not None:
            try:
                parsed = schema.model_validate(
                    parsed.model_dump(mode="json")
                    if self.protocol == 2 and isinstance(parsed, BaseModel)
                    else parsed
                )
            except (ValidationError, TypeError) as error:
                parsed = None
                if is_v3_schema(schema):
                    validation_error_code = validation_code(error)
        elif is_v3_schema(schema):
            # Parse locally only to classify a failed structured response. This
            # neither retries the SDK nor permits a partial/cleaned proposal.
            raw = result.get("raw") if isinstance(result, dict) else None
            try:
                content = getattr(raw, "content", None)
                if isinstance(content, str) and content:
                    parsed = schema.model_validate_json(content)
                elif len(getattr(raw, "tool_calls", [])) == 1:
                    parsed = schema.model_validate(raw.tool_calls[0]["args"])
            except (ValidationError, TypeError, ValueError, KeyError) as error:
                validation_error_code = validation_code(error)
            if parsed is None and validation_error_code is None:
                validation_error_code = "proposal_schema_invalid"
        return await asyncio.to_thread(
            self._complete,
            reserved["call_id"],
            result.get("raw") if isinstance(result, dict) else None,
            parsed,
            started,
            **({"validation_error_code": validation_error_code} if is_v3_schema(schema) else {}),
            **(
                {"provider_usage_valid": attempt_state.provider_usage_valid is not False}
                if self.protocol == 2
                else {}
            ),
        )
