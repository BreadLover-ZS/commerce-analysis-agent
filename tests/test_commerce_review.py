"""Business oracles, import boundaries and real worker integration for the review profile."""

import hashlib
from datetime import date
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import select

from app.commerce.datasets import (
    SCENARIOS,
    DatasetInput,
    canonical,
    commit_dataset,
    csv_bytes,
    list_datasets,
    parse_csv,
    scenario_payload,
    stage_dataset,
)
from app.commerce.review import calculate_review, dataset_review
from app.research.context import evidence_package
from app.research.evidence import REVIEW_SECTIONS, deterministic_report, validate_report
from app.research.schemas import TaskRequest
from app.storage.models import Artifact, CommerceDataset, DailySale
from app.storage.repository import NotFound, Repository
from app.tasks.worker import Worker, checkpoint_store
from scripts.backup_local import create_backup, restore_backup
from tests.conftest import authenticate
from tests.fake_model import ScriptedCommerceModel


def hand_payload():
    # Independent small oracle: prior 40.00, current 38.00; A -10.00, B +8.00.
    return DatasetInput.model_validate(
        {
            "products": [
                {"sku": sku, "name": sku, "category": "日用", "active_from": "2026-09-01"}
                for sku in ["A", "B"]
            ],
            "daily_sales": [
                {
                    "sku": sku,
                    "day": f"2026-09-0{day}",
                    "paid_units": units,
                    "refunded_units": refund // 100,
                    "revenue_cents": units * 100,
                    "refund_cents": refund,
                }
                for sku in ["A", "B"]
                for day in range(1, 5)
                for units, refund in [
                    (
                        (10 if day <= 2 else 5) if sku == "A" else (10 if day <= 2 else 15),
                        100 if sku == "B" and day > 2 else 0,
                    )
                ]
            ],
        }
    )


def calc(payload, start="2026-09-03", end="2026-09-04"):
    return calculate_review(
        canonical(payload),
        date.fromisoformat(start),
        date.fromisoformat(end),
        dataset_id="test-version",
        content_hash="test-hash",
        sample=True,
    )


def commit(repo, user, payload=None):
    payload = payload or hand_payload()
    info = stage_dataset(
        repo, user, payload, "经营数据", True, hashlib.sha256(csv_bytes(payload)).hexdigest()
    )
    return commit_dataset(repo, user, info["id"])


def request(repo, user, dataset_id, **kwargs):
    return TaskRequest(
        conversation_id=repo.create_conversation(user.id),
        query="复盘经营表现与退款",
        sources=["sales"],
        strategy="workflow",
        profile="commerce_review",
        dataset_id=dataset_id,
        start_date=date(2026, 9, 3),
        end_date=date(2026, 9, 4),
        **kwargs,
    )


def test_independent_metric_oracle_and_dimension_reconciliation():
    result = calc(hand_payload())
    assert result["metrics"]["net_revenue"] == "38.00"
    assert result["metrics"]["net_revenue_delta"] == "-2.00"
    assert result["metrics"]["net_revenue_change_pct"] == "-5.00"
    assert result["metrics"]["refund_rate_pct"] == "5.00"
    assert result["previous_period"]["net_revenue"] == "40.00"
    assert {e["entity"]: e["delta_cents"] for e in result["dimensions"]["products"]} == {
        "A": -1000,
        "B": 800,
    }
    for entries in result["dimensions"].values():
        assert sum(e["delta_cents"] for e in entries) == -200
    assert {f["code"] for f in result["findings"]} == {"net_decline", "refund_increase"}


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_scenarios_are_reproducible_and_reports_validate(scenario):
    payload = scenario_payload(scenario)
    assert canonical(payload) == canonical(scenario_payload(scenario))
    assert canonical(parse_csv(csv_bytes(payload))) == canonical(payload)
    result = calc(payload, "2026-09-01", "2026-09-30")
    import json
    from pathlib import Path

    expected = json.loads(Path("evaluations/commerce_review_cases.json").read_text())["cases"][
        scenario
    ]
    assert result["metrics"]["net_revenue"] == expected["net_revenue"]
    assert result["previous_period"]["comparable"] == expected["comparable"]
    evidence = [{"id": "E-review", "kind": "metrics", "payload": result}]
    report = deterministic_report(evidence, [])
    assert [s.name for s in report.sections] == REVIEW_SECTIONS
    assert validate_report(report, evidence)["passed"]
    if scenario in {"missing", "no_prior"}:
        assert "net_revenue_delta" not in result["metrics"]
        assert not report.sections[1].claims
    if scenario == "new_product":
        assert result["coverage"]["expected_records"] == 111
        assert result["previous_period"]["comparable"]
    if scenario == "zero":
        assert result["metrics"]["net_revenue"] == "0.00"
        assert "refund_rate_pct" not in result["metrics"]
        assert "net_revenue_change_pct" not in result["metrics"]
    if scenario == "cross_refund":
        assert any("跨期" in note for note in result["limitations"])


def test_missing_row_is_not_zero_and_disables_attribution():
    payload = hand_payload()
    payload.daily_sales.pop()
    result = calc(payload)
    assert result["coverage"]["missing_records"] == 1
    assert "net_revenue_delta" not in result["metrics"]
    assert all(e["delta_cents"] is None for e in result["dimensions"]["products"])


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "negative",
        "decimal",
        "metadata",
        "early",
        "extra_column",
        "encoding",
        "oversize",
    ],
)
def test_csv_rejects_bad_rows_without_echoing_uploaded_secrets(mutation):
    text = csv_bytes(hand_payload()).decode("utf-8-sig")
    lines = text.splitlines()
    row = lines[1].split(",")
    if mutation == "duplicate":
        lines.append(lines[1])
    elif mutation in {"negative", "decimal"}:
        row[5] = "-1" if mutation == "negative" else "1.5"
        lines[1] = ",".join(row)
    elif mutation == "metadata":
        row[1] = "secret-must-not-echo"
        lines[1] = ",".join(row)
    elif mutation == "early":
        row[4] = "2026-08-31"
        lines[1] = ",".join(row)
    elif mutation == "extra_column":
        lines[0] += ",unsupported"
    content = "\n".join(lines).encode()
    if mutation == "encoding":
        content = b"\xff\xff"
    elif mutation == "oversize":
        content = b"x" * (2 * 1024 * 1024 + 1)
    with pytest.raises(ValueError) as error:
        parse_csv(content)
    assert "secret-must-not-echo" not in str(error.value)
    if mutation in {"negative", "decimal", "metadata", "early", "duplicate"}:
        assert "行" in str(error.value)


def test_preview_commit_idempotency_owner_isolation_and_csrf(client, setup):
    _, repo, user, other = setup
    authenticate(client)
    first = client.post(
        "/api/commerce/datasets/preview",
        files={"file": ("sales.csv", csv_bytes(hand_payload()))},
        data={"sample": "true"},
    )
    assert first.status_code == 200
    info = first.json()
    assert not info["committed"] and info["quality"]["metrics"]["net_revenue"] == "78.00"
    task_request = request(repo, user, info["id"])
    with pytest.raises(NotFound):
        repo.create_task(user, task_request, str(uuid4()))
    assert client.post(f"/api/commerce/datasets/{info['id']}/commit").status_code == 200
    assert client.post(f"/api/commerce/datasets/{info['id']}/commit").json()["id"] == info["id"]
    duplicate = client.post(
        "/api/commerce/datasets/preview",
        files={"file": ("again.csv", csv_bytes(hand_payload()))},
        data={"sample": "true"},
    )
    assert duplicate.json()["id"] == info["id"]
    assert len(list_datasets(repo, user)) == 1
    with pytest.raises(NotFound):
        dataset_review(
            repo, other.id, other.tenant_id, info["id"], date(2026, 9, 3), date(2026, 9, 4)
        )
    authenticate(client, "other@example.com", "other-commerce-2026")
    assert client.get(f"/api/commerce/datasets/{info['id']}").status_code == 404
    assert client.post(f"/api/commerce/datasets/{info['id']}/commit").status_code == 404
    assert (
        client.get(
            f"/api/commerce/datasets/{info['id']}/quality?start=2026-09-03&end=2026-09-04"
        ).status_code
        == 404
    )
    assert client.get("/api/commerce/datasets").json() == {"datasets": []}
    client.headers.pop("X-CSRF-Token")
    assert client.post("/api/commerce/scenarios", json={"scenario": "normal"}).status_code == 403


def test_invalid_import_is_atomic_and_scenarios_always_sample(client, setup):
    _, repo, *_ = setup
    authenticate(client)
    with repo.session() as db:
        before = len(list(db.scalars(select(CommerceDataset))))
    raw = csv_bytes(hand_payload()) + b"bad\n"
    assert (
        client.post("/api/commerce/datasets/preview", files={"file": ("bad.csv", raw)}).status_code
        == 422
    )
    with repo.session() as db:
        assert len(list(db.scalars(select(CommerceDataset)))) == before
    result = client.post("/api/commerce/scenarios", json={"scenario": "normal"}).json()
    assert result["sample"] is True
    assert (
        client.post(
            "/api/commerce/scenarios", json={"scenario": "normal", "sample": False}
        ).status_code
        == 422
    )


def test_profile_rejects_external_sources_and_unbound_dataset(setup):
    _, repo, user, _ = setup
    for values in [{"sources": ["sales", "web"]}, {"strategy": "single"}, {"dataset_id": None}]:
        params = dict(
            conversation_id=repo.create_conversation(user.id),
            query="经营复盘",
            profile="commerce_review",
            dataset_id=str(uuid4()),
            sources=["sales"],
            strategy="workflow",
        )
        params.update(values)
        with pytest.raises(ValidationError):
            TaskRequest(**params)


async def test_worker_review_resume_keeps_version_and_exports_backup(setup, tmp_path):
    settings, repo, user, _ = setup
    info = commit(repo, user)
    task = repo.create_task(
        user, request(repo, user, info["id"], require_review=True), str(uuid4())
    )
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    assert repo.get_task(task["id"])["status"] == "waiting_review"
    # An unrelated live business-table edit and a new import cannot change this task.
    with repo.session() as db:
        db.scalar(select(DailySale)).revenue_cents += 999999
        db.commit()
    newer = hand_payload()
    newer.daily_sales[-1].revenue_cents += 1000
    assert commit(repo, user, newer)["id"] != info["id"]
    repo.review(task["id"], user.id, True)
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    done = repo.get_task(task["id"])
    assert done["status"] == "degraded" and done["validation"]["passed"]
    evidence = repo.evidence(task["id"])
    assert evidence[0]["payload"]["metrics"]["net_revenue"] == "38.00"
    assert evidence[0]["payload"]["dataset_hash"] == info["content_hash"]
    with repo.session() as db:
        artifact = db.scalar(
            select(Artifact).where(Artifact.task_id == task["id"], Artifact.name == "report.md")
        )
    markdown = (settings.data_dir / "artifacts" / artifact.storage_key).read_text()
    assert info["content_hash"] in markdown and "模拟" in markdown
    backup = tmp_path.parent / ("review-backup-" + uuid4().hex)
    restored_path = tmp_path.parent / ("review-restored-" + uuid4().hex)
    try:
        create_backup(settings, backup, services_stopped=True)
        restored = restore_backup(backup, restored_path)
        restored_repo = Repository(
            settings.model_copy(
                update={
                    "database_url": "sqlite:///" + restored["database"],
                    "data_dir": restored_path / "runtime",
                }
            )
        )
        try:
            assert list_datasets(restored_repo, user)[-1]["content_hash"] == info["content_hash"]
        finally:
            restored_repo.engine.dispose()
    finally:
        import shutil

        shutil.rmtree(backup, ignore_errors=True)
        shutil.rmtree(restored_path, ignore_errors=True)


async def test_review_narrative_cannot_rewrite_facts_and_bad_numbers_fall_back(setup):
    class BadNarrative(ScriptedCommerceModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            names = [t["function"]["name"] for t in kwargs.get("tools", [])]
            if "ReviewNarrative" in names:
                from langchain_core.messages import AIMessage
                from langchain_core.outputs import ChatGeneration, ChatResult

                return ChatResult(
                    generations=[
                        ChatGeneration(
                            message=AIMessage(
                                content="",
                                tool_calls=[
                                    {
                                        "id": "review",
                                        "name": "ReviewNarrative",
                                        "args": {
                                            "analysis": ["利润增长99999元"],
                                            "recommendations": ["建议核对退款归属。"],
                                        },
                                    }
                                ],
                                usage_metadata={
                                    "input_tokens": 10,
                                    "output_tokens": 10,
                                    "total_tokens": 20,
                                },
                            )
                        )
                    ]
                )
            return super()._generate(messages, stop, run_manager, **kwargs)

    settings, repo, user, _ = setup
    settings.agent_mode, settings.llm_model = "live", "fake-commerce"
    settings.openai_api_key = SecretStr("offline-review-protocol")
    info = commit(repo, user)
    task = repo.create_task(user, request(repo, user, info["id"]), str(uuid4()))
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver, BadNarrative()).run_once()
    done = repo.get_task(task["id"])
    assert done["validation"].get("fallback_used") or done["validation"].get("draft_fallback_used")
    assert "99999" not in str(done["report"])
    assert done["report"]["sections"][0]["claims"][0]["metrics"][1]["value"] == "38.00"


def test_report_rejects_deleted_or_changed_deterministic_facts():
    evidence = [{"id": "E-review", "kind": "metrics", "payload": calc(hand_payload())}]
    report = deterministic_report(evidence, [])
    report.sections[0].claims.pop()
    assert not validate_report(report, evidence)["passed"]


def test_max_product_context_keeps_summary_and_rendered_contribution_facts():
    payload = hand_payload().model_dump(mode="json")
    payload["products"] = [
        dict(payload["products"][0], sku=f"P-{i}", name=f"商品{i}", category=f"类{i}")
        for i in range(100)
    ]
    payload["daily_sales"] = [
        dict(row, sku=p["sku"])
        for p in payload["products"]
        for row in payload["daily_sales"]
        if row["sku"] == "A"
    ]
    result = calc(DatasetInput.model_validate(payload))
    evidence = [
        {
            "id": "E-review",
            "kind": "metrics",
            "title": "经营",
            "locator": "commerce://review",
            "content_hash": "hash",
            "retrieved_at": "now",
            "excerpt": "",
            "payload": result,
        }
    ]
    package = evidence_package(evidence)
    assert package["manifest"]["selected_ids"] == ["E-review"]
    retained = package["sources"][0]["payload"]["fact_catalog"]
    for section in deterministic_report(evidence, []).sections:
        for claim in section.claims:
            assert all(metric.key in retained for metric in claim.metrics)


def test_same_tenant_does_not_grant_dataset_access(setup):
    from app.storage.models import User

    _, repo, user, _ = setup
    dataset = commit(repo, user)
    with repo.session() as db:
        colleague = User(
            id=str(uuid4()),
            tenant_id=user.tenant_id,
            email="colleague@example.com",
            password_hash=user.password_hash,
        )
        db.add(colleague)
        db.commit()
    assert list_datasets(repo, colleague) == []
    with pytest.raises(NotFound):
        commit_dataset(repo, colleague, dataset["id"])
    with pytest.raises(NotFound):
        repo.create_task(colleague, request(repo, colleague, dataset["id"]), str(uuid4()))


def test_sqlite_upgrade_preserves_existing_tasks_and_schema_matches(setup, tmp_path):
    import json

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import MetaData, Table

    from app.core.auth import bootstrap_user
    from app.storage.models import User, utcnow

    settings = setup[0].model_copy(
        update={"database_url": "sqlite:///" + str(tmp_path / "upgrade.sqlite")}
    )
    config = Config("alembic.ini")
    config.attributes["settings"] = settings
    command.upgrade(config, "878d61a878e3")
    repo = Repository(settings)
    try:
        bootstrap_user(repo, settings)
        with repo.session() as db:
            user = db.scalar(select(User).where(User.email == "demo@example.com"))
        # Write a real legacy row; the current ORM correctly requires migrated columns.
        legacy_request = TaskRequest(
            conversation_id=repo.create_conversation(user.id), query="旧版经营分析"
        )
        parameters = legacy_request.model_dump(mode="json", exclude={"bundle_id", "parent_task_id"})
        key, task_id = str(uuid4()), str(uuid4())
        legacy_tasks = Table("tasks", MetaData(), autoload_with=repo.engine)
        with repo.engine.begin() as db:
            db.execute(
                legacy_tasks.insert().values(
                    id=task_id,
                    conversation_id=legacy_request.conversation_id,
                    user_id=user.id,
                    tenant_id=user.tenant_id,
                    idempotency_key=key,
                    request_hash=hashlib.sha256(
                        json.dumps(parameters, sort_keys=True).encode()
                    ).hexdigest(),
                    query=legacy_request.query,
                    parameters=parameters,
                    mode="demo",
                    status="queued",
                    created_at=utcnow(),
                    updated_at=utcnow(),
                    attempts=0,
                    runtime_ms=0,
                    tool_calls=0,
                    model_calls=0,
                    input_tokens=0,
                    output_tokens=0,
                    usage_complete=True,
                    usage_reports=0,
                )
            )
        command.upgrade(config, "head")
        command.check(config)
        upgraded = repo.get_task(task_id)
        assert upgraded["query"] == "旧版经营分析" and upgraded["graph_version"] == "research-v1"
        assert repo.create_task(user, legacy_request, key)["id"] == task_id
        assert commit(repo, user)["committed"]
    finally:
        repo.engine.dispose()


@pytest.mark.parametrize(
    "text",
    [
        "退款金额上升与净销售额下降之间的关联仍属待验证假设。",
        "按记账周期计算的退款件数占比处于较高水平。",
        "本期退款件数有所增加。",
    ],
)
def test_manual_failures_are_rejected_even_if_model_reviewer_would_accept(text):
    from app.research.schemas import Claim

    payload = calc(scenario_payload("top_decline"), "2026-09-01", "2026-09-30")
    assert payload["metrics"]["refund_amount_delta"] == "0.00"
    assert payload["metrics"]["refunded_units_delta"] == "0"
    evidence = [{"id": "E-review", "kind": "metrics", "payload": payload}]
    report = deterministic_report(evidence, [])
    report.sections[3].claims = [
        Claim(id="manual-failure", kind="analysis", text=text, evidence_ids=["E-review"])
    ]
    assert not validate_report(report, evidence)["passed"]


async def test_live_review_protocol_keeps_facts_and_uses_only_two_model_calls(setup):
    settings, repo, user, _ = setup
    settings.agent_mode, settings.llm_model = "live", "fake-commerce"
    settings.openai_api_key = SecretStr("offline-review-protocol")
    info = commit(repo, user)
    task = repo.create_task(user, request(repo, user, info["id"]), str(uuid4()))
    model = ScriptedCommerceModel()
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver, model).run_once()
    done = repo.get_task(task["id"])
    assert done["metrics"]["model_calls"] == 2
    assert done["validation"]["semantic_status"] == "model_reviewed"
    assert not done["validation"].get("fallback_used")
    assert model._tool_sets == [["ReviewNarrative"], ["SemanticReview"]]


@pytest.mark.parametrize("scenario", ["missing", "no_prior", "zero"])
async def test_incomplete_or_zero_comparisons_keep_program_explanation(setup, scenario):
    # The scripted model asserts a decline for every fixture. It must not replace
    # the known boundary explanation when a comparison or denominator is unavailable.
    settings, repo, user, _ = setup
    settings.agent_mode, settings.llm_model = "live", "fake-commerce"
    settings.openai_api_key = SecretStr("offline-review-gate")
    dataset = commit(repo, user, scenario_payload(scenario))
    body = request(repo, user, dataset["id"]).model_copy(
        update={"start_date": date(2026, 9, 1), "end_date": date(2026, 9, 30)}
    )
    task = repo.create_task(user, body, str(uuid4()))
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver, ScriptedCommerceModel()).run_once()
    report = repo.get_task(task["id"])["report"]
    assert report["sections"][3]["claims"][0]["id"] == "review-neutral"
    assert all(
        c["id"].startswith(("review-neutral", "review-hypothesis"))
        for c in report["sections"][3]["claims"]
    )


async def test_review_semantics_only_receives_free_recommendations(setup):
    import json

    class ScopeInspectModel(ScriptedCommerceModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            names = [t["function"]["name"] for t in kwargs.get("tools", [])]
            if "SemanticReview" in names:
                context = json.loads(messages[-1].content.split("\n证据：\n", 1)[0])
                assert [s["name"] for s in context["report"]["sections"]] == ["行动建议"]
                assert context["report"]["limitations"] == []
            return super()._generate(messages, stop, run_manager, **kwargs)

    settings, repo, user, _ = setup
    settings.agent_mode, settings.llm_model = "live", "fake-commerce"
    settings.openai_api_key = SecretStr("offline-review-scope")
    info = commit(repo, user)
    task = repo.create_task(user, request(repo, user, info["id"]), str(uuid4()))
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver, ScopeInspectModel()).run_once()
    assert repo.get_task(task["id"])["validation"]["semantic_status"] == "model_reviewed"
