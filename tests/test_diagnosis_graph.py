"""Actual Worker/checkpoints/API diagnostics. Demo policy and scripted model evidence only."""

import copy
from datetime import timedelta
from uuid import uuid4

import pytest

from app.diagnosis.inputs import create_bundle
from app.diagnosis.reporting import validate_report
from app.diagnosis.schemas import ClarificationAnswer
from app.diagnosis.store import DiagnosisStore
from app.research.schemas import TaskRequest
from app.storage.models import CommerceDataset, InputRequest, Task, utcnow
from app.storage.repository import Conflict, NotFound, StaleLease
from app.tasks.worker import Worker, checkpoint_store
from tests.conftest import authenticate, expire_lease
from tests.test_diagnosis_model_calls import ScriptedDecisionModel
from tests.test_diagnosis_tools import bundle_request, committed, oracle_payload


def new_diagnosis(
    setup, query="净销售额下降涉及哪些商品，退款变化能解释多少", require_review=False, payload=None
):
    _, repo, user, _ = setup
    dataset = committed(repo, user, payload)
    bundle = create_bundle(repo, user, bundle_request(dataset["id"]))
    request = TaskRequest(
        conversation_id=repo.create_conversation(user.id),
        query=query,
        profile="commerce_diagnosis",
        bundle_id=bundle["bundle_id"],
        require_review=require_review,
    )
    return repo.create_task(user, request, str(uuid4())), bundle


async def test_diagnostic_worker_report_has_independently_expected_facts(setup):
    settings, repo, user, _ = setup
    task, bundle = new_diagnosis(setup)
    async with checkpoint_store(settings) as saver:
        assert await Worker(repo, settings, saver).run_once()
    done = repo.get_task(task["id"])
    assert done["status"] == "degraded", done
    assert done["validation"]["passed"]
    assert done["report"]["stop_reason"] == "target_covered"
    assert done["report"]["bundle_hash"] == bundle["bundle_hash"]
    assert any(
        f["metric_key"] == "net_revenue.delta" and f["value"] == "-200.00"
        for f in done["report"]["facts"]
    )
    assert done["metrics"]["model_calls"] == 0
    assert done["metrics"]["tool_calls"] == 4
    projection = DiagnosisStore(repo).projection(task["id"], user)
    assert [s["proposal"]["action"] for s in projection["steps"]] == ["tool", "tool", "stop"]
    assert len(repo.artifacts(task["id"])) == 5
    assert projection["steps"][0]["assessment"]["hypothesis_status"] == "supported_observation"
    assert any(
        f["hypothesis_code"] == "refund_amount_change" and f["status"] == "supported_observation"
        for f in projection["findings"]
    )


async def test_clarification_then_exact_report_review_and_download(client, setup):
    settings, repo, user, _ = setup
    task, _ = new_diagnosis(setup, "那个商品的净销售额变化需要核对", require_review=True)
    authenticate(client)
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    paused = client.get(f"/api/tasks/{task['id']}").json()
    assert paused["status"] == "waiting_input", paused
    projection = client.get(f"/api/tasks/{task['id']}/diagnosis").json()
    pending = projection["pending_input"]
    answer = {
        "interrupt_id": pending["interrupt_id"],
        "expected_version": pending["expected_version"],
        "choice": "A",
    }
    response = client.post(f"/api/tasks/{task['id']}/clarifications", json=answer)
    assert response.status_code == 200, response.text
    assert client.post(f"/api/tasks/{task['id']}/clarifications", json=answer).status_code == 200
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    waiting = client.get(f"/api/tasks/{task['id']}").json()
    assert waiting["status"] == "waiting_review", waiting
    assert waiting["report"]["scope"]["skus"] == ["A"]
    assert not repo.artifacts(task["id"])
    pending = client.get(f"/api/tasks/{task['id']}/diagnosis").json()["pending_input"]
    review = {
        "approved": True,
        "interrupt_id": pending["interrupt_id"],
        "expected_version": pending["expected_version"],
        "report_hash": waiting["report_hash"],
    }
    assert (
        client.post(f"/api/tasks/{task['id']}/review", json={"approved": True}).status_code == 409
    )
    response = client.post(f"/api/tasks/{task['id']}/review", json=review)
    assert response.status_code == 200, response.text
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    done = client.get(f"/api/tasks/{task['id']}").json()
    assert done["status"] == "degraded", done
    pdf = next(a for a in done["artifacts"] if a["name"] == "report-v1.pdf")
    assert client.get("/api/artifacts/" + pdf["id"]).content.startswith(b"%PDF")
    assert client.post(f"/api/tasks/{task['id']}/review", json=review).status_code == 200


async def test_same_question_branches_on_intermediate_contributions(setup):
    settings, repo, user, _ = setup
    question = "净销售额为什么下降"
    concentrated = oracle_payload()
    dispersed = oracle_payload()
    # Independent data edit: tie absolute SKU changes -> no concentrated lead.
    dispersed.daily_sales[-1].revenue_cents = 10000
    traces = []
    for payload in (concentrated, dispersed):
        task, _ = new_diagnosis(setup, question, payload=payload)
        async with checkpoint_store(settings) as saver:
            await Worker(repo, settings, saver).run_once()
        done = repo.get_task(task["id"])
        assert done["validation"]["passed"], done
        traces.append(
            [
                s["proposal"].get("tool_name")
                for s in DiagnosisStore(repo).projection(task["id"], user)["steps"]
            ]
        )
    assert traces[0][:2] == ["rank_contributors", "drilldown_series"]
    assert traces[1][:2] == ["rank_contributors", "analyze_refunds"]


def tool_proposal(name, arguments=None):
    return {
        "action": "tool",
        "question": "核对既有证据",
        "hypothesis_code": "data_coverage",
        "tool_name": name,
        "arguments": arguments or {},
    }


@pytest.mark.parametrize(
    "responses,reason",
    [
        ([tool_proposal("compare_periods")] * 3, "no_information"),
        ([tool_proposal("execute_sql")] * 2, "invalid_decision"),
        ([tool_proposal("compare_periods", {"user_id": "other"})] * 2, "invalid_decision"),
        ([{"action": "tool", "forged_fact": 99999}] * 2, "invalid_decision"),
        ([tool_proposal("rank_contributors", {"top_k": k}) for k in (1, 2, 3)], "no_information"),
    ],
)
async def test_invalid_and_cached_loops_are_bounded(setup, responses, reason):
    settings, repo, user, _ = setup
    task, _ = new_diagnosis(setup)
    model = ScriptedDecisionModel(*responses)
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver, model=model).run_once()
    done = repo.get_task(task["id"])
    assert done["status"] == "degraded", done
    assert done["report"]["stop_reason"] == reason
    assert done["metrics"]["model_calls"] <= 3
    assert done["metrics"]["tool_calls"] <= 5
    assert done["report"]["execution_mode"] == "scripted_protocol"
    if responses[0].get("tool_name") == "compare_periods" and not responses[0]["arguments"]:
        assert done["metrics"]["tool_calls"] == 2
        assert all(
            s["status"] == "REUSED"
            for s in DiagnosisStore(repo).projection(task["id"], user)["steps"]
        )


async def test_eight_steps_and_ten_tools_are_hard_global_bounds(setup):
    settings, repo, user, _ = setup
    task, _ = new_diagnosis(setup)
    proposals = [
        tool_proposal("compare_periods", {"metric": metric})
        for metric in ("gross_revenue", "refund_amount", "paid_units", "refunded_units")
    ]
    proposals += [
        tool_proposal("compare_periods", {"filters": {"skus": [sku]}}) for sku in ("A", "B")
    ]
    proposals += [
        tool_proposal("rank_contributors", {"filters": {"skus": [sku]}}) for sku in ("A", "B")
    ]
    model = ScriptedDecisionModel(*proposals, tool_proposal("analyze_refunds"))
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver, model=model).run_once()
    done = repo.get_task(task["id"])
    assert done["status"] == "degraded", done
    assert done["report"]["stop_reason"] == "step_budget"
    assert done["metrics"]["tool_calls"] == 10 and done["metrics"]["model_calls"] == 8
    assert len(DiagnosisStore(repo).projection(task["id"], user)["steps"]) == 8
    assert len(model.requests) == 8


@pytest.mark.parametrize("field", ["facts", "observations", "recommendations", "versions", "scope"])
async def test_program_gate_rejects_forged_report_content(setup, field):
    settings, repo, user, _ = setup
    task, bundle = new_diagnosis(setup)
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    task = repo.get_task(task["id"])
    projection = DiagnosisStore(repo).projection(task["id"], user)
    report = copy.deepcopy(task["report"])
    if field == "facts":
        report["facts"][0]["value"] = "999999.00"
    elif field == "observations":
        report["observations"][0]["text"] = "服务质量差已被证明"
    elif field == "recommendations":
        report["recommendations"] = ["保证利润增长并立即采购"]
    elif field == "versions":
        report["versions"] = {}
    else:
        report["scope"]["skus"] = ["A"]
    assert not validate_report(
        report, task, bundle, repo.evidence(task["id"]), projection["steps"]
    )["passed"]


async def test_waiting_ttl_cancel_and_ownership_do_not_advance_graph(client, setup):
    settings, repo, user, other = setup
    task, _ = new_diagnosis(setup, "那个商品的净销售额变化")
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    pending = DiagnosisStore(repo).projection(task["id"], user)["pending_input"]
    answer = ClarificationAnswer(
        interrupt_id=pending["interrupt_id"],
        expected_version=pending["expected_version"],
        choice="A",
    )
    with pytest.raises(NotFound):
        DiagnosisStore(repo).answer(task["id"], other, answer)
    with pytest.raises(Conflict):
        DiagnosisStore(repo).answer(
            task["id"], user, answer.model_copy(update={"choice": "foreign"})
        )
    with repo.session() as db:
        until = utcnow() - timedelta(seconds=1)
        db.get(Task, task["id"]).waiting_until = until
        db.get(InputRequest, pending["interrupt_id"]).waiting_until = until
        db.commit()
    assert repo.claim() is None
    assert repo.get_task(task["id"])["status"] == "expired"
    with pytest.raises(Conflict):
        DiagnosisStore(repo).answer(task["id"], user, answer)
    assert not repo.artifacts(task["id"])


async def test_wait_does_not_charge_runtime_and_resume_keeps_consumed_budget(setup):
    settings, repo, user, _ = setup
    task, _ = new_diagnosis(setup, "那个商品的净销售额变化")
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    before = repo.get_task(task["id"])
    pending = DiagnosisStore(repo).projection(task["id"], user)["pending_input"]
    with repo.session() as db:
        row = db.get(Task, task["id"])
        row.runtime_ms = 1000
        row.attempts = 10  # Normal human resumes must not use the recovery-attempt quota.
        row.updated_at = utcnow() - timedelta(hours=2)
        db.commit()
    DiagnosisStore(repo).answer(
        task["id"],
        user,
        ClarificationAnswer(
            interrupt_id=pending["interrupt_id"],
            expected_version=pending["expected_version"],
            choice="A",
        ),
    )
    ctx = repo.claim()
    assert ctx is not None
    assert 177 < repo.remaining_runtime(ctx) <= 179
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).process(ctx)
    done = repo.get_task(task["id"])
    assert done["status"] == "degraded", done
    assert done["metrics"]["runtime_ms"] >= 1000
    assert done["metrics"]["tool_calls"] >= before["metrics"]["tool_calls"]


async def test_cancel_after_files_before_publication_keeps_orphans_inaccessible(setup, monkeypatch):
    settings, repo, user, _ = setup
    task, _ = new_diagnosis(setup)
    original = DiagnosisStore.publish_artifacts

    def cancel(self, ctx, *args):
        repo.cancel(task["id"], user.id)
        return original(self, ctx, *args)

    monkeypatch.setattr(DiagnosisStore, "publish_artifacts", cancel)
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    assert repo.get_task(task["id"])["status"] == "cancelled"
    assert not repo.artifacts(task["id"])
    assert list((settings.data_dir / "artifacts").rglob("*.pdf"))


def test_old_worker_cannot_update_step_finding_report_or_artifact(setup):
    _, repo, user, _ = setup
    task, _ = new_diagnosis(setup)
    old = repo.claim()
    store = DiagnosisStore(repo)
    store.put_step(old, 1, tool_proposal("rank_contributors"))
    expire_lease(repo, task["id"])
    assert repo.claim() is not None
    for action in (
        lambda: store.update_step(old, 1, status="REJECTED"),
        lambda: store.save_findings(old, []),
        lambda: store.save_report(old, {}),
        lambda: store.publish_artifacts(old, 1, "0" * 64, {}),
    ):
        with pytest.raises(StaleLease):
            action()


async def test_diagnostic_api_dates_parent_scope_and_feedback_are_versioned(client, setup):
    settings, repo, user, _ = setup
    task, bundle = new_diagnosis(setup)
    authenticate(client)
    base = {
        "conversation_id": repo.create_conversation(user.id),
        "query": "追问退款变化",
        "profile": "commerce_diagnosis",
        "parent_task_id": task["id"],
    }
    bad = client.post(
        "/api/tasks",
        json={**base, "start_date": "2026-09-02"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert bad.status_code == 400
    response = client.post("/api/tasks", json=base, headers={"Idempotency-Key": str(uuid4())})
    assert response.status_code == 202, response.text
    followup = response.json()
    assert followup["bundle_id"] == bundle["bundle_id"] and followup["parent_task_id"] == task["id"]
    assert followup["parameters"]["end_date"] == "2026-09-01"
    async with checkpoint_store(settings) as saver:
        worker = Worker(repo, settings, saver)
        await worker.run_once()
        await worker.run_once()
    projection = client.get(f"/api/tasks/{task['id']}/diagnosis").json()
    before = repo.get_task(task["id"])["report_hash"]
    feedback = {
        "expected_version": projection["state_version"],
        "category": "useful",
        "comment": "核对记录",
    }
    assert client.post(f"/api/tasks/{task['id']}/feedback", json=feedback).status_code == 201
    assert repo.get_task(task["id"])["report_hash"] == before
    assert (
        client.post(
            f"/api/tasks/{task['id']}/feedback", json={**feedback, "expected_version": 0}
        ).status_code
        == 409
    )
    assert {e["id"] for e in repo.evidence(task["id"])}.isdisjoint(
        {e["id"] for e in repo.evidence(followup["id"])}
    )


def test_partial_explicit_dates_are_checked_against_bundle_not_legacy_defaults(client, setup):
    from app.diagnosis.schemas import InputBundleRequest

    _, repo, user, _ = setup
    dataset = committed(repo, user)
    bundle = create_bundle(
        repo,
        user,
        InputBundleRequest(
            sales_dataset_id=dataset["id"], period={"start": "2026-08-01", "end": "2026-08-01"}
        ),
    )
    authenticate(client)
    base = {
        "profile": "commerce_diagnosis",
        "conversation_id": repo.create_conversation(user.id),
        "query": "核对商品变化",
        "bundle_id": bundle["bundle_id"],
    }
    key = {"Idempotency-Key": str(uuid4())}
    partial = client.post("/api/tasks", json={**base, "end_date": "2026-08-01"}, headers=key)
    assert partial.status_code == 202, partial.text
    equivalent = client.post("/api/tasks", json=base, headers=key)
    assert equivalent.status_code == 202 and equivalent.json()["id"] == partial.json()["id"]
    assert partial.json()["parameters"]["start_date"] == "2026-08-01"


async def test_frozen_budget_cannot_expand_during_wait_or_deployment_change(setup):
    settings, repo, user, _ = setup
    settings.max_model_calls, settings.max_tool_calls = 1, 3
    task, _ = new_diagnosis(setup)
    assert task["parameters"]["budget"]["max_tool_calls"] == 3
    settings.max_tool_calls, settings.max_model_calls, settings.max_runtime_seconds = 100, 100, 1800
    model = ScriptedDecisionModel(
        tool_proposal("rank_contributors"), tool_proposal("analyze_refunds")
    )
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver, model=model).run_once()
    done = repo.get_task(task["id"])
    assert done["status"] == "degraded", done
    assert done["report"]["stop_reason"] == "model_budget"
    assert done["metrics"]["model_calls"] == 1 and done["metrics"]["tool_calls"] == 3
    budgets = DiagnosisStore(repo).projection(task["id"], user)["budgets"]
    assert budgets["max_model_calls"] == 1 and budgets["max_tool_calls"] == 3
    assert budgets["max_runtime_seconds"] == 180


async def test_demo_task_cannot_become_billable_through_changed_mode_or_real_client(setup):
    from langchain_openai import ChatOpenAI
    from pydantic import SecretStr

    settings, repo, _, _ = setup
    task, _ = new_diagnosis(setup)
    settings.agent_mode, settings.llm_model = "live", "offline-never-request"
    settings.openai_api_key = SecretStr("offline-never-request")
    model = ChatOpenAI(
        model="offline-never-request",
        api_key="offline",
        max_retries=0,
        base_url="http://127.0.0.1:1/must-not-connect",
    )
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver, model=model).run_once()
    done = repo.get_task(task["id"])
    assert done["status"] == "degraded", done
    assert done["metrics"]["model_calls"] == 0 and done["report"]["execution_mode"] == "demo_policy"


async def test_injected_real_sdk_client_is_labelled_live_with_offline_http_transport(setup):
    import json

    import httpx
    from pydantic import SecretStr

    from evaluations.diagnosis import live_model

    settings, repo, _, _ = setup
    settings.agent_mode = "live"
    settings.llm_model = "deepseek-flash"
    settings.openai_base_url = "https://api.deepseek.com"
    settings.openai_api_key = SecretStr("offline-test-key")
    task, _ = new_diagnosis(setup)
    reached = []

    async def transport(request):
        reached.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "id": "offline-mode-classification",
                "object": "chat.completion",
                "created": 1,
                "model": "deepseek-flash",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "action": "stop",
                                    "question": "本地协议测试停止",
                                    "stop_reason": "evidence_gap",
                                },
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        model = live_model(settings, client)
        async with checkpoint_store(settings) as saver:
            await Worker(repo, settings, saver, model=model).run_once()
    done = repo.get_task(task["id"])
    assert reached == ["/chat/completions"]
    assert done["report"]["execution_mode"] == "live_model"
    assert done["validation"]["passed"]
    assert done["metrics"]["model_calls"] == 1
    assert done["metrics"]["input_tokens"] == 20 and done["metrics"]["output_tokens"] == 10


async def test_revoke_access_after_report_blocks_details_events_evidence_and_artifacts(
    client, setup
):
    settings, repo, _, _ = setup
    task, bundle = new_diagnosis(setup)
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    artifact = repo.artifacts(task["id"])[0]
    with repo.session() as db:
        db.get(CommerceDataset, bundle["sales_dataset_id"]).committed = False
        db.commit()
    authenticate(client)
    for suffix in ("", "/diagnosis", "/evidence", "/events", "/stream"):
        assert client.get(f"/api/tasks/{task['id']}" + suffix).status_code == 404
    assert client.get("/api/artifacts/" + artifact["id"]).status_code == 404


async def test_old_approval_does_not_authorize_new_report_version(client, setup):
    from app.api.server import ReviewRequest

    settings, repo, user, _ = setup
    task, _ = new_diagnosis(setup, require_review=True)
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    store = DiagnosisStore(repo)
    before = repo.get_task(task["id"])
    pending = store.projection(task["id"], user)["pending_input"]
    approval = ReviewRequest(
        approved=True,
        interrupt_id=pending["interrupt_id"],
        expected_version=pending["expected_version"],
        report_hash=before["report_hash"],
    )
    store.review(task["id"], user, approval)
    ctx = repo.claim()
    revised = copy.deepcopy(before["report"])
    revised["stop_reason"] = "exploration_complete"
    new = store.save_report(ctx, revised)
    assert new["report"]["report_version"] == 2 and new["report_hash"] != before["report_hash"]
    with pytest.raises(Conflict):
        store.review(task["id"], user, approval)
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).process(ctx)
    assert not repo.artifacts(task["id"])


async def test_canceled_wait_cannot_turn_into_expired_on_late_answer(setup):
    settings, repo, user, _ = setup
    task, _ = new_diagnosis(setup, "那个商品的销售额变化")
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    pending = DiagnosisStore(repo).projection(task["id"], user)["pending_input"]
    repo.cancel(task["id"], user.id)
    with repo.session() as db:
        db.get(InputRequest, pending["interrupt_id"]).waiting_until = utcnow() - timedelta(
            seconds=1
        )
        db.commit()
    with pytest.raises(Conflict):
        DiagnosisStore(repo).answer(
            task["id"],
            user,
            ClarificationAnswer(
                interrupt_id=pending["interrupt_id"],
                expected_version=pending["expected_version"],
                choice="A",
            ),
        )
    assert repo.get_task(task["id"])["status"] == "cancelled"


def test_wait_expiration_reconciles_active_runtime_before_checkpoint_finish(setup):
    _, repo, _, _ = setup
    task, _ = new_diagnosis(setup)
    ctx = repo.claim()
    pending = DiagnosisStore(repo).ensure_input(
        ctx, "scope:step-1", "clarification", {"options": ["A", "B"]}
    )
    with repo.session() as db:
        now = utcnow()
        row = db.get(Task, task["id"])
        row.execution_started_at = now - timedelta(seconds=2)
        request = db.get(InputRequest, pending["interrupt_id"])
        request.created_at = now - timedelta(seconds=1)
        request.waiting_until = row.waiting_until = now - timedelta(milliseconds=100)
        db.commit()
    assert repo.claim() is None
    done = repo.get_task(task["id"])
    assert done["status"] == "expired" and 999 <= done["metrics"]["runtime_ms"] <= 1001


async def test_explicit_zero_change_does_not_support_concentrated_decline(setup):
    settings, repo, _, _ = setup
    payload = oracle_payload()
    for row in payload.daily_sales:
        row.paid_units = row.refunded_units = row.revenue_cents = row.refund_cents = 0
    task, _ = new_diagnosis(setup, "净销售额为什么下降", payload=payload)
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    done = repo.get_task(task["id"])
    assert done["validation"]["passed"], done
    assert not any(
        f["hypothesis_code"] == "sales_concentration" and f["status"] == "supported_observation"
        for f in done["report"]["observations"]
    )
    assert any(
        f["metric_key"] == "net_revenue.delta" and f["value"] == "0.00"
        for f in done["report"]["facts"]
    )
