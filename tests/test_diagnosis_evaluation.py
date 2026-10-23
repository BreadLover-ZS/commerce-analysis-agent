"""Independent evaluation gates: tampering, isolation, caps, repeats and review."""

import asyncio
import copy
import json
from pathlib import Path

import httpx
import pytest

from evaluations.diagnosis import RequestBudget, evaluate, live_configuration, parser, summarize
from evaluations.diagnosis_cases import load_cases
from evaluations.diagnosis_grading import (
    ASSERTIONS,
    grade,
    quote_errors,
    stale_stock_is_bounded,
    token_threshold_respected,
)
from evaluations.diagnosis_harness import MODES
from evaluations.diagnosis_review import review_run
from evaluations.diagnosis_runtime import EvaluationSettings


def test_all_corpus_splits_have_executable_assertions_without_running_holdout():
    for split in ("development", "regression", "holdout"):
        for case in load_cases(split=split)[0]:
            assert set(case.expected.required_assertions) <= ASSERTIONS
            assert case.harness.mode in MODES


def test_stale_stock_cannot_publish_current_risk_or_coverage():
    item = {
        "reason": "stale_snapshot",
        "below_lead_proxy": None,
        "coverage_days": None,
        "fact_keys": {"available_units": "inventory.A.available_units"},
    }
    assert stale_stock_is_bounded([{"items": [item]}])
    assert not stale_stock_is_bounded([])
    for key, value in (
        ("below_lead_proxy", False),
        ("below_lead_proxy", True),
        ("coverage_days", "999"),
        ("fact_keys", {"coverage_days": "forged"}),
        ("fact_keys", {"lead_paid_units_proxy": "forged"}),
    ):
        assert not stale_stock_is_bounded([{"items": [{**item, key: value}]}])


def test_token_threshold_allows_final_known_overshoot_but_rejects_another_send():
    calls = [
        {
            "created_at": "01",
            "completed_at": "02",
            "usage_complete": True,
            "usage": {"input_tokens": 5, "output_tokens": 1},
        },
        {
            "created_at": "03",
            "completed_at": "04",
            "usage_complete": True,
            "usage": {"input_tokens": 6, "output_tokens": 1},
        },
    ]
    metrics = {"input_tokens": 11, "output_tokens": 2, "usage_complete": True}
    assert token_threshold_respected(metrics, calls, 10)
    assert not token_threshold_respected(
        metrics,
        [
            *calls,
            {
                **calls[-1],
                "created_at": "05",
                "completed_at": "06",
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        ],
        10,
    )
    assert not token_threshold_respected({**metrics, "input_tokens": 10}, calls, 10)
    assert not token_threshold_respected(
        metrics, [{**calls[0], "usage": {"input_tokens": True, "output_tokens": 1}}, calls[1]], 10
    )


def test_conditional_missing_assertions_do_not_reject_complete_authorized_subset(receipt):
    _, case, bundle, record = receipt
    altered = case.model_copy(
        update={
            "expected": case.expected.model_copy(
                update={
                    "required_assertions": [
                        "missing_is_not_zero",
                        "no_full_attribution_when_incomparable",
                        "injection_does_not_expand_authority",
                    ]
                }
            )
        }
    )
    for group in ("fixed", "agent"):
        source = json.loads((receipt[0] / f"D01-1/{group}.json").read_text())
        result = grade(altered, bundle, source)
        assert result["automatic_passed"], result


@pytest.fixture(scope="module")
def receipt(tmp_path_factory):
    root = tmp_path_factory.mktemp("g6-receipt") / "run"
    args = parser().parse_args(
        ["run", "--split", "development", "--case-id", "D01", "--output", str(root)]
    )
    summary = asyncio.run(evaluate(args))
    assert summary["all_automatic_passed"]
    case = load_cases(split="development", case_ids=["D01"])[0][0]
    bundle = json.loads((root / "D01-1/bundle.json").read_text())
    record = json.loads((root / "D01-1/agent.json").read_text())
    return root, case, bundle, record


def test_cli_runs_real_pair_and_retains_versions_artifacts_and_pending_review(receipt):
    root, _, _, record = receipt
    summary = json.loads((root / "summary.json").read_text())
    assert summary["provider_requests"] == 0 and summary["release_eligible"] is False
    assert summary["business_quality_rate"] is None
    assert summary["common_paired_contracts"][0]["same_bundle"]
    assert summary["common_paired_contracts"][0]["same_budget"]
    assert record["grading"]["semantic_review"] == "PENDING"
    assert len(list((root / "D01-1/agent-artifacts").iterdir())) == 5
    assert len(list((root / "D01-1/fixed-artifacts").iterdir())) == 5


@pytest.mark.parametrize(
    "tamper",
    [
        "report_fact",
        "empty_facts",
        "query_result",
        "query_digest",
        "unit",
        "period",
        "budget",
        "scope",
        "unsupported",
    ],
)
def test_independent_grader_rejects_tampering_even_when_program_validation_says_pass(
    receipt, tamper
):
    _, case, bundle, original = receipt
    record = copy.deepcopy(original)
    assert record["task"]["validation"]["passed"]
    if tamper == "report_fact":
        record["task"]["report"]["facts"][0]["value"] = "9999999.00"
    elif tamper == "empty_facts":
        record["task"]["report"]["facts"] = []
    elif tamper == "query_result":
        record["queries"][0]["result"]["coverage"]["current"]["missing_records"] = 8
    elif tamper == "query_digest":
        record["queries"][0]["result_digest"] = "0" * 64
    elif tamper == "unit":
        record["task"]["report"]["facts"][0]["unit"] = "count"
    elif tamper == "period":
        record["task"]["report"]["facts"][0]["period"]["start"] = "2026-01-01"
    elif tamper == "budget":
        record["task"]["metrics"]["model_calls"] = 13
    elif tamper == "scope":
        record["task"]["report"]["scope"]["skus"] = ["OTHER"]
    else:
        record["evidence"][0]["payload"]["fact_catalog"]["profit"] = {"value": "3"}
    assert not grade(case, bundle, record)["automatic_passed"]


def test_unknown_assertion_fails_closed(receipt):
    _, case, bundle, record = receipt
    altered = case.model_copy(
        update={
            "expected": case.expected.model_copy(update={"required_assertions": ["unimplemented"]})
        }
    )
    result = grade(altered, bundle, record)
    assert not result["automatic_passed"] and "unknown_assertion:unimplemented" in result["errors"]


def test_eval_settings_ignore_all_ambient_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-must-not-be-read")
    monkeypatch.setenv("AGENT_MODE", "live")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://business.invalid/business")
    monkeypatch.setenv("KNOWLEDGE_PROVIDER", "ragflow")
    monkeypatch.setenv("MAX_MODEL_CALLS", "99")
    settings = EvaluationSettings(_env_file=None, data_dir=tmp_path)
    assert settings.agent_mode == "demo" and not settings.openai_api_key.get_secret_value()
    assert settings.database_url.startswith("sqlite")
    assert settings.knowledge_provider == "local" and settings.max_model_calls == 12


def test_live_requires_explicit_provider_budget_key_and_payment_acknowledgment(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-unselected")
    args = parser().parse_args(
        ["run", "--split", "development", "--mode", "live", "--output", str(tmp_path / "r")]
    )
    with pytest.raises(ValueError, match="live须"):
        live_configuration(args)
    args.allow_paid, args.provider, args.model = True, "specified-provider", "specified-model"
    args.endpoint, args.api_key_env, args.max_paid_requests = (
        "https://provider.invalid/v1",
        "EXPLICIT_EVAL_KEY",
        3,
    )
    with pytest.raises(ValueError, match="环境变量为空"):
        live_configuration(args)
    monkeypatch.setenv("EXPLICIT_EVAL_KEY", "explicit-key")
    assert live_configuration(args)["openai_api_key"] == "explicit-key"
    args.endpoint = "https://key:secret@provider.invalid/v1"
    with pytest.raises(ValueError, match="HTTPS"):
        live_configuration(args)


async def test_request_budget_blocks_before_network_and_after_unknown():
    budget, reached = RequestBudget(1), []

    async def transport(request):
        reached.append(request.url.path)
        return httpx.Response(200, json={"choices": [], "internal-secret": "must-not-retain"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport),
        event_hooks={"request": [budget.before_send], "response": [budget.after_response]},
    ) as client:
        await client.post(
            "https://provider.invalid/v1/chat/completions",
            json={"model": "selected-model", "messages": [], "private": "must-not-retain"},
            headers={"Authorization": "Bearer must-not-retain"},
        )
        with pytest.raises(RuntimeError, match="exhausted"):
            await client.post(
                "https://provider.invalid/v1/chat/completions", json={"model": "selected-model"}
            )
        assert reached == ["/v1/chat/completions"] and budget.sent == 1
        budget.limit, budget.blocked = 100, True
        with pytest.raises(RuntimeError, match="exhausted"):
            await client.post(
                "https://provider.invalid/v1/chat/completions", json={"model": "selected-model"}
            )
    assert len(reached) == 1
    assert "must-not-retain" not in json.dumps(budget.traces)
    assert budget.traces[0]["request"]["model"] == "selected-model"


def test_pending_or_self_review_cannot_release(receipt, tmp_path):
    root = receipt[0]
    template = json.loads((root / "review-template.json").read_text())
    path = tmp_path / "reviews.json"
    path.write_text(json.dumps(template))
    with pytest.raises(ValueError):
        review_run(root, path)
    template["reviewer"] = "Task model itself"
    template["reviewer_kind"] = "task_model_self_review"
    path.write_text(json.dumps(template))
    with pytest.raises(ValueError):
        review_run(root, path)


def completed_reviews(root):
    template = json.loads((root / "review-template.json").read_text())
    template["reviewer"] = "Synthetic test reviewer; not a real business review"
    for record in template["records"]:
        record["verdict"] = "PASS"
        for check in record["checks"].values():
            check.update(
                passed=True,
                reason="Synthetic importer contract check only",
                evidence_refs=["task/report", "grading"],
            )
    return template


async def test_total_token_budget_settles_usage_and_denies_before_transport(tmp_path):
    journal = tmp_path / "budget.jsonl"
    budget = RequestBudget(99, max_total_tokens=25, context_ceiling=10, journal=journal)
    totals, reached = iter([4, 6, 7]), []

    async def transport(request):
        reached.append(request)
        total = next(totals)
        return httpx.Response(
            200,
            json={
                "usage": {"prompt_tokens": total - 1, "completion_tokens": 1, "total_tokens": total}
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport),
        event_hooks={"request": [budget.before_send], "response": [budget.after_response]},
    ) as client:
        for known in (4, 10, 17):
            await client.post("https://provider.invalid/chat/completions", json={"max_tokens": 5})
            assert budget.spent_tokens == known and budget.reserved_tokens == 0
        with pytest.raises(RuntimeError, match="token_budget_capacity"):
            await client.post("https://provider.invalid/chat/completions", json={"max_tokens": 5})
    assert len(reached) == budget.sent == 3
    events = [json.loads(line) for line in journal.read_text().splitlines()]
    assert all(event["spent_tokens"] + event["reserved_tokens"] <= 25 for event in events)
    assert events[-1]["event"] == "denied_before_send"
    assert journal.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"prompt_tokens": True, "completion_tokens": 1, "total_tokens": 2},
        {"prompt_tokens": -1, "completion_tokens": 2, "total_tokens": 1},
        {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 4},
        {"prompt_tokens": 15, "completion_tokens": 1, "total_tokens": 16},
    ],
)
async def test_unknown_usage_keeps_entire_reservation_and_stops_paid_calls(tmp_path, usage):
    budget = RequestBudget(
        99, max_total_tokens=25, context_ceiling=10, journal=tmp_path / "budget.jsonl"
    )
    reached = []

    async def transport(request):
        reached.append(request)
        return httpx.Response(200, json={"usage": usage})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport),
        event_hooks={"request": [budget.before_send], "response": [budget.after_response]},
    ) as client:
        await client.post("https://provider.invalid/chat/completions", json={"max_tokens": 5})
        with pytest.raises(RuntimeError, match="exhausted"):
            await client.post("https://provider.invalid/chat/completions", json={"max_tokens": 5})
    assert len(reached) == 1 and budget.blocked
    assert budget.spent_tokens == 0 and budget.reserved_tokens == 15


async def test_first_reservation_too_large_sends_no_request():
    budget = RequestBudget(99, max_total_tokens=14, context_ceiling=10)

    async def transport(request):
        pytest.fail("The request must be denied before network transport")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), event_hooks={"request": [budget.before_send]}
    ) as client:
        with pytest.raises(RuntimeError, match="token_budget_capacity"):
            await client.post("https://provider.invalid/chat/completions", json={"max_tokens": 5})
    assert budget.sent == budget.reserved_tokens == 0


async def test_token_only_authorization_and_unknown_model_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPLICIT_EVAL_KEY", "fake-test-key")
    args = parser().parse_args(
        [
            "run",
            "--split",
            "development",
            "--mode",
            "live",
            "--allow-paid",
            "--provider",
            "test",
            "--endpoint",
            "https://provider.invalid",
            "--model",
            "unknown-profile",
            "--api-key-env",
            "EXPLICIT_EVAL_KEY",
            "--max-total-tokens",
            "3000000",
            "--output",
            str(tmp_path / "run"),
        ]
    )
    assert live_configuration(args)["openai_api_key"] == "fake-test-key"
    with pytest.raises(ValueError, match="已核验上下文"):
        await evaluate(args)
    assert not args.output.exists()


def test_partial_stopped_run_reviews_only_retained_receipts(receipt, tmp_path):
    import shutil

    from evaluations.diagnosis_grading import sha

    root = tmp_path / "partial"
    shutil.copytree(receipt[0], root)
    meta = json.loads((root / "run.json").read_text())
    manifest = json.loads((root / "expectations-manifest.json").read_text())
    manifest.append({"case_id": "D02", "expected": manifest[0]["expected"]})
    meta["planned"]["case_ids"].append("D02")
    meta["expected_sha256"] = sha([item["expected"] for item in manifest])
    (root / "run.json").write_text(json.dumps(meta))
    (root / "expectations-manifest.json").write_text(json.dumps(manifest))
    summary = json.loads((root / "summary.json").read_text())
    summary["stopped"] = "unknown_usage_stop_all_paid_requests"
    (root / "summary.json").write_text(json.dumps(summary))
    reviews = tmp_path / "reviews.json"
    reviews.write_text(json.dumps(completed_reviews(root)))
    result = review_run(root, reviews)
    assert result["review_completed"] and not result["release_eligible"]
    assert result["independent_cases"] == 2


def test_independent_review_import_is_hash_bound_and_demo_never_releases(receipt, tmp_path):
    root = receipt[0]
    data = completed_reviews(root)
    path = tmp_path / "reviews.json"
    path.write_text(json.dumps(data))
    result = review_run(root, path)
    assert result["review_completed"] and result["release_eligible"] is False
    data["records"][0]["receipt_sha256"] = "0" * 64
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="哈希"):
        review_run(root, path)


def test_offline_grading_audit_keeps_original_receipts_and_is_bound_to_review(receipt, tmp_path):
    import hashlib

    from evaluations.diagnosis_audit import audit_run

    root = receipt[0]
    before = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()
    }
    result = audit_run(root)
    assert result["new_model_requests"] == 0 and not result["raw_receipts_modified"]
    assert result["corrected_automatic_summary"]["all_automatic_passed"]
    assert before == {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()
    }
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(result))
    review_path = tmp_path / "review.json"
    review_path.write_text(json.dumps(completed_reviews(root)))
    imported = review_run(root, review_path, audit_path)
    assert imported["review_completed"] and not imported["release_eligible"]
    result["records"][0]["current_grading"]["automatic_passed"] = False
    audit_path.write_text(json.dumps(result))
    with pytest.raises(ValueError, match="审计"):
        review_run(root, review_path, audit_path)


def test_review_template_cannot_drop_baseline_even_when_review_file_matches(receipt, tmp_path):
    import shutil

    root = tmp_path / "edited-template"
    shutil.copytree(receipt[0], root)
    template_path = root / "review-template.json"
    template = json.loads(template_path.read_text())
    template["records"] = [r for r in template["records"] if r["receipt"].endswith("/agent.json")]
    template_path.write_text(json.dumps(template))
    path = tmp_path / "reviews.json"
    path.write_text(json.dumps(completed_reviews(root)))
    with pytest.raises(ValueError, match="全部运行"):
        review_run(root, path)


@pytest.mark.parametrize("alter", ["missing", "duplicate", "bad_ref", "contradiction"])
def test_review_does_not_drop_repeats_or_accept_unbound_criteria(receipt, tmp_path, alter):
    root = receipt[0]
    data = completed_reviews(root)
    if alter == "missing":
        data["records"].pop()
    elif alter == "duplicate":
        data["records"][1] = data["records"][0]
    elif alter == "bad_ref":
        data["records"][0]["checks"]["all_facts_bound"]["evidence_refs"] = [
            "task/private-missing-field"
        ]
    else:
        data["records"][0]["checks"]["all_facts_bound"]["passed"] = False
    path = tmp_path / "reviews.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        review_run(root, path)


def test_repeat_statistics_include_failures_and_do_not_inflate_independent_cases(receipt):
    root = receipt[0]
    row = json.loads((root / "D01-1/agent.json").read_text())["measurement"]
    rows = [row, dict(row, repeat=2, automatic_passed=False), dict(row, repeat=3)]
    result = summarize(rows, {"repeats": 3})["groups"]["agent"]
    assert result["independent_cases"] == 1 and result["runs"] == 3
    assert result["automatic_contract_rate"] == 2 / 3
    assert result["all_planned_repeats_correct_case_rate"] == 0
    assert result["total_estimated_cost"] is None


async def test_v3_single_sku_equivalence_requires_complete_bound_decomposition():
    from evaluations.diagnosis_grading import single_sku_decomposition
    from evaluations.diagnosis_runtime import admit, collect, execute, new_task, sandbox

    case = load_cases(split="development", case_ids=["D12"])[0][0]
    with sandbox() as (settings, repo, user, other):
        conversation, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conversation, bundle, case.query, "additional")
        await execute(repo, settings)
        record = collect(repo, task["id"], "additional")
    business = case.model_dump(mode="json")
    sources = [e["payload"] for e in record["evidence"]]
    report = record["task"]["report"]
    assert "rank_contributors" not in {p["tool"] for p in sources}
    assert single_sku_decomposition(business, sources, report)
    assert grade(case, bundle, record)["automatic_passed"]
    # No baseline-only shortcut, no multi-SKU proxy, no altered identity/facts,
    # and no reclassification of the old v1/v2 contract's historical grades.
    assert not single_sku_decomposition(
        business, [p for p in sources if p["tool"] != "analyze_refunds"], report
    )
    assert not single_sku_decomposition(
        business, sources, {**report, "scope": {"skus": ["A", "B"]}}
    )
    changed = copy.deepcopy(record)
    refund = next(
        e["payload"] for e in changed["evidence"] if e["payload"]["tool"] == "analyze_refunds"
    )
    refund["net_change_identity"]["net_delta_cents"] += 1
    assert not grade(case, bundle, changed)["automatic_passed"]
    changed = copy.deepcopy(report)
    changed["facts"] = [f for f in report["facts"] if f["metric_key"] != "net_revenue.delta"]
    assert not single_sku_decomposition(business, sources, changed)
    changed = copy.deepcopy(business)
    changed["sales"]["daily_sales"].pop(0)
    assert not single_sku_decomposition(changed, sources, report)
    changed = copy.deepcopy(record)
    changed["task"]["graph_version"] = "diagnosis-v2"
    assert not grade(case, bundle, changed)["assertions"]["required_capabilities"]


def test_literal_quote_oracle_rejects_offsets_and_version_substitution():
    import hashlib

    text = "覆盖天数低于七天时人工核对。"
    hashed = hashlib.sha256(text.encode()).hexdigest()
    chunk = dict(
        document_id="doc",
        chunk_id="chunk",
        document_name="规则甲",
        document_hash=hashed,
        chunk_hash=hashed,
        locator="正文",
        text=text,
    )
    quote = {k: v for k, v in chunk.items() if k != "text"} | dict(
        quote=text,
        quote_hash=hashed,
        quote_start=0,
        quote_end=len(text),
        interpretation_status="unverified",
    )
    assert not quote_errors([quote], [chunk])
    assert quote_errors([dict(quote, quote_end=len(text) - 1)], [chunk])
    assert quote_errors([dict(quote, document_hash="0" * 64)], [chunk])


async def test_holdout_cannot_be_used_as_demo_tuning_data(tmp_path):
    args = parser().parse_args(["run", "--split", "holdout", "--output", str(tmp_path / "holdout")])
    with pytest.raises(ValueError, match="保留集仅允许"):
        await evaluate(args)
    assert not (tmp_path / "holdout").exists()


async def test_custom_corpus_is_frozen_and_audited_without_substituting_default(tmp_path):
    from evaluations.diagnosis_audit import audit_run

    case = load_cases(split="development", case_ids=["D01"])[0][0]
    raw = {
        "schema_version": 1,
        "oracle": "independent_raw_rows_v1",
        "splits": {"development": 1, "regression": 0, "holdout": 0},
        "cases": [case.model_dump(mode="json")],
    }
    custom = tmp_path / "custom.json"
    custom.write_text(json.dumps(raw, ensure_ascii=False))
    output = tmp_path / "run"
    result = await evaluate(
        parser().parse_args(
            ["run", "--split", "development", "--cases", str(custom), "--output", str(output)]
        )
    )
    assert result["all_automatic_passed"] and result["provider_requests"] == 0
    assert (output / "cases-source.json").read_bytes() == custom.read_bytes()
    audited = audit_run(output)
    assert audited["new_model_requests"] == 0
    assert audited["corrected_automatic_summary"]["all_automatic_passed"]
    (output / "cases-source.json").write_bytes((output / "cases-source.json").read_bytes() + b" ")
    with pytest.raises(ValueError, match="问题集"):
        audit_run(output)


async def test_fixture_failure_is_retained_instead_of_aborting_and_dropping_run(tmp_path):
    cases_path = Path(__file__).resolve().parents[1] / "evaluations/diagnosis_cases.json"
    raw = json.loads(cases_path.read_text())
    case = next(c for c in raw["cases"] if c["case_id"] == "R17")
    case["inventory"]["snapshot_day"] = "invalid"
    damaged = tmp_path / "invalid-cases.json"
    damaged.write_text(json.dumps(raw))
    root = tmp_path / "receipt"
    args = parser().parse_args(
        [
            "run",
            "--split",
            "regression",
            "--case-id",
            "R17",
            "--cases",
            str(damaged),
            "--output",
            str(root),
        ]
    )
    summary = await evaluate(args)
    assert summary["all_automatic_passed"] is False and summary["executed_runs"] == 1
    record = json.loads((root / "R17-1/additional.json").read_text())
    assert "fixture_preparation:ValidationError" in record["grading"]["errors"]
    assert record["measurement"]["provider_requests"] == 0
    assert (root / "progress.jsonl").exists()


@pytest.mark.parametrize("case_id", ["R17", "R18", "R19", "R20"])
async def test_extended_case_grading_distinguishes_registry_and_algorithm_versions(
    tmp_path, case_id
):
    root = tmp_path / "receipt"
    args = parser().parse_args(
        ["run", "--split", "regression", "--case-id", case_id, "--output", str(root)]
    )
    result = await evaluate(args)
    record = json.loads((root / f"{case_id}-1/additional.json").read_text())
    assert result["all_automatic_passed"], record["grading"]
    assert record["task"]["tool_registry_version"] == "5"
    assert all(q["query_spec"]["tool_version"] == "5" for q in record["queries"])
    assert all(
        f["tool_version"] == "1"
        for e in record["evidence"]
        for f in e["payload"]["fact_catalog"].values()
    )
    assert result["provider_requests"] == 0
    if case_id in {"R19", "R20"}:
        case = load_cases(split="regression", case_ids=[case_id])[0][0]
        bundle = json.loads((root / f"{case_id}-1/bundle.json").read_text())
        forged = copy.deepcopy(record)
        forged["task"]["report"]["rule_interpretations"] = [
            {
                "status": "unverified",
                "text": "未核验解读",
                "references": [
                    {"evidence_id": "foreign", "chunk_id": "foreign", "quote_hash": "0" * 64}
                ],
            }
        ]
        assert not grade(case, bundle, forged)["automatic_passed"]


@pytest.mark.parametrize("split,case_id", [("development", "D17"), ("regression", "R14")])
async def test_optional_display_assertions_do_not_break_admission_or_fault_grading(
    tmp_path, split, case_id
):
    root = tmp_path / "receipt"
    result = await evaluate(
        parser().parse_args(["run", "--split", split, "--case-id", case_id, "--output", str(root)])
    )
    record = json.loads((root / f"{case_id}-1/gate.json").read_text())
    assert result["all_automatic_passed"], record["grading"]
    assert "evaluation_completed" not in record["grading"]["assertions"]
    assert not any(e.startswith("evaluation_error") for e in record["grading"]["errors"])
