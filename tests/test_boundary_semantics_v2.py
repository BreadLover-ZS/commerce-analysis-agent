"""Literal-author meaning, not agreement between two detectors, is the oracle."""

import pytest

from app.commerce.answer_contract_v5 import request_contract
from evaluations.diagnosis_request_checks_v4 import boundary_request

CASES = [
    ("说明比较期不适用的边界。", True),
    (
        "请给出授权范围本期已有净销售额，说明比较期（早于启用日）不适用的边界，不补造比较期零值。",
        True,
    ),
    ("请解释启用前为什么不能比较。", True),
    ("请说明这份数据的局限。", True),
    ("哪些口径不能计算？", True),
    ("请说明尚未启用时比较为何不成立。", True),
    ("请列明哪些指标不支持计算。", True),
    ("比较期不能用于比较，请解释原因。", True),
    ("请说明商品启用后缺行有什么限制。", True),
    ("请说明哪些条件具备、哪些仍缺少。", True),
    ("请给出本期净销售额。", False),
    ("无需说明不适用的边界，请解释退款原因。", False),
    ("不要解释比较期不适用的边界，请列出哪些商品下降。", False),
    ("无需说明不可比较的边界，只给出本期净销售额。", False),
    ("不要解释不可比较的边界，请给出本期净销售额。", False),
    ("不用说明不适用的边界，请给出本期净销售额。", False),
    ("请给出净销售额，不要认为比较期不适用。", False),
    ("请引用边界服务接口规范。", False),
]


@pytest.mark.parametrize("query,expected", CASES)
def test_production_boundary_matches_literal_author(query, expected):
    assert request_contract(query)["boundary_intent"] is expected


@pytest.mark.parametrize("query,expected", CASES)
def test_independent_boundary_matches_literal_author(query, expected):
    assert boundary_request(query) is expected


def test_boundary_question_preserves_requested_current_value():
    c = request_contract(CASES[1][0])
    assert c["boundary_intent"] and not c["pure_boundary"]
    assert c["value_metrics"] == ["net_revenue"]


def test_frozen_predecessor_preserves_original_interpretation():
    from app.commerce.answer_contract_v4 import request_contract as legacy
    from evaluations.diagnosis_request_checks_v3 import boundary_request as old_check

    q = "说明比较期不适用的边界。"
    assert legacy(q)["version"] == "answer-contract-v4" and not legacy(q)["boundary_intent"]
    assert not old_check(q)


@pytest.mark.parametrize("group", ["agent", "fixed"])
@pytest.mark.parametrize("version", [1, 2])
async def test_boundary_policy_is_frozen_through_worker_and_report(setup, group, version):
    from uuid import uuid4

    from app.commerce.datasets import DatasetInput
    from app.diagnosis.inputs import create_bundle
    from app.research.schemas import TaskRequest
    from evaluations.diagnosis_runtime import collect, execute
    from tests.test_diagnosis_tools import bundle_request, committed, oracle_payload

    settings, repo, user, _ = setup
    settings.answer_boundary_version = version
    settings.diagnosis_runtime = "v16"
    settings.commerce_review_answer_version = 6
    raw = oracle_payload().model_dump(mode="json")
    for p in raw["products"]:
        p["active_from"] = "2026-09-01"
    raw["daily_sales"] = [r for r in raw["daily_sales"] if r["day"] == "2026-09-01"]
    data = committed(repo, user, DatasetInput.model_validate(raw))
    bundle = create_bundle(repo, user, bundle_request(data["id"]))
    q = "请给出授权范围本期已有净销售额，说明比较期（早于启用日）不适用的边界，不补造比较期零值。"
    request = TaskRequest(
        conversation_id=repo.create_conversation(user.id),
        query=q,
        bundle_id=bundle["bundle_id"],
        profile="commerce_diagnosis" if group == "agent" else "commerce_review",
        strategy="diagnostic" if group == "agent" else "workflow",
        sources=["sales"],
    )
    key = str(uuid4())
    task = repo.create_task(user, request, key)
    expected = "answer-contract-v5" if version == 2 else "answer-contract-v4"
    if group == "agent":
        assert task["parameters"]["answer_contract"]["version"] == expected
    settings.answer_boundary_version = 3 - version
    assert repo.create_task(user, request, key)["id"] == task["id"]
    # Deployment changes must not reinterpret the frozen task.
    await execute(repo, settings)
    record = collect(repo, task["id"], group)
    assert record["task"]["validation"]["passed"], record["task"]["validation"]
    if group == "agent":
        rep = record["task"]["report"]
        assert rep["request_contract"]["version"] == expected
        assert rep["answer_completion"]["status"] == (
            "boundary_explained" if version == 2 else "incomplete"
        )
        if version == 2:
            assert [g["code"] for g in rep["gap_records"]] == ["period_not_applicable"]
    else:
        payload = next(
            e["payload"]
            for e in record["evidence"]
            if e["payload"].get("review_profile") == "commerce_review"
        )
        assert payload["answer_contract"]["version"] == expected
        assert payload["answer_completion"]["status"] == (
            "boundary_explained" if version == 2 else "fulfilled"
        )
        text = " ".join(
            c["text"] for s in record["task"]["report"]["sections"] for c in s["claims"]
        )
        assert ("边界与限制已答齐" in text) is (version == 2)
        if version == 2:
            import copy

            from evaluations.diagnosis_request_checks_v4 import completion_checks

            assert "源数据仍不完整" not in text
            business = {
                "query": q,
                "sales": raw,
                "period": {"start": "2026-09-01", "end": "2026-09-01"},
                "comparison_period": {"start": "2026-08-01", "end": "2026-08-01"},
            }
            report = record["task"]["report"]
            sources = [e["payload"] for e in record["evidence"]]
            assert completion_checks(business, report, sources, ["A", "B"])[
                "v5_not_applicable_not_claimed_missing"
            ]
            wrong = copy.deepcopy(report)
            next(
                c
                for s in wrong["sections"]
                for c in s["claims"]
                if c["id"] == "review-answer-completion"
            )["text"] = "缺口与限制已答齐，源数据仍不完整。"
            assert not completion_checks(business, wrong, sources, ["A", "B"])[
                "v5_not_applicable_not_claimed_missing"
            ]
    assert not record["model_invocations"]
