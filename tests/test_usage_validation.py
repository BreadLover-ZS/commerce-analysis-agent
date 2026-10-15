from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.research.meter import RuntimeModelMeter
from app.storage.models import Task
from app.storage.repository import MAX_STORED_TOKENS, BudgetExceeded
from app.tasks.worker import Worker, checkpoint_store
from tests.conftest import authenticate, new_task
from tests.fake_model import ScriptedCommerceModel


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        [],
        "private-provider-value",
        {"input_tokens": 10},
        {"input_tokens": -1, "output_tokens": 5},
        {"input_tokens": 10, "output_tokens": -1},
        {"input_tokens": True, "output_tokens": 5},
        {"input_tokens": 10, "output_tokens": False},
        {"input_tokens": 1.5, "output_tokens": 5},
        {"input_tokens": "10", "output_tokens": 5},
        {"input_tokens": float("nan"), "output_tokens": 5},
        {"input_tokens": 10, "output_tokens": float("inf")},
        {"input_tokens": MAX_STORED_TOKENS + 1, "output_tokens": 5},
        {"input_tokens": 10, "output_tokens": MAX_STORED_TOKENS + 1},
    ],
)
def test_bad_receipt_preserves_prior_counters_and_blocks_new_generation(setup, usage):
    _, repo, *_ = setup
    task = new_task(setup)
    ctx = repo.claim()
    repo.consume(ctx, "model")
    assert repo.record_usage(ctx, {"input_tokens": 10, "output_tokens": 5})
    repo.consume(ctx, "model")
    assert not repo.record_usage(ctx, usage)
    metrics = repo.get_task(task["id"])["metrics"]
    assert (metrics["input_tokens"], metrics["output_tokens"]) == (10, 5)
    assert not metrics["usage_complete"]
    with pytest.raises(BudgetExceeded, match="不完整"):
        repo.consume(ctx, "model")


@pytest.mark.parametrize("input_tokens,output_tokens", [(0, 0), (10, 5), (MAX_STORED_TOKENS, 0)])
def test_zero_and_representable_integer_receipts_remain_valid(setup, input_tokens, output_tokens):
    _, repo, *_ = setup
    task = new_task(setup)
    ctx = repo.claim()
    repo.consume(ctx, "model")
    assert repo.record_usage(ctx, {"input_tokens": input_tokens, "output_tokens": output_tokens})
    metrics = repo.get_task(task["id"])["metrics"]
    assert metrics["usage_complete"]
    assert (metrics["input_tokens"], metrics["output_tokens"]) == (input_tokens, output_tokens)


def test_cumulative_overflow_does_not_partially_credit_valid_field(setup):
    _, repo, *_ = setup
    task = new_task(setup)
    ctx = repo.claim()
    repo.consume(ctx, "model")
    with repo.session() as db:
        db.get(Task, task["id"]).input_tokens = MAX_STORED_TOKENS - 1
        db.commit()
    assert not repo.record_usage(ctx, {"input_tokens": 2, "output_tokens": 5})
    metrics = repo.get_task(task["id"])["metrics"]
    assert (metrics["input_tokens"], metrics["output_tokens"]) == (MAX_STORED_TOKENS - 1, 0)
    assert not metrics["usage_complete"]


def test_later_inflight_receipt_cannot_restore_unknown_usage(setup):
    _, repo, *_ = setup
    task = new_task(setup)
    ctx = repo.claim()
    repo.consume(ctx, "model")
    repo.consume(ctx, "model")
    assert not repo.record_usage(ctx, {"input_tokens": -1, "output_tokens": 0})
    assert repo.record_usage(ctx, {"input_tokens": 10, "output_tokens": 5})
    metrics = repo.get_task(task["id"])["metrics"]
    assert not metrics["usage_complete"]
    assert (metrics["input_tokens"], metrics["output_tokens"]) == (10, 5)
    with pytest.raises(BudgetExceeded):
        repo.consume(ctx, "model")


def test_legacy_negative_counters_never_produce_cost_estimate_or_admission(setup, client):
    settings, repo, *_ = setup
    settings.input_price_per_million = settings.output_price_per_million = 1
    authenticate(client)
    task = new_task(setup)
    ctx = repo.claim()
    with repo.session() as db:
        db.get(Task, task["id"]).input_tokens = -100
        db.commit()
    detail = client.get(f"/api/tasks/{task['id']}").json()
    assert not detail["metrics"]["usage_complete"]
    assert detail["metrics"]["estimated_cost"] is None
    with pytest.raises(BudgetExceeded):
        repo.consume(ctx, "model")
    with repo.session() as db:
        assert not db.get(Task, task["id"]).usage_complete


@pytest.mark.parametrize(
    "raw", [None, [], "private-provider-value", {"prompt_tokens": "10", "completion_tokens": 5}]
)
async def test_malformed_sdk_fallback_usage_records_safe_unknown_receipt(setup, raw):
    _, repo, *_ = setup
    task = new_task(setup)
    ctx = repo.claim()
    meter = RuntimeModelMeter(repo, ctx)
    run = uuid4()
    await meter.on_chat_model_start({}, [], run_id=run)
    await meter.on_llm_end(
        SimpleNamespace(generations=[], llm_output={"token_usage": raw}), run_id=run
    )
    metrics = repo.get_task(task["id"])["metrics"]
    assert metrics["model_calls"] == 1 and not metrics["usage_complete"]
    event = next(e for e in repo.events(task["id"]) if e["kind"] == "model_usage_incomplete")
    assert event["data"] == {"reason": "missing_or_invalid_token_counts"}
    assert "private-provider-value" not in str(event)


async def test_invalid_usage_from_sdk_boundary_stops_workflow_after_one_generation(setup):
    class InvalidUsageModel(ScriptedCommerceModel):
        def _generate(self, *args, **kwargs):
            result = super()._generate(*args, **kwargs)
            result.generations[0].message.usage_metadata = {
                "input_tokens": -100,
                "output_tokens": 5,
                "total_tokens": -95,
            }
            return result

    settings, repo, *_ = setup
    settings.agent_mode, settings.llm_model = "live", "fake-commerce"
    settings.openai_api_key = SecretStr("offline-invalid-usage")
    task = new_task(setup, sources=["sales"])
    model = InvalidUsageModel()
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver, model).run_once()
    done = repo.get_task(task["id"])
    assert done["status"] == "degraded"
    assert done["metrics"]["model_calls"] == len(model._tool_sets) == 1
    assert not done["metrics"]["usage_complete"]
    assert done["metrics"]["input_tokens"] == done["metrics"]["output_tokens"] == 0
