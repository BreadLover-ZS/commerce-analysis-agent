"""Independent money oracles, immutable input authorization and durable tool attempts."""

import copy
import json
from datetime import date
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import func, select

from app.commerce.datasets import DatasetInput, commit_dataset, stage_dataset
from app.commerce.review import calculate_periods
from app.core.config import Settings
from app.diagnosis.inputs import create_bundle, get_bundle
from app.diagnosis.schemas import InputBundleRequest
from app.diagnosis.tools import MAX_MODEL_RESULT_BYTES, DiagnosisToolGateway, model_summary
from app.research.schemas import TaskRequest
from app.storage.models import (
    AnalysisInputBundle,
    CommerceDataset,
    Evidence,
    Task,
    ToolInvocation,
    ToolQueryResult,
)
from app.storage.repository import BudgetExceeded, Conflict, NotFound, StaleLease, TaskCancelled
from tests.conftest import authenticate, expire_lease


def oracle_payload():
    # Independent documented cents oracle: 140000 -> 120000; A -40000, B +20000.
    # The two 1-day periods are deliberately nonadjacent.
    return DatasetInput.model_validate(
        {
            "products": [
                {"sku": sku, "name": sku, "category": category, "active_from": "2026-08-01"}
                for sku, category in (("A", "饮具"), ("B", "收纳"))
            ],
            "daily_sales": [
                {
                    "sku": sku,
                    "day": day,
                    "revenue_cents": revenue,
                    "refund_cents": refund,
                    "paid_units": paid,
                    "refunded_units": refunded,
                }
                for sku, day, revenue, refund, paid, refunded in (
                    ("A", "2026-08-01", 100000, 10000, 100, 10),
                    ("B", "2026-08-01", 50000, 0, 50, 0),
                    ("A", "2026-09-01", 80000, 30000, 100, 30),
                    ("B", "2026-09-01", 70000, 0, 50, 0),
                )
            ],
        }
    )


def bundle_request(dataset_id, **kwargs):
    return InputBundleRequest(
        sales_dataset_id=dataset_id,
        period={"start": "2026-09-01", "end": "2026-09-01"},
        comparison_period={"start": "2026-08-01", "end": "2026-08-01"},
        **kwargs,
    )


def committed(repo, user, payload=None):
    info = stage_dataset(repo, user, payload or oracle_payload(), "手算样本", True, "0" * 64)
    return commit_dataset(repo, user, info["id"])


def diagnosis_task(repo, user, bundle, sources=None):
    task = repo.create_task(
        user,
        TaskRequest(
            conversation_id=repo.create_conversation(user.id),
            query="核对净销售额与退款的记账贡献",
            sources=sources or ["sales"],
            profile="commerce_diagnosis",
            bundle_id=bundle["bundle_id"],
        ),
        str(uuid4()),
    )
    return task, repo.claim()


def gateway(setup, payload=None, **scope):
    _, repo, user, _ = setup
    dataset = committed(repo, user, payload)
    bundle = create_bundle(repo, user, bundle_request(dataset["id"], **scope))
    task, ctx = diagnosis_task(repo, user, bundle)
    return DiagnosisToolGateway(repo, ctx), task, bundle


def test_gv02_independent_offset_contributions_and_200_percent(setup):
    gw, task, bundle = gateway(setup)
    compare = gw.invoke("compare_periods", {}, "baseline")
    facts = compare["fact_catalog"]
    assert facts["net_revenue.current"]["value"] == "1200.00"
    assert facts["net_revenue.comparison"]["value"] == "1400.00"
    assert facts["net_revenue.delta"]["raw_value"] == -20000
    assert compare["status"] == "ok"
    assert any("不相邻" in s for s in compare["limitations"])
    rank = gw.invoke("rank_contributors", {"top_k": 1}, "step-1")
    assert rank["contributors"] == [
        {
            "entity": "A",
            "delta_cents": -40000,
            "fact_key": "contribution.0",
            "share_of_total_pct": "200.00",
            "share_fact_key": "contribution.0.share",
        }
    ]
    assert rank["reconciliation"] == {
        "total_delta_cents": -20000,
        "shown_delta_cents": -40000,
        "other_delta_cents": 20000,
        "full_delta_cents": -20000,
        "balanced": True,
    }
    assert rank["truncated"] and len(rank["all_contributors"]) == 2
    for f in rank["fact_catalog"].values():
        assert f["bundle_hash"] == bundle["bundle_hash"]
        assert f["comparison_period"] == bundle["comparison_period"]
        assert f["evidence_id"] in rank["evidence_ids"]
    refunds = gw.invoke("analyze_refunds", {}, "step-2")
    assert refunds["net_change_identity"] == {
        "net_delta_cents": -20000,
        "revenue_delta_cents": 0,
        "refund_delta_cents": 20000,
    }
    assert refunds["fact_catalog"]["refund_ratio.current"]["value"] == "20.00"
    assert refunds["fact_catalog"]["refund_ratio.comparison"]["value"] == "6.67"
    assert len(gw.repo.evidence(task["id"])) == 3


def test_gv01_gv21_filters_topk_and_normalized_query_identity(setup):
    gw, task, _ = gateway(setup)
    a = gw.invoke("compare_periods", {"filters": {"skus": ["A"]}}, "a")
    b = gw.invoke("compare_periods", {"filters": {"skus": ["B"]}}, "b")
    assert a["query_spec_hash"] != b["query_spec_hash"]
    assert a["evidence_ids"] != b["evidence_ids"]
    assert a["fact_catalog"]["net_revenue.delta"]["raw_value"] == -40000
    assert b["fact_catalog"]["net_revenue.delta"]["raw_value"] == 20000
    same_a = gw.invoke("compare_periods", {"filters": {"categories": ["饮具"]}}, "same-a")
    assert same_a["reused"] and same_a["call_id"] == a["call_id"]
    full = gw.invoke("compare_periods", {}, "all")
    normalized = gw.invoke("compare_periods", {"filters": {"skus": ["B", "A"]}}, "all-2")
    assert normalized["reused"] and normalized["call_id"] == full["call_id"]
    one = gw.invoke("rank_contributors", {"top_k": 1}, "rank-1")
    two = gw.invoke("rank_contributors", {"top_k": 2}, "rank-2")
    assert one["query_spec_hash"] != two["query_spec_hash"]
    assert gw.repo.get_task(task["id"])["metrics"]["tool_calls"] == 5
    with gw.repo.session() as db:
        assert db.scalar(select(func.count()).select_from(ToolQueryResult)) == 5
        assert db.scalar(select(func.count()).select_from(ToolInvocation)) == 5


@pytest.mark.parametrize(
    "tool,args,status",
    [
        ("execute_sql", {}, "unavailable"),
        ("compare_periods", {"metric": "profit"}, "unsupported_metric"),
        ("compare_periods", {"user_id": "other"}, "invalid_argument"),
        ("rank_contributors", {"top_k": True}, "invalid_argument"),
        ("rank_contributors", {"top_k": 21}, "invalid_argument"),
        ("rank_contributors", {"filters": {"skus": ["A", "A"]}}, "invalid_argument"),
        ("compare_periods", {"period": {"start": "2000-01-01"}}, "invalid_argument"),
        ("drilldown_series", {"filters": {}}, "invalid_argument"),
        ("drilldown_series", {"filters": {"skus": ["A", "B"]}}, "invalid_argument"),
        ("inspect_dataset", {"path": "/etc/passwd"}, "invalid_argument"),
        ("compare_periods", {"filters": {"skus": ["unknown"]}}, "invalid_argument"),
    ],
)
def test_gv06_rejected_arguments_never_execute_or_consume(setup, tool, args, status):
    gw, task, _ = gateway(setup)
    assert gw.invoke(tool, args, "invalid")["status"] == status
    assert gw.repo.get_task(task["id"])["metrics"]["tool_calls"] == 0
    assert not gw.repo.evidence(task["id"])


def test_gv03_ratio_up_when_refund_amount_and_count_fall(setup):
    payload = oracle_payload()
    for row in payload.daily_sales:
        if row.day == date(2026, 9, 1):
            row.paid_units = 10
            row.refunded_units = 2
            row.refund_cents = 100
    gw, _, _ = gateway(setup, payload)
    result = gw.invoke("analyze_refunds", {}, "refund")
    assert result["ratio_increased"] is True
    assert result["refund_amount_delta_cents"] == -9800
    assert result["denominator_effect"] is True


def test_gv04_missing_and_explicit_zero_remain_different(setup):
    payload = oracle_payload()
    payload.daily_sales = [
        r for r in payload.daily_sales if not (r.sku == "A" and r.day.month == 9)
    ]
    for row in payload.daily_sales:
        if row.sku == "B" and row.day.month == 9:
            row.revenue_cents = row.refund_cents = row.paid_units = row.refunded_units = 0
    gw, _, _ = gateway(setup, payload)
    rank = gw.invoke("rank_contributors", {}, "rank")
    assert rank["status"] == "incomparable" and rank["reconciliation"] is None
    missing = gw.invoke("drilldown_series", {"filters": {"skus": ["A"]}}, "missing")
    point = next(s for s in missing["series"] if s["period_name"] == "current")
    assert point["status"] == "missing" and not point["fact_keys"]
    zero = gw.invoke("drilldown_series", {"filters": {"skus": ["B"]}}, "zero")
    point = next(s for s in zero["series"] if s["period_name"] == "current")
    assert point["status"] == "zero" and point["fact_keys"]
    assert zero["fact_catalog"][point["fact_keys"]["net_revenue"]]["raw_value"] == 0


def test_gv22_new_sku_not_active_before_launch_and_cross_period_refund(setup):
    payload = oracle_payload()
    payload.products[0].active_from = date(2026, 9, 1)
    payload.daily_sales = [
        r for r in payload.daily_sales if not (r.sku == "A" and r.day.month == 8)
    ]
    payload.daily_sales[1].refund_cents = 90000  # Allowed to exceed same-day payment.
    gw, _, _ = gateway(setup, payload)
    series = gw.invoke("drilldown_series", {"filters": {"skus": ["A"]}}, "new")
    assert (
        next(s for s in series["series"] if s["period_name"] == "comparison")["status"]
        == "not_active"
    )
    assert series["status"] == "incomparable"
    overall = gw.invoke("rank_contributors", {}, "rank")
    assert overall["reconciliation"]["balanced"]


@pytest.mark.parametrize("prior,current", [(0, 100), (-100, 0), (0, 0)])
def test_gv04_nonpositive_base_does_not_produce_change_rate(setup, prior, current):
    payload = oracle_payload()
    for row in payload.daily_sales:
        amount = prior if row.day.month == 8 else current
        row.revenue_cents, row.refund_cents = max(amount, 0), max(-amount, 0)
        row.paid_units = row.refunded_units = 0
    gw, _, _ = gateway(setup, payload)
    result = gw.invoke("compare_periods", {}, "compare")
    assert result["change_rate_unavailable"] == "non_positive_base"
    assert "net_revenue.change_pct" not in result["fact_catalog"]
    refunds = gw.invoke("analyze_refunds", {}, "refund")
    assert refunds["ratio_increased"] is None and refunds["ratio_fact_keys"] == {}
    assert refunds["ratio_unavailable"] == {
        "current": "zero_denominator",
        "comparison": "zero_denominator",
    }
    if prior == current:
        rank = gw.invoke("rank_contributors", {}, "rank")
        assert all(x["share_of_total_pct"] is None for x in rank["contributors"])


def test_gv05_gv26_owner_sources_scope_and_cache_reauthorization(setup):
    _, repo, user, other = setup
    gw, task, bundle = gateway(setup, allowed_skus=["A"])
    first = gw.invoke("compare_periods", {}, "one")
    assert first["scope"] == {"skus": ["A"]}
    assert (
        gw.invoke("compare_periods", {"filters": {"skus": ["B"]}}, "two")["status"]
        == "invalid_argument"
    )
    with pytest.raises(NotFound):
        get_bundle(repo, other, bundle["bundle_id"])
    with pytest.raises(NotFound):
        create_bundle(repo, other, bundle_request(bundle["sales_dataset_id"]))
    with repo.session() as db:
        db.get(CommerceDataset, bundle["sales_dataset_id"]).committed = False
        db.commit()
    for operation in (gw.catalog, lambda: gw.invoke("compare_periods", {}, "cached")):
        with pytest.raises(NotFound):
            operation()
    assert repo.get_task(task["id"])["metrics"]["tool_calls"] == 1
    with repo.session() as db:
        db.get(CommerceDataset, bundle["sales_dataset_id"]).committed = True
        item = db.get(Task, task["id"])
        item.parameters = {**item.parameters, "sources": ["sales", "documents"]}
        db.commit()
    with pytest.raises(Conflict):
        gw.catalog()


def test_gv05_mutated_bundle_or_sales_payload_rejected(setup):
    _, repo, user, _ = setup
    gw, _, bundle = gateway(setup)
    with repo.session() as db:
        source = db.get(CommerceDataset, bundle["sales_dataset_id"])
        source.payload = {**source.payload, "schema_version": 99}
        db.commit()
    with pytest.raises(Conflict):
        gw.invoke("compare_periods", {}, "changed-source")
    # Test the input hash independently of source verification.
    with repo.session() as db:
        item = db.get(AnalysisInputBundle, bundle["bundle_id"])
        item.payload = {**item.payload, "allowed_skus": ["foreign"]}
        db.commit()
    with pytest.raises(Conflict):
        get_bundle(repo, user, bundle["bundle_id"])


def test_gv11_tool_result_before_checkpoint_reuses_same_call_and_evidence(setup):
    gw, task, _ = gateway(setup)
    before = gw.invoke("compare_periods", {}, "stable-step")
    expire_lease(gw.repo, task["id"])
    recovered = DiagnosisToolGateway(gw.repo, gw.repo.claim())
    after = recovered.invoke("compare_periods", {}, "stable-step")
    assert after["reused"] and after["call_id"] == before["call_id"]
    assert after["evidence_ids"] == before["evidence_ids"]
    assert gw.repo.get_task(task["id"])["metrics"]["tool_calls"] == 1
    with pytest.raises(StaleLease):
        gw.invoke("compare_periods", {}, "stale")


def test_gv12_old_worker_cannot_publish_late_result(setup, monkeypatch):
    gw, task, _ = gateway(setup)
    original = gw._calculate

    def takeover(*args):
        result = original(*args)
        expire_lease(gw.repo, task["id"])
        assert gw.repo.claim() is not None
        return result

    monkeypatch.setattr(gw, "_calculate", takeover)
    with pytest.raises(StaleLease):
        gw.invoke("compare_periods", {}, "late")
    assert gw.repo.evidence(task["id"]) == []


def test_gv11_incomplete_attempt_recomputed_with_new_budget_and_retained_unknown(
    setup, monkeypatch
):
    gw, task, _ = gateway(setup)
    original = gw._publish

    def die(*args):
        raise SystemExit("simulated hard crash before result commit")

    monkeypatch.setattr(gw, "_publish", die)
    with pytest.raises(SystemExit):
        gw.invoke("compare_periods", {}, "step")
    expire_lease(gw.repo, task["id"])
    recovered = DiagnosisToolGateway(gw.repo, gw.repo.claim())
    recovered._publish = original.__func__.__get__(recovered)
    assert recovered.invoke("compare_periods", {}, "step")["status"] == "ok"
    assert gw.repo.get_task(task["id"])["metrics"]["tool_calls"] == 2
    with gw.repo.session() as db:
        attempts = list(db.scalars(select(ToolInvocation).order_by(ToolInvocation.attempt)))
        assert [(c.attempt, c.status) for c in attempts] == [(1, "UNKNOWN"), (2, "SUCCEEDED")]
        assert db.scalar(select(func.count()).select_from(Evidence)) == 1


def test_gv14_cancel_during_tool_prevents_result_commit(setup, monkeypatch):
    gw, task, _ = gateway(setup)
    original = gw._calculate

    def cancel(*args):
        result = original(*args)
        gw.repo.cancel(task["id"], gw.ctx.user_id)
        return result

    monkeypatch.setattr(gw, "_calculate", cancel)
    with pytest.raises(TaskCancelled):
        gw.invoke("compare_periods", {}, "cancelled")
    assert gw.repo.evidence(task["id"]) == []


def test_gv27_failures_count_cache_does_not_and_hard_limit_stops_execution(setup, monkeypatch):
    gw, task, _ = gateway(setup)
    original = gw._calculate

    def fail(*args):
        raise ValueError("private uploaded data must never reach the result")

    monkeypatch.setattr(gw, "_calculate", fail)
    failed = gw.invoke("compare_periods", {}, "failed")
    assert failed["status"] == "unavailable" and "private" not in json.dumps(failed)
    monkeypatch.setattr(gw, "_calculate", original)
    assert gw.invoke("compare_periods", {}, "retry")["status"] == "ok"
    assert gw.invoke("compare_periods", {}, "cache")["reused"]
    with gw.repo.session() as db:
        db.get(Task, task["id"]).tool_calls = 10
        db.commit()
    with pytest.raises(BudgetExceeded):
        gw.invoke("rank_contributors", {}, "budget")
    assert gw.repo.get_task(task["id"])["metrics"]["tool_calls"] == 10
    with gw.repo.session() as db:
        assert [
            c.status for c in db.scalars(select(ToolInvocation).order_by(ToolInvocation.attempt))
        ] == ["FAILED", "SUCCEEDED"]


@pytest.mark.parametrize(
    "comparison",
    [
        {"start": "2026-09-01", "end": "2026-09-01"},
        {"start": "2026-10-01", "end": "2026-10-01"},
        {"start": "2026-08-01", "end": "2026-08-02"},
    ],
)
def test_gv22_invalid_comparison_periods_rejected(comparison):
    with pytest.raises(ValidationError):
        InputBundleRequest(
            sales_dataset_id=uuid4(),
            period={"start": "2026-09-01", "end": "2026-09-01"},
            comparison_period=comparison,
        )


def test_explicit_calculation_core_uses_actual_custom_period():
    value = calculate_periods(
        oracle_payload().model_dump(mode="json"),
        date(2026, 9, 1),
        date(2026, 9, 1),
        dataset_id="id",
        content_hash="hash",
        sample=True,
        comparison_start=date(2026, 8, 1),
        comparison_end=date(2026, 8, 1),
    )
    assert value["previous_period"]["start"] == "2026-08-01"
    assert value["metrics"]["net_revenue_delta"] == "-200.00"


def test_input_api_is_idempotent_authenticated_and_immutable(client, setup):
    _, repo, user, _ = setup
    dataset = committed(repo, user)
    request = bundle_request(dataset["id"]).model_dump(mode="json")
    assert client.post("/api/analysis-inputs", json=request).status_code == 401
    authenticate(client)
    missing_csrf = client.headers.pop("X-CSRF-Token")
    assert client.post("/api/analysis-inputs", json=request).status_code == 403
    client.headers["X-CSRF-Token"] = missing_csrf
    first = client.post("/api/analysis-inputs", json=request)
    assert first.status_code == 201
    again = client.post("/api/analysis-inputs", json=request)
    assert again.json() == first.json()
    bundle = first.json()
    assert client.get("/api/analysis-inputs/" + bundle["bundle_id"]).json() == bundle
    for changes in (
        {"allowed_skus": ["unknown"]},
        {"metric_version": "other"},
        {"allowed_skus": ["A", "A"]},
    ):
        assert client.post("/api/analysis-inputs", json={**request, **changes}).status_code == 400
    default = {k: v for k, v in request.items() if k != "comparison_period"}
    derived = client.post("/api/analysis-inputs", json=default).json()
    assert derived["comparison_period"] == {"start": "2026-08-31", "end": "2026-08-31"}
    with repo.session() as db:
        assert db.scalar(select(func.count()).select_from(AnalysisInputBundle)) == 2
    client.post("/api/auth/logout")
    authenticate(client, "other@example.com", "other-commerce-2026")
    assert client.get("/api/analysis-inputs/" + bundle["bundle_id"]).status_code == 404
    assert client.post("/api/analysis-inputs", json=request).status_code == 404


def test_new_data_upload_cannot_change_existing_input(setup):
    _, repo, user, _ = setup
    gw, _, bundle = gateway(setup)
    payload = oracle_payload()
    payload.daily_sales[-1].revenue_cents = 90000
    newer = committed(repo, user, payload)
    assert newer["id"] != bundle["sales_dataset_id"]
    assert (
        get_bundle(repo, user, bundle["bundle_id"])["sales_content_hash"]
        == bundle["sales_content_hash"]
    )
    assert (
        gw.invoke("compare_periods", {}, "fixed")["fact_catalog"]["net_revenue.current"]["value"]
        == "1200.00"
    )


def test_model_summary_size_limit_and_full_evidence_is_unchanged(setup):
    gw, _, _ = gateway(setup)
    value = gw.invoke("rank_contributors", {}, "rank")
    original = copy.deepcopy(value)
    assert "all_contributors" not in model_summary(value)
    assert original == value
    large = {**value, "series": [{"payload": "字" * 4000}] * 180}
    summary = model_summary(large)
    assert summary["summary_truncated"]
    assert len(json.dumps(summary, ensure_ascii=False).encode()) <= MAX_MODEL_RESULT_BYTES


def test_empty_migration_roundtrip_and_populated_rollback_refused(tmp_path):
    from app.core.auth import bootstrap_user
    from app.storage.models import User
    from app.storage.repository import Repository

    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        database_url="sqlite:///" + str(tmp_path / "migrated.sqlite"),
    )
    config = Config("alembic.ini")
    config.attributes["settings"] = settings
    command.upgrade(config, "head")
    command.downgrade(config, "c21a01600001")
    command.upgrade(config, "head")
    repo = Repository(settings)
    try:
        bootstrap_user(repo, settings)
        with repo.session() as db:
            user = db.scalar(select(User).where(User.email == settings.bootstrap_email))
        dataset = committed(repo, user)
        create_bundle(repo, user, bundle_request(dataset["id"]))
        with pytest.raises(RuntimeError, match="禁止自动删除"):
            command.downgrade(config, "c21a01600001")
        with repo.session() as db:
            assert db.scalar(select(func.count()).select_from(AnalysisInputBundle)) == 1
    finally:
        repo.engine.dispose()
