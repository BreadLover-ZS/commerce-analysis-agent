"""Explicit local fault protocol. Never used as the model-quality denominator.

These are in-process deterministic injections. Real SIGKILL/PostgreSQL recovery
evidence remains in the separately identified integration suites.
"""

import copy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from app.diagnosis.schemas import ClarificationAnswer
from app.diagnosis.store import DiagnosisStore
from app.diagnosis.tools import DiagnosisToolGateway
from app.storage.models import CommerceDataset, Task, utcnow
from app.storage.repository import Conflict, StaleLease
from evaluations.diagnosis_runtime import collect, execute

MODES = {
    "none",
    "clarify",
    "foreign_bundle",
    "overlap",
    "unequal",
    "cache_loop",
    "cancel",
    "duplicate_answer",
    "forged_fact",
    "illegal_tool",
    "revoked",
    "stale_approval",
    "stale_worker",
    "takeover",
    "unknown_usage",
}


class ProtocolModel:
    diagnosis_protocol_stub = True
    callbacks = None
    max_retries = 0

    def __init__(self, *responses, effect=None):
        self.responses, self.effect, self.requests = list(responses), effect, 0

    def with_structured_output(self, schema, **kwargs):
        return self

    async def ainvoke(self, messages, **kwargs):
        self.requests += 1
        if self.effect:
            self.effect()
        response = (
            self.responses.pop(0)
            if self.responses
            else {"action": "stop", "question": "终止核对", "stop_reason": "exploration_complete"}
        )
        if isinstance(response, BaseException):
            raise response
        return {
            "parsed": response,
            "parsing_error": None,
            "raw": SimpleNamespace(
                content=str(response),
                tool_calls=[],
                id="local-protocol",
                usage_metadata={"input_tokens": 10, "output_tokens": 4},
                response_metadata={},
            ),
        }


def proposal(tool):
    return {
        "action": "tool",
        "question": "核对受限资料",
        "hypothesis_code": "data_coverage",
        "tool_name": tool,
        "arguments": {},
    }


def expire(repo, task_id):
    with repo.session() as db:
        db.get(Task, task_id).lease_until = utcnow() - timedelta(seconds=1)
        db.commit()


async def answer_clarification(
    repo, settings, user, task_id, choice, *, model=None, duplicate=False
):
    store = DiagnosisStore(repo)
    pending = store.projection(task_id, user)["pending_input"]
    if not pending or pending["type"] != "clarification":
        return {"clarification_requested": False}
    body = ClarificationAnswer(
        interrupt_id=pending["interrupt_id"],
        expected_version=pending["expected_version"],
        choice=choice,
    )
    first = store.answer(task_id, user, body)
    checks = {"clarification_requested": True}
    if duplicate:
        again = store.answer(task_id, user, body)
        checks["duplicate_is_idempotent"] = (
            first["state_version"] == again["state_version"] and first["status"] == again["status"]
        )
        checks["different_answer_rejected"] = False
        try:
            other = next(v for v in pending["payload"]["options"] if v != choice)
            store.answer(task_id, user, body.model_copy(update={"choice": other}))
        except Conflict:
            checks["different_answer_rejected"] = True
    await execute(repo, settings, model=model)
    checks["final_scope_matches_answer"] = (repo.get_task(task_id).get("report") or {}).get(
        "scope", {}
    ).get("skus") == [choice]
    return checks


async def run_harness(repo, settings, user, task, case):
    mode, task_id = case.harness.mode, task["id"]
    if mode not in MODES or case.execution != "fault":
        raise ValueError("unknown_or_nonfault_harness")
    checks = {}
    if mode in {"illegal_tool", "cache_loop", "unknown_usage", "cancel"}:
        if mode == "illegal_tool":
            model = ProtocolModel(proposal("execute_sql"), proposal("execute_sql"))
        elif mode == "cache_loop":
            model = ProtocolModel(*([proposal("compare_periods")] * 3))
        elif mode == "unknown_usage":
            model = ProtocolModel(TimeoutError("local injected unknown usage"))
        else:
            model = ProtocolModel(effect=lambda: repo.cancel(task_id, user.id))
        await execute(repo, settings, model=model)
        result = collect(repo, task_id, "gate")
        done = result["task"]
        if mode == "illegal_tool":
            checks = {
                "illegal_tool_not_executed": all(
                    q["tool_name"] != "execute_sql" for q in result["queries"]
                ),
                "invalid_decisions_bounded": done["report"]["stop_reason"] == "invalid_decision"
                and model.requests <= 2,
            }
        elif mode == "cache_loop":
            checks = {
                "cache_reuse_does_not_spend_tools": done["metrics"]["tool_calls"] == 2,
                "loop_stops": done["report"]["stop_reason"] == "no_information",
                "reused_steps": any(s["status"] == "REUSED" for s in result["steps"]),
            }
        elif mode == "unknown_usage":
            checks = {
                "unknown_retained": not done["metrics"]["usage_complete"],
                "no_next_request": model.requests == done["metrics"]["model_calls"] == 1,
                "stopped_for_unknown": done["report"]["stop_reason"] == "usage_unknown",
            }
        else:
            checks = {
                "cancelled": done["status"] == "cancelled",
                "no_next_request": model.requests == 1,
                "no_published_artifacts": not repo.artifacts(task_id),
            }
    elif mode == "revoked":
        sales_id = case_bundle_sales(repo, task)
        with repo.session() as db:
            db.get(CommerceDataset, sales_id).committed = False
            db.commit()
        await execute(repo, settings)
        done = repo.get_task(task_id)
        checks = {
            "failed_without_spending": done["status"] == "failed"
            and done["metrics"]["model_calls"] == done["metrics"]["tool_calls"] == 0,
            "no_report": done["report"] is None,
        }
    elif mode in {"takeover", "stale_worker"}:
        old = repo.claim()
        DiagnosisToolGateway(repo, old).invoke("inspect_dataset", {}, step_id="recovery-probe")
        original = collect(repo, task_id, "gate")
        expire(repo, task_id)
        new = repo.claim()
        checks["new_lease_token"] = new.lease_token != old.lease_token
        try:
            repo.emit(old, "late_write", "不得写入")
            checks["old_worker_write_rejected"] = False
        except StaleLease:
            checks["old_worker_write_rejected"] = True
        await execute(repo, settings, ctx=new)
        after = collect(repo, task_id, "gate")
        original_ids = {r["call_id"] for r in original["tool_invocations"]}
        checks["committed_tool_identity_reused"] = (
            original_ids <= {r["call_id"] for r in after["tool_invocations"]}
            and sum(q["tool_name"] == "inspect_dataset" for q in after["queries"]) == 1
        )
        checks["same_task_completed"] = after["task"]["status"] in {"succeeded", "degraded"}
    elif mode == "duplicate_answer":
        # The case measures CAS, not whether a model infers ambiguity from its
        # wording. The typed request belongs only to this local protocol group.
        choice = case.harness.clarification_choice
        model = ProtocolModel(
            {"action": "clarify", "question": "请选择授权对象", "clarification_scope": "sku"},
            {**proposal("rank_contributors"), "arguments": {"filters": {"skus": [choice]}}},
            {"action": "stop", "question": "核对完成", "stop_reason": "target_covered"},
        )
        await execute(repo, settings, model=model)
        checks = await answer_clarification(
            repo, settings, user, task_id, choice, model=model, duplicate=True
        )
    elif mode == "forged_fact":
        from app.diagnosis.graph import make_report

        def forged(*args, **kwargs):
            report = copy.deepcopy(make_report(*args, **kwargs))
            report["facts"][0]["value"] = "99999999.00"
            return report

        with patch("app.diagnosis.graph.make_report", forged):
            await execute(repo, settings)
        done = repo.get_task(task_id)
        checks = {
            "forged_report_rejected": done["status"] == "failed"
            and done["error_code"] == "report_validation_failed",
            "no_report_published": done["report"] is None and not repo.artifacts(task_id),
        }
    elif mode == "stale_approval":
        await execute(repo, settings)
        store = DiagnosisStore(repo)
        pending = store.projection(task_id, user)["pending_input"]
        body = SimpleNamespace(
            approved=True,
            interrupt_id=pending["interrupt_id"],
            expected_version=pending["expected_version"],
            report_hash="0" * 64,
        )
        try:
            store.review(task_id, user, body)
            checks["stale_review_rejected"] = False
        except Conflict:
            checks["stale_review_rejected"] = repo.get_task(task_id)["status"] == "waiting_review"
        body.report_hash = repo.get_task(task_id)["report_hash"]
        store.review(task_id, user, body)
        original = DiagnosisStore.publish_artifacts

        def late_batch(self, ctx, version, report_hash, files):
            return original(self, ctx, version, "0" * 64, files)

        with patch.object(DiagnosisStore, "publish_artifacts", late_batch):
            await execute(repo, settings)
        done = repo.get_task(task_id)
        checks["late_version_publication_failed"] = (
            done["status"] == "failed" and done["error_code"] == "Conflict"
        )
        checks["no_stale_artifacts_visible"] = not repo.artifacts(task_id)
    return {
        "mode": mode,
        "kind": "local_in_process_protocol_not_sigkill_or_model_quality",
        "checks": checks,
    }


def case_bundle_sales(repo, task):
    from app.storage.models import AnalysisInputBundle

    with repo.session() as db:
        return db.get(AnalysisInputBundle, task["bundle_id"]).sales_dataset_id
