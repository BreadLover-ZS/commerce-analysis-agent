"""Independent counterexamples from historical lessons; never contact a provider."""

import itertools
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from app.commerce.explanation_contract_v6 import CODES, contract
from app.commerce.review_report_v5 import compose, required_explanations
from app.research.evidence import validate_report
from evaluations import diagnosis
from evaluations.diagnosis import RequestBudget
from evaluations.diagnosis_runtime import execute
from tests.test_diagnosis_v16 import fixed_evidence, narrative, sdk
from tests.test_review_bound_inputs import review_task


@pytest.mark.parametrize(
    "query,expected",
    [
        ("分别列出两期支付金额和退款金额，不要解释原因。", set()),
        ("不要逐一解释原因，只需分别列出支付金额和退款金额。", set()),
        ("逐一讨论退款和数据质量，不讨论支付原因。", {"refund_drivers", "data_quality"}),
        ("分别解释商品组合和退款；不要讨论数据质量。", {"product_mix", "refund_drivers"}),
    ],
)
def test_negative_constraints_are_not_made_positive_obligations(query, expected):
    assert required_explanations({"objective": query, "answer_version": 6}) == expected


async def test_formal_entry_refuses_missing_frozen_plan_before_credentials(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("credentials were reached before formal admission")

    monkeypatch.setattr(diagnosis, "live_configuration", forbidden)
    with pytest.raises(ValueError, match="冻结"):
        await diagnosis.evaluate(
            SimpleNamespace(frozen_plan=None, final_holdout=True, split="holdout", mode="live")
        )


TOPICS = [("退款", "refund_drivers"), ("商品组合", "product_mix"), ("数据质量", "data_quality")]
CASES = [
    (f"{positive}{a[0]}和{b[0]}{separator}{negative}{c[0]}。", {a[1], b[1]}, {c[1]})
    for positive, (a, b, c), separator, negative in itertools.product(
        ["分别解释", "逐一讨论"],
        itertools.permutations(TOPICS),
        ["，", "；", "。", "但"],
        ["不要讨论", "不解释", "无需解释", "别解释"],
    )
]


@pytest.mark.parametrize("query,required,excluded", CASES)
def test_clause_polarity_goldens_do_not_reuse_the_implementation_parser(query, required, excluded):
    value = contract(query)
    assert set(value["required"]) == required and set(value["excluded"]) == excluded


@pytest.mark.parametrize(
    "negative",
    [
        "不要解释原因",
        "不要逐一解释原因",
        "不用分别讨论原因",
        "无需解释原因",
        "不需要解释原因",
        "别解释原因",
        "Do not explain causes",
    ],
)
def test_global_denial_allows_empty_analysis_and_forbids_model_hypotheses(negative):
    query = "分别列出两期支付金额和退款金额，" + negative + "。"
    value = contract(query)
    assert value["required"] == [] and set(value["excluded"]) == CODES
    evidence = fixed_evidence()
    evidence[0]["payload"].update(answer_version=6, objective=query)
    reply = narrative(evidence, 1)
    with pytest.raises(ValueError, match="scope"):
        compose(evidence, [], reply)
    reply["analysis"] = []
    report, _ = compose(evidence, [], reply)
    assert "请求已答齐" in str(report.model_dump())
    assert validate_report(report, evidence)["passed"]


def test_forbidden_fourth_choice_is_rejected_before_capacity_selection():
    evidence = fixed_evidence()
    evidence[0]["payload"].update(
        answer_version=6, objective="分别解释退款和商品组合，不讨论支付原因。"
    )
    reply = narrative(evidence, 4)
    assert reply["analysis"][-1] == "sales_drivers"
    with pytest.raises(ValueError, match="scope"):
        compose(evidence, [], reply)


@pytest.mark.parametrize("query", ["分别解释退款，但不要解释任何原因。", "不要不解释支付原因。"])
def test_ambiguous_or_conflicting_constraints_fail_before_task_admission(setup, query):
    from app.research.schemas import TaskRequest
    from tests.test_diagnosis_tools import committed

    settings, repo, user, _ = setup
    settings.commerce_review_answer_version = 6
    dataset = committed(repo, user)
    with pytest.raises(ValueError):
        repo.create_task(
            user,
            TaskRequest(
                conversation_id=repo.create_conversation(user.id),
                query=query,
                profile="commerce_review",
                dataset_id=dataset["id"],
                sources=["sales"],
                strategy="workflow",
            ),
            "invalid-contract",
        )
    assert repo.list_tasks(user.id) == []


def test_no_advice_is_not_a_request_to_hide_required_values():
    evidence = fixed_evidence()
    evidence[0]["payload"].update(
        answer_version=6,
        objective="分别列出两期支付金额、退款金额及差额，不要解释原因，不要给出建议。",
    )
    report, meta = compose(evidence, [], {"analysis": [], "recommendations": []})
    assert report.sections[4].claims == []
    assert any("行动建议" in note for note in report.limitations)
    assert meta["required_explanations"] == []
    assert validate_report(report, evidence)["passed"]
    assert any(
        m.key == "comparison_gross_revenue"
        for s in report.sections
        for c in s.claims
        for m in c.metrics
    )


@pytest.mark.parametrize("corrupt_rebuild", [False, True])
async def test_semantic_budget_fallback_is_revalidated_before_publication(
    setup, tmp_path, monkeypatch, corrupt_rebuild
):
    import app.research.graph as module

    settings, repo, _, _ = setup
    settings.commerce_review_answer_version = 6
    settings.agent_mode, settings.openai_api_key, settings.llm_model = (
        "live",
        SecretStr("local-offline"),
        "local-mock",
    )
    task, _, _ = review_task(setup)
    builder, count = module.deterministic_report, []

    def rebuilt(*args, **kwargs):
        count.append(1)
        report = builder(*args, **kwargs)
        if corrupt_rebuild and len(count) > 1:
            report.sections[0].claims[0].metrics[0].value = "999999.00"
        return report

    monkeypatch.setattr(module, "deterministic_report", rebuilt)
    budget = RequestBudget(
        1, max_total_tokens=1000, context_ceiling=32, journal=tmp_path / "journal.jsonl", protocol=2
    )
    requests = []

    async def transport(request):
        requests.append(request)
        body = json.loads(request.content)
        name = body["tools"][0]["function"]["name"]
        assert name == "ReviewNarrativeV6"
        return httpx.Response(
            200,
            json={
                "id": "local-narrative",
                "model": "local-mock",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "local-call",
                                    "type": "function",
                                    "function": {
                                        "name": name,
                                        "arguments": '{"analysis":[],"recommendations":[]}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport),
        event_hooks={"request": [budget.before_send], "response": [budget.after_response]},
    ) as client:
        await execute(repo, settings, model=sdk(client))
    done = repo.get_task(task["id"])
    assert len(requests) == 1 and len(count) >= 2
    if corrupt_rebuild:
        assert done["status"] == "failed" and done["error_code"] == "report_validation_failed", done
        assert repo.artifacts(task["id"]) == []
    else:
        assert done["status"] == "degraded" and done["validation"]["passed"], done
        assert done["validation"]["model_stopped"] and "请求已答齐" not in str(done["report"])


def test_new_constraints_do_not_reinterpret_an_old_idempotent_v5_request(setup):
    from uuid import uuid4

    settings, repo, user, _ = setup
    settings.commerce_review_answer_version = 5
    _, _, request = review_task(setup)
    request = request.model_copy(update={"query": "分别解释退款，但不要解释任何原因。"})
    key = str(uuid4())
    old = repo.create_task(user, request, key)
    settings.commerce_review_answer_version = 6
    retried = repo.create_task(user, request, key)
    assert retried["id"] == old["id"] and retried["parameters"] == old["parameters"]
    assert retried["graph_version"] == "commerce-review-bundle-v5"
    with pytest.raises(ValueError):
        repo.create_task(user, request, str(uuid4()))
