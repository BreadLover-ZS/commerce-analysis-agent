"""Scripted protocol checks; these do not measure real model diagnosis quality."""

from types import SimpleNamespace

import pytest
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.diagnosis.model_calls import DiagnosisModelGateway
from app.research.meter import RuntimeModelMeter
from app.storage.models import ModelInvocation, Task
from app.storage.repository import BudgetExceeded, Conflict, StaleLease, TaskCancelled
from tests.conftest import expire_lease
from tests.test_diagnosis_tools import gateway


class Choice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(max_length=20)


MESSAGES = [("system", "输出一个受限决策"), ("human", "选择下一步")]


class ScriptedDecisionModel:
    diagnosis_protocol_stub = True
    callbacks = None
    max_retries = 0

    def __init__(self, *responses, effect=None):
        self.responses = list(responses)
        self.requests = []
        self.effect = effect

    def with_structured_output(self, schema, **kwargs):
        assert kwargs["include_raw"] is True
        self.schema = schema
        return self

    async def ainvoke(self, messages, **kwargs):
        self.requests.append(messages)
        if self.effect:
            self.effect()
        response = self.responses.pop(0) if self.responses else {"action": "stop"}
        if isinstance(response, BaseException):
            raise response
        raw = SimpleNamespace(
            content=str(response),
            tool_calls=[],
            id="scripted-request",
            usage_metadata={"input_tokens": 10, "output_tokens": 4},
            response_metadata={},
        )
        return {"parsed": response, "raw": raw, "parsing_error": None}


def model_gateway(setup, *responses, **kwargs):
    tools, task, _ = gateway(setup)
    model = ScriptedDecisionModel(*responses, **kwargs)
    return DiagnosisModelGateway(tools.repo, tools.ctx, model, "script-v1"), tools, task, model


async def test_gv23_output_and_usage_reused_after_checkpoint_gap(setup):
    calls, tools, task, model = model_gateway(setup, {"action": "rank"})
    before = await calls.invoke(Choice, MESSAGES, step_id="step-1")
    assert before["status"] == "SUCCEEDED" and before["usage_complete"]
    expire_lease(tools.repo, task["id"])
    ctx = tools.repo.claim()
    tools.repo.account_unreported_usage(ctx)
    recovered = DiagnosisModelGateway(tools.repo, ctx, model, "script-v1")
    after = await recovered.invoke(Choice, MESSAGES, step_id="step-1")
    assert after["call_id"] == before["call_id"] and after["reused"]
    assert after["output"] == {"action": "rank"}
    assert len(model.requests) == 1
    metrics = tools.repo.get_task(task["id"])["metrics"]
    assert metrics["runtime_ms"] >= 0  # Active execution is now charged at takeover.
    assert {k: v for k, v in metrics.items() if k != "runtime_ms"} == {
        "tool_calls": 0,
        "model_calls": 1,
        "input_tokens": 10,
        "output_tokens": 4,
        "usage_complete": True,
    }


@pytest.mark.parametrize("crash_window", ["before_send", "after_response"])
async def test_gv24_started_without_committed_response_never_retries(
    setup, monkeypatch, crash_window
):
    calls, tools, task, model = model_gateway(setup)

    def die(*args, **kwargs):
        raise SystemExit("simulated hard kill")

    if crash_window == "before_send":
        monkeypatch.setattr(model, "with_structured_output", die)
    else:
        monkeypatch.setattr(calls, "_complete", die)
    with pytest.raises(SystemExit):
        await calls.invoke(Choice, MESSAGES, step_id="step-1")
    expire_lease(tools.repo, task["id"])
    ctx = tools.repo.claim()
    tools.repo.account_unreported_usage(ctx)
    recovered = DiagnosisModelGateway(tools.repo, ctx, model, "script-v1")
    saved = await recovered.invoke(Choice, MESSAGES, step_id="step-1")
    assert saved["status"] == "UNKNOWN" and saved["reused"]
    with pytest.raises(BudgetExceeded):
        await recovered.invoke(Choice, MESSAGES, step_id="step-2")
    metrics = tools.repo.get_task(task["id"])["metrics"]
    assert metrics["model_calls"] == 1 and not metrics["usage_complete"]
    assert metrics["input_tokens"] == metrics["output_tokens"] == 0
    assert len(model.requests) == (0 if crash_window == "before_send" else 1)


async def test_gv10_timeout_unknown_is_retained_and_forbids_next_request(setup):
    calls, tools, task, model = model_gateway(setup, TimeoutError("private provider body"))
    result = await calls.invoke(Choice, MESSAGES, step_id="step-1")
    assert result["status"] == "UNKNOWN" and "private" not in str(result)
    with pytest.raises(BudgetExceeded):
        await calls.invoke(Choice, MESSAGES, step_id="step-2")
    assert len(model.requests) == 1
    with tools.repo.session() as db:
        call = db.scalar(select(ModelInvocation))
        assert call.status == "UNKNOWN" and call.validated_output is None
        assert call.usage is None and not call.usage_complete
        assert db.get(Task, task["id"]).usage_reports == 1


async def test_invalid_schema_response_keeps_known_usage_and_separate_correction(setup):
    calls, tools, task, model = model_gateway(
        setup, {"action": "bad", "extra": True}, {"action": "stop"}
    )
    first = await calls.invoke(Choice, MESSAGES, step_id="step-1")
    assert first["status"] == "FAILED" and first["error_code"] == "validation_error"
    assert first["usage_complete"]
    correction = await calls.invoke(Choice, MESSAGES, step_id="step-1", attempt=2)
    assert correction["status"] == "SUCCEEDED" and correction["call_id"] != first["call_id"]
    assert len(model.requests) == 2
    assert tools.repo.get_task(task["id"])["metrics"]["model_calls"] == 2
    with tools.repo.session() as db:
        rows = list(db.scalars(select(ModelInvocation).order_by(ModelInvocation.attempt)))
        assert [r.status for r in rows] == ["FAILED", "SUCCEEDED"]
        assert rows[0].response_digest and rows[0].usage["input_tokens"] == 10
        assert rows[0].validated_output is None


@pytest.mark.parametrize(
    "invalid_usage",
    [None, {"input_tokens": -1, "output_tokens": 2}, {"input_tokens": True, "output_tokens": 2}],
)
async def test_missing_or_invalid_usage_never_credits_or_allows_next_request(
    setup, monkeypatch, invalid_usage
):
    calls, tools, task, model = model_gateway(setup)
    original = model.ainvoke

    async def invoke(*args, **kwargs):
        result = await original(*args, **kwargs)
        result["raw"].usage_metadata = invalid_usage
        return result

    monkeypatch.setattr(model, "ainvoke", invoke)
    result = await calls.invoke(Choice, MESSAGES, step_id="step-1")
    assert result["status"] == "SUCCEEDED" and not result["usage_complete"]
    with pytest.raises(BudgetExceeded):
        await calls.invoke(Choice, MESSAGES, step_id="step-2")
    metrics = tools.repo.get_task(task["id"])["metrics"]
    assert metrics["input_tokens"] == metrics["output_tokens"] == 0
    assert len(model.requests) == 1


async def test_gv12_old_worker_cannot_commit_output_or_usage(setup):
    calls, tools, task, model = model_gateway(setup)

    def takeover():
        expire_lease(tools.repo, task["id"])
        assert tools.repo.claim() is not None

    model.effect = takeover
    with pytest.raises(StaleLease):
        await calls.invoke(Choice, MESSAGES, step_id="step-1")
    assert tools.repo.get_task(task["id"])["metrics"]["input_tokens"] == 0
    with tools.repo.session() as db:
        assert db.scalar(select(ModelInvocation.status)) == "STARTED"


async def test_gv14_cancellation_keeps_inflight_usage_but_stops_new_calls(setup):
    calls, tools, task, model = model_gateway(setup)
    model.effect = lambda: tools.repo.cancel(task["id"], tools.ctx.user_id)
    result = await calls.invoke(Choice, MESSAGES, step_id="step-1")
    assert result["status"] == "SUCCEEDED"
    assert tools.repo.get_task(task["id"])["metrics"]["input_tokens"] == 10
    assert tools.repo.get_task(task["id"])["report"] is None
    with pytest.raises(TaskCancelled):
        await calls.invoke(Choice, MESSAGES, step_id="step-2")
    assert len(model.requests) == 1


async def test_identity_reuse_with_changed_prompt_is_a_conflict(setup):
    calls, _, _, model = model_gateway(setup)
    await calls.invoke(Choice, MESSAGES, step_id="step-1")
    with pytest.raises(Conflict):
        await calls.invoke(Choice, [("human", "changed")], step_id="step-1")
    assert len(model.requests) == 1


async def test_gv27_preflight_hard_budget_prevents_network_send(setup):
    calls, tools, task, model = model_gateway(setup)
    with tools.repo.session() as db:
        row = db.get(Task, task["id"])
        row.model_calls = row.usage_reports = 12
        db.commit()
    with pytest.raises(BudgetExceeded):
        await calls.invoke(Choice, MESSAGES, step_id="step-1")
    assert model.requests == []
    with tools.repo.session() as db:
        assert list(db.scalars(select(ModelInvocation))) == []


def test_legacy_callback_is_cloned_out_to_prevent_double_reservation(setup):
    tools, _, _ = gateway(setup)
    old_meter = RuntimeModelMeter(tools.repo, tools.ctx)
    model = ChatOpenAI(model="offline", api_key="offline", max_retries=0, callbacks=[old_meter])
    calls = DiagnosisModelGateway(tools.repo, tools.ctx, model, "offline")
    assert calls.model.callbacks == []
    assert model.callbacks == [old_meter]
    model.max_retries = 1
    with pytest.raises(ValueError, match="关闭SDK重试"):
        DiagnosisModelGateway(tools.repo, tools.ctx, model, "offline")
