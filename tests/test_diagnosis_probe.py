"""Probe admission and first-failure regression tests; all SDK I/O is mocked."""

import copy
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.diagnosis.inputs import create_bundle
from app.diagnosis.model_calls import DiagnosisModelGateway
from app.research.schemas import TaskRequest
from app.storage.repository import BudgetExceeded
from evaluations.diagnosis import RequestBudget
from evaluations.diagnosis_probe import failure_reason, require_prior_review, validate_selection
from evaluations.diagnosis_runtime import collect, execute
from tests.test_diagnosis_tools import bundle_request, committed
from tests.test_diagnosis_v16 import MESSAGES, Choice, prepared_gateway, sdk


def new_probe_task(
    setup, group="fixed", query="分别列出两期支付金额、退款金额和净销售额，不要解释原因。"
):
    settings, repo, user, _ = setup
    settings.stop_after_model_failure = True
    settings.diagnosis_runtime = "v16"
    settings.commerce_review_answer_version = 6
    settings.agent_mode, settings.openai_api_key = "live", SecretStr("local-offline")
    dataset = committed(repo, user)
    bundle = create_bundle(repo, user, bundle_request(dataset["id"]))
    return repo.create_task(
        user,
        TaskRequest(
            conversation_id=repo.create_conversation(user.id),
            query=query,
            bundle_id=bundle["bundle_id"],
            profile="commerce_review" if group == "fixed" else "commerce_diagnosis",
            strategy="workflow" if group == "fixed" else "diagnostic",
            sources=["sales"],
        ),
        str(uuid4()),
    )


def completion(body, name=None):
    message = {"role": "assistant", "content": json.dumps(body)}
    if name:
        message.update(
            content=None,
            tool_calls=[
                {
                    "id": "local-call",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(body)},
                }
            ],
        )
    return httpx.Response(
        200,
        json={
            "id": "offline",
            "model": "local-mock",
            "object": "chat.completion",
            "choices": [
                {"index": 0, "finish_reason": "tool_calls" if name else "stop", "message": message}
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        },
    )


async def test_historical_hold_counts_towards_cap_without_becoming_known_usage(tmp_path):
    budget = RequestBudget(
        10,
        max_total_tokens=40,
        context_ceiling=10,
        initial_spent_tokens=4,
        frozen_unknown_tokens=20,
        journal=tmp_path / "journal",
    )
    sent = []

    async def transport(request):
        sent.append(request)
        return completion({})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport),
        event_hooks={"request": [budget.before_send], "response": [budget.after_response]},
    ) as client:
        await client.post("https://provider.invalid/chat/completions", json={"max_tokens": 3})
        with pytest.raises(RuntimeError, match="token_budget_capacity"):
            await client.post("https://provider.invalid/chat/completions", json={"max_tokens": 3})
    assert len(sent) == 1 and budget.spent_tokens == 8 and budget.reserved_tokens == 0
    assert budget.state()["frozen_unknown_tokens"] == 20
    assert budget.state()["upper_bound_charged_tokens"] == 28


@pytest.mark.parametrize("bad", [-1, True, 1.5, None, 97])
def test_invalid_or_unfunded_hold_is_rejected(bad):
    with pytest.raises(ValueError):
        RequestBudget(
            1,
            max_total_tokens=100,
            context_ceiling=10,
            initial_spent_tokens=4,
            frozen_unknown_tokens=bad,
        )


@pytest.mark.parametrize("fixed", [False, True])
async def test_frozen_old_unknown_does_not_break_new_proof_of_no_send(setup, tmp_path, fixed):
    repo, ctx, task = prepared_gateway(setup, fixed)
    budget = RequestBudget(
        9,
        max_total_tokens=36,
        context_ceiling=10,
        initial_spent_tokens=4,
        frozen_unknown_tokens=20,
        journal=tmp_path / "journal",
        protocol=2,
    )

    async def forbidden(request):
        pytest.fail("hold must stop before transport")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(forbidden), event_hooks={"request": [budget.before_send]}
    ) as client:
        outcome = await DiagnosisModelGateway(repo, ctx, sdk(client), "local-mock").invoke(
            Choice, MESSAGES, step_id="step-1"
        )
    assert outcome["status"] == "NOT_SENT" and outcome["usage_complete"]
    assert repo.get_task(task["id"])["metrics"]["usage_complete"]
    assert budget.sent == 0 and budget.spent_tokens == 4 and budget.frozen_unknown_tokens == 20


@pytest.mark.parametrize("kind", ["schema", "scope"])
async def test_first_fixed_failure_never_spends_on_repair_or_semantic_review(setup, tmp_path, kind):
    task = new_probe_task(setup)
    settings, repo, _, _ = setup
    assert task["parameters"]["budget"]["stop_after_model_failure"]
    sent = []

    async def transport(request):
        sent.append(request)
        assert len(sent) == 1, "failure must not trigger another model call"
        name = json.loads(request.content)["tools"][0]["function"]["name"]
        return completion(
            {
                "analysis": ["bad_code" if kind == "schema" else "sales_drivers"],
                "recommendations": [],
            },
            name,
        )

    budget = RequestBudget(
        12, max_total_tokens=1000, context_ceiling=10, journal=tmp_path / "journal", protocol=2
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport),
        event_hooks={"request": [budget.before_send], "response": [budget.after_response]},
    ) as client:
        await execute(repo, settings, model=sdk(client))
    record = collect(repo, task["id"], "fixed")
    assert len(sent) == 1 and record["task"]["metrics"]["usage_complete"]
    assert record["task"]["metrics"]["model_calls"] == 1
    assert failure_reason(record, {"automatic_passed": True}) is not None


async def test_failed_durable_invocation_blocks_another_identity_without_spending(setup):
    setup[0].stop_after_model_failure = True
    repo, ctx, _ = prepared_gateway(setup)
    sent = []

    async def transport(request):
        sent.append(request)
        name = json.loads(request.content)["tools"][0]["function"]["name"]
        return completion({"extra": "invalid"}, name)

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        calls = DiagnosisModelGateway(repo, ctx, sdk(client), "local-mock")
        first = await calls.invoke(Choice, MESSAGES, step_id="step-1")
        assert first["status"] == "FAILED"
        with pytest.raises(BudgetExceeded, match="model_failure_stop"):
            await calls.invoke(Choice, MESSAGES, step_id="step-2")
    assert len(sent) == 1


@pytest.mark.parametrize(
    "reason", ["unknown", "rejected", "call_failed", "grade_failed", "fallback"]
)
def test_all_failure_types_stop_even_if_a_fallback_report_looks_valid(reason):
    r = {
        "task": {"metrics": {"usage_complete": True}, "validation": {}, "status": "degraded"},
        "model_invocations": [],
        "steps": [],
    }
    grading = {"automatic_passed": True}
    if reason == "unknown":
        r["task"]["metrics"]["usage_complete"] = False
    elif reason == "rejected":
        r["steps"] = [{"status": "REJECTED"}]
    elif reason == "call_failed":
        r["model_invocations"] = [{"status": "FAILED"}]
    elif reason == "grade_failed":
        grading["automatic_passed"] = False
    else:
        r["task"]["validation"]["draft_fallback_used"] = True
    assert failure_reason(r, grading)


def test_review_and_repair_are_bound_before_starting_next_batch(tmp_path):
    parent = {
        "directory": str(tmp_path),
        "parent_receipt_sha256": "r",
        "parent_failed": False,
        "parent_implementation_sha256": "old",
    }
    review = tmp_path / "review.json"
    review.write_text(json.dumps({"receipt_sha256": "r", "verdict": "PENDING"}))
    with pytest.raises(ValueError, match="pending"):
        require_prior_review(parent, "old")
    review.write_text(json.dumps({"receipt_sha256": "r", "verdict": "PASS"}))
    require_prior_review(parent, "old")
    parent["parent_failed"] = True
    with pytest.raises(ValueError, match="root_cause"):
        require_prior_review(parent, "old")
    repair = tmp_path / "repair.json"
    repair.write_text(
        json.dumps(
            {
                "failed_receipt_sha256": "r",
                "implementation_sha256": "new",
                "local_verification_passed": True,
            }
        )
    )
    require_prior_review(parent, "new", repair)
    bad = copy.deepcopy(parent)
    bad["parent_receipt_sha256"] = "changed"
    with pytest.raises(ValueError, match="review_not_bound"):
        require_prior_review(bad, "new", repair)


def test_unexecuted_holdout_is_not_a_development_probe(tmp_path):
    source = Path("docs/acceptance/diagnosis-v15-h01-h20/cases.json")
    root = tmp_path / "legacy"
    root.mkdir()
    from evaluations.diagnosis_probe_accounting import file_hash

    (root / "run.json").write_text(json.dumps({"cases_file_sha256": file_hash(source)}))
    (root / "progress.jsonl").write_text(json.dumps({"case_id": "H03", "group": "fixed"}) + "\n")
    validate_selection(source, "H03", "fixed", root)
    with pytest.raises(ValueError, match="unexecuted_holdout"):
        validate_selection(source, "H14", "additional", root)


@pytest.mark.parametrize(
    "mode,expected_http", [("valid", 2), ("schema", 1), ("action", 1), ("zero", 0)]
)
async def test_complete_one_task_probe_has_exact_spend_and_stops_on_first_error(
    tmp_path, monkeypatch, mode, expected_http
):
    from evaluations import diagnosis_probe as module
    from evaluations.diagnosis_probe_accounting import HOLD, LIMIT, file_hash

    source = Path("docs/acceptance/diagnosis-v15-h01-h20/cases.json").resolve()
    case_id = "H03"
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    group = "fixed"
    (legacy / "run.json").write_text(json.dumps({"cases_file_sha256": file_hash(source)}))
    (legacy / "progress.jsonl").write_text(json.dumps({"case_id": case_id, "group": group}) + "\n")
    parent = {
        "known_tokens": 4,
        "frozen_unknown_tokens": HOLD,
        "confirmed_http_requests": 1,
        "directory": str(legacy),
        "legacy_root": str(legacy),
        "run_id": "local-base",
        "parent_failed": False,
    }
    monkeypatch.setattr(module, "load_parent", lambda *a, **kw: copy.deepcopy(parent))
    monkeypatch.setenv("PROBE_TEST_KEY", "offline-key")
    if mode == "zero":
        # Fault injection: an operator cancels before the first model call.
        # Accounting remains valid at zero while quality must remain FAIL.
        created = {}
        original_new_task = module.new_task

        def new_task(repo, user, *a, **kw):
            task = original_new_task(repo, user, *a, **kw)
            created.update(task_id=task["id"], user_id=user.id)
            return task

        async def cancelled(repo, settings, **kwargs):
            repo.cancel(created["task_id"], created["user_id"])

        monkeypatch.setattr(module, "new_task", new_task)
        monkeypatch.setattr(module, "execute", cancelled)
    requests = []

    async def transport(request):
        requests.append(request)
        if len(requests) == 1:
            value = {"analysis": [], "recommendations": []}
            if mode == "schema":
                value["analysis"] = ["not_registered"]
            elif mode == "action":
                value["recommendations"] = [
                    {"action_id": "forged", "precondition_id": "forged", "scope_skus": []}
                ]
        else:
            assert mode == "valid", "failed first response must not spend again"
            value = {
                "unsupported_claim_ids": [],
                "unsupported_limitation_indices": [],
                "explanation": "local mock",
            }
        return completion(value)

    original = httpx.AsyncClient

    class OfflineClient(original):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, transport=httpx.MockTransport(transport))

    monkeypatch.setattr(module.httpx, "AsyncClient", OfflineClient)
    out = tmp_path / "probe"
    args = module.parser().parse_args(
        [
            "--parent",
            str(tmp_path / "hold.json"),
            "--legacy-root",
            str(legacy),
            "--cases",
            str(source),
            "--case-id",
            case_id,
            "--group",
            group,
            "--output",
            str(out),
            "--max-http",
            "2",
            "--max-total-tokens",
            str(LIMIT),
            "--allow-paid",
            "--api-key-env",
            "PROBE_TEST_KEY",
        ]
    )
    failure = await module.run(args)
    assert len(requests) == expected_http
    assert bool(failure) is (mode != "valid")
    summary = json.loads((out / "summary.json").read_bytes())
    assert summary["budget"]["known_tokens"] == 4 + 4 * expected_http
    assert summary["budget"]["frozen_unknown_tokens"] == HOLD
    assert summary["new_http_requests"] == expected_http and not summary["release_eligible"]
    audited = module.audit_probe(out)
    assert audited["confirmed_http_requests"] == 1 + expected_http
    raw = (out / "summary.json").read_bytes()
    broken = json.loads(raw)
    broken["budget"]["frozen_unknown_tokens"] = 0
    (out / "summary.json").write_text(json.dumps(broken))
    with pytest.raises(ValueError, match="summary_inconsistent"):
        module.audit_probe(out)
    (out / "summary.json").write_bytes(raw)
    if mode == "zero":
        assert (out / "budget-journal.jsonl").read_bytes() == b""
        return
    record_path = out / "receipt.json"
    record = json.loads(record_path.read_bytes())
    record["model_invocations"][0].update(status="UNKNOWN", usage_complete=False, usage=None)
    record["task"]["metrics"]["usage_complete"] = False
    record_path.write_text(json.dumps(record))
    summary["receipt_sha256"] = file_hash(record_path)
    (out / "summary.json").write_text(json.dumps(summary))
    with pytest.raises(ValueError, match="unknown_invocation"):
        module.audit_probe(out)


async def test_business_rejected_diagnosis_step_stops_before_a_corrective_proposal(setup, tmp_path):
    task = new_probe_task(setup, group="agent", query="请逐商品核对两期每日净销售额序列及日覆盖。")
    settings, repo, _, _ = setup
    settings.stop_after_model_failure = False  # Frozen Task policy still applies.
    sent = []

    async def transport(request):
        sent.append(request)
        assert len(sent) == 1
        name = json.loads(request.content)["tools"][0]["function"]["name"]
        return completion(
            {
                "action": "tool",
                "question": "核对现有事实",
                "tool_name": "rank_contributors",
                "arguments": {"filters": {"skus": ["FOREIGN"]}},
            },
            name,
        )

    budget = RequestBudget(
        12, max_total_tokens=1000, context_ceiling=10, journal=tmp_path / "journal", protocol=2
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport),
        event_hooks={"request": [budget.before_send], "response": [budget.after_response]},
    ) as client:
        await execute(repo, settings, model=sdk(client))
    record = collect(repo, task["id"], "agent")
    assert len(sent) == 1 and record["model_invocations"][0]["status"] == "SUCCEEDED"
    assert any(s["status"] == "REJECTED" for s in record["steps"])
    assert failure_reason(record, {"automatic_passed": True}) == "proposal_or_tool_rejected"


def test_unavailable_semantic_review_is_a_probe_failure():
    record = {
        "task": {
            "metrics": {"usage_complete": True},
            "validation": {"semantic_status": "unavailable"},
            "status": "degraded",
        }
    }
    assert failure_reason(record, {"automatic_passed": True}) == "semantic_review_unavailable"


async def test_cached_nonprogress_does_not_pay_for_a_second_proposal(setup, tmp_path):
    task = new_probe_task(setup, group="agent", query="请逐商品核对两期每日净销售额序列及日覆盖。")
    settings, repo, _, _ = setup
    sent = []

    async def transport(request):
        sent.append(request)
        assert len(sent) == 1, "no progress must stop after the first repeated tool"
        name = json.loads(request.content)["tools"][0]["function"]["name"]
        return completion(
            {
                "action": "tool",
                "question": "重复已有检查",
                "tool_name": "inspect_dataset",
                "arguments": {},
            },
            name,
        )

    budget = RequestBudget(
        12, max_total_tokens=1000, context_ceiling=10, journal=tmp_path / "journal", protocol=2
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport),
        event_hooks={"request": [budget.before_send], "response": [budget.after_response]},
    ) as client:
        await execute(repo, settings, model=sdk(client))
    record = collect(repo, task["id"], "agent")
    assert len(sent) == 1 and record["task"]["report"]["stop_reason"] == "no_information"
    assert failure_reason(record, {"automatic_passed": True}) == "no_information_stop"
