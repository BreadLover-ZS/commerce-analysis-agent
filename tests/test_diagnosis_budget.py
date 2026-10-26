"""Accounting reconciliation and spec-aligned stopping, with no paid requests."""

import json

import httpx
import pytest

from evaluations.diagnosis import RequestBudget
from evaluations.diagnosis_budget import (
    claim_prior_budget,
    critical_failure,
    prior_spend,
    resume_cases,
)


@pytest.fixture
def previous(tmp_path):
    root = tmp_path / "previous"
    root.mkdir()
    meta = {
        "run_id": "prior",
        "planned": {"mode": "live"},
        "provider": "test",
        "endpoint": "https://provider.invalid",
        "model": "chosen",
        "max_total_tokens": 25,
    }
    usage = {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}
    metrics = {"usage_complete": True, "input_tokens": 3, "output_tokens": 1}
    row = {"receipt": "case-1/agent.json", "metrics": metrics, "provider_requests": 1}
    record = {
        "measurement": row,
        "task": {"metrics": metrics},
        "provider_trace": [
            {
                "sequence": 1,
                "http_status": 200,
                "response": {"usage": usage},
                "known_tokens": 4,
                "token_reservation": 15,
            }
        ],
    }
    summary = {
        "run_id": "prior",
        "stopped": "old_overstrict_completion_stop",
        "executed_runs": 1,
        "provider_requests": 1,
        "token_budget": {
            "max_total_tokens": 25,
            "known_tokens": 4,
            "unresolved_reserved_tokens": 0,
            "upper_bound_charged_tokens": 4,
        },
    }
    (root / "case-1").mkdir()
    for name, data in {
        "run.json": meta,
        "summary.json": summary,
        "case-1/agent.json": record,
    }.items():
        (root / name).write_text(json.dumps(data))
    (root / "progress.jsonl").write_text(json.dumps(row) + "\n")
    (root / "budget-journal.jsonl").write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {
                    "event": "reserved_before_send",
                    "sequence": 1,
                    "reservation": 15,
                    "spent_tokens": 0,
                    "reserved_tokens": 15,
                },
                {
                    "event": "known_usage",
                    "sequence": 1,
                    "usage_tokens": 4,
                    "spent_tokens": 4,
                    "reserved_tokens": 0,
                },
            ]
        )
        + "\n"
    )
    return root


def reconcile(root):
    return prior_spend(
        root, provider="test", endpoint="https://provider.invalid", model="chosen", token_limit=25
    )


async def test_reconciled_spend_is_charged_before_new_requests_and_claim_is_exclusive(previous):
    history = reconcile(previous)
    assert history["known_tokens"] == 4 and history["provider_requests"] == 1
    claim_prior_budget(history, previous.parent / "new", "new-run")
    with pytest.raises(FileExistsError):
        claim_prior_budget(history, previous.parent / "another", "another-run")
    budget = RequestBudget(
        99, max_total_tokens=18, context_ceiling=10, initial_spent_tokens=history["known_tokens"]
    )

    async def transport(request):
        pytest.fail("Historical spend must not be reset to permit this request")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), event_hooks={"request": [budget.before_send]}
    ) as client:
        with pytest.raises(RuntimeError, match="token_budget_capacity"):
            await client.post("https://provider.invalid/chat/completions", json={"max_tokens": 5})
    assert budget.sent == 0 and budget.spent_tokens == 4


@pytest.mark.parametrize(
    "tamper",
    [
        "unknown",
        "http_usage",
        "ledger",
        "journal",
        "summary",
        "provider",
        "cap",
        "chain",
        "duplicate",
        "path",
        "incomplete",
    ],
)
def test_prior_charge_tampering_cannot_create_free_budget(previous, tamper):
    def change(name, fn):
        p = previous / name
        data = json.loads(p.read_text())
        fn(data)
        p.write_text(json.dumps(data))

    if tamper == "unknown":
        change("case-1/agent.json", lambda r: r["task"]["metrics"].update(usage_complete=False))
    elif tamper == "http_usage":
        change(
            "case-1/agent.json",
            lambda r: r["provider_trace"][0]["response"]["usage"].update(total_tokens=3),
        )
    elif tamper == "ledger":
        change("case-1/agent.json", lambda r: r["task"]["metrics"].update(input_tokens=2))
    elif tamper == "journal":
        p = previous / "budget-journal.jsonl"
        p.write_text(p.read_text().replace('"usage_tokens": 4', '"usage_tokens": 3'))
    elif tamper == "summary":
        change("summary.json", lambda r: r["token_budget"].update(known_tokens=0))
    elif tamper == "provider":
        change("run.json", lambda r: r.update(model="another"))
    elif tamper == "cap":
        change("run.json", lambda r: r.update(max_total_tokens=100))
    elif tamper == "chain":
        change("run.json", lambda r: r.update(prior_paid_budget={"prior_run": {"run_id": "older"}}))
    elif tamper == "duplicate":
        p = previous / "progress.jsonl"
        p.write_text(p.read_text() * 2)
    elif tamper == "path":
        change("progress.jsonl", lambda r: r.update(receipt="../outside.json"))
    else:
        p = previous / "budget-journal.jsonl"
        p.write_text(p.read_text().splitlines()[0] + "\n")
    with pytest.raises(ValueError):
        reconcile(previous)


def test_completion_failure_is_retained_but_critical_output_failure_stops():
    checks = {
        "immutable_source_versions": True,
        "facts_match_raw_records": True,
        "bounded_execution": True,
        "no_unsupported_report_fact": True,
        "expected_outcome": False,
        "required_capabilities": False,
    }
    grading = {"automatic_passed": False, "assertions": checks}
    assert not critical_failure(grading)
    assert not critical_failure(grading, critical_case=True)
    for key in (
        "immutable_source_versions",
        "facts_match_raw_records",
        "bounded_execution",
        "no_unsupported_report_fact",
    ):
        assert critical_failure({**grading, "assertions": {**checks, key: False}})
    assert critical_failure({"automatic_passed": False, "assertions": {}})


def test_multiple_stopped_segments_preserve_all_usage_and_detect_rewritten_ancestry(
    previous, tmp_path
):
    import shutil

    base = reconcile(previous)
    child = tmp_path / "child"
    shutil.copytree(previous, child)
    meta = json.loads((child / "run.json").read_text())
    meta.update(
        run_id="child", prior_paid_budget={k: v for k, v in base.items() if not k.startswith("_")}
    )
    (child / "run.json").write_text(json.dumps(meta))
    summary = json.loads((child / "summary.json").read_text())
    summary["run_id"] = "child"
    (child / "summary.json").write_text(json.dumps(summary))
    (child / "budget-journal.jsonl").write_text(
        json.dumps(
            {
                "event": "carried_prior_usage",
                "spent_tokens": 4,
                "reserved_tokens": 0,
                "provider_requests": 1,
                "prior_run_id": "prior",
            }
        )
        + "\n"
    )
    assert reconcile(child)["known_tokens"] == 4
    p = previous / "case-1/agent.json"
    p.write_text(p.read_text() + " ")
    with pytest.raises(ValueError, match="预算链"):
        reconcile(child)


async def test_new_experiment_carries_only_charges_then_supports_exact_resume(previous, tmp_path):
    # A completed old plan can fund a changed implementation, but none of its
    # Tasks/FAILs are counted as evidence for the new experiment.
    summary_path = previous / "summary.json"
    old_summary = json.loads(summary_path.read_text())
    old_summary["stopped"] = None
    summary_path.write_text(json.dumps(old_summary))
    history = prior_spend(
        previous,
        provider="test",
        endpoint="https://provider.invalid",
        model="chosen",
        token_limit=25,
        require_stopped=False,
    )
    child = tmp_path / "new-experiment"
    child.mkdir()
    budget = RequestBudget(
        5,
        max_total_tokens=25,
        context_ceiling=10,
        initial_spent_tokens=4,
        journal=child / "budget-journal.jsonl",
    )
    budget.sent, budget.traces = history["provider_requests"], list(history["_traces"])
    budget.event("carried_prior_usage", provider_requests=1, prior_run_id="prior")

    async def transport(request):
        return httpx.Response(
            200, json={"usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}}
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport),
        event_hooks={"request": [budget.before_send], "response": [budget.after_response]},
    ) as client:
        await client.post(
            "https://provider.invalid/chat/completions", json={"model": "chosen", "max_tokens": 5}
        )
    metrics = {"usage_complete": True, "input_tokens": 2, "output_tokens": 1}
    row = {"receipt": "new-case/agent.json", "metrics": metrics, "provider_requests": 1}
    record = {"measurement": row, "task": {"metrics": metrics}, "provider_trace": budget.traces[1:]}
    (child / "new-case").mkdir()
    meta = {
        **history["_meta"],
        "run_id": "new",
        "planned": {"mode": "live", "case_ids": ["D10"]},
        "source_manifest": {"app/diagnosis/planner_v3.py": "new-version"},
        "budget_carry_mode": "accounting_only",
        "prior_paid_budget": {k: v for k, v in history.items() if not k.startswith("_")},
    }
    summary = {
        "run_id": "new",
        "stopped": "planned_local_stop",
        "executed_runs": 1,
        "provider_requests": 2,
        "token_budget": budget.state(),
    }
    for name, value in {
        "run.json": meta,
        "summary.json": summary,
        "new-case/agent.json": record,
    }.items():
        (child / name).write_text(json.dumps(value))
    (child / "progress.jsonl").write_text(json.dumps(row) + "\n")
    reconciled = reconcile(child)
    assert reconciled["known_tokens"] == 7 and reconciled["provider_requests"] == 2
    assert reconciled["_rows"] == [row]
    assert len(reconciled["_traces"]) == 2
    assert not (child / "case-1").exists()
    # An exact resume retains only the new experiment's evidence while keeping
    # the earlier experiment's charges. The accounting prefix cannot vanish.
    import shutil

    resumed = tmp_path / "new-resumed"
    shutil.copytree(child, resumed)
    meta.update(
        run_id="resumed",
        budget_carry_mode="resume",
        prior_paid_budget={k: v for k, v in reconciled.items() if not k.startswith("_")},
    )
    summary["run_id"] = "resumed"
    (resumed / "run.json").write_text(json.dumps(meta))
    (resumed / "summary.json").write_text(json.dumps(summary))
    (resumed / "budget-journal.jsonl").write_text(
        json.dumps(
            {
                "event": "carried_prior_usage",
                "spent_tokens": 7,
                "reserved_tokens": 0,
                "provider_requests": 2,
                "prior_run_id": "new",
            }
        )
        + "\n"
    )
    assert reconcile(resumed)["known_tokens"] == 7
    assert reconcile(resumed)["_rows"] == [row]
    row["provider_requests"] = 0
    (child / "progress.jsonl").write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError):
        reconcile(resumed)


def test_cli_disallows_two_budget_parents_and_paid_carry_in_demo(tmp_path):
    from evaluations.diagnosis import live_configuration, parser

    args = parser().parse_args(
        [
            "run",
            "--split",
            "development",
            "--charge-from",
            str(tmp_path / "paid"),
            "--output",
            str(tmp_path / "new"),
        ]
    )
    with pytest.raises(ValueError, match="demo"):
        live_configuration(args)
    with pytest.raises(SystemExit):
        parser().parse_args(
            [
                "run",
                "--split",
                "development",
                "--charge-from",
                "paid",
                "--resume-from",
                "paid",
                "--output",
                "new",
            ]
        )


@pytest.mark.parametrize("token_limit", [3000000, 4000000])
async def test_accounting_only_cli_uses_new_worker_results_and_old_charges(
    previous, tmp_path, monkeypatch, token_limit
):
    from evaluations.diagnosis import evaluate, parser

    meta_path, summary_path = previous / "run.json", previous / "summary.json"
    meta, summary = json.loads(meta_path.read_text()), json.loads(summary_path.read_text())
    meta.update(
        provider="DeepSeek",
        endpoint="https://api.deepseek.com",
        model="deepseek-flash",
        max_total_tokens=3000000,
    )
    summary["stopped"] = None
    summary["token_budget"]["max_total_tokens"] = 3000000
    meta_path.write_text(json.dumps(meta))
    summary_path.write_text(json.dumps(summary))
    requests = []

    async def provider(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "mock-new-round",
                "model": "deepseek-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "action": "stop",
                                    "question": "保留当前固定输入缺口",
                                    "stop_reason": "evidence_gap",
                                }
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            },
        )

    class OfflineClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            kwargs.setdefault("transport", httpx.MockTransport(provider))
            super().__init__(**kwargs)

    monkeypatch.setattr("evaluations.diagnosis.httpx.AsyncClient", OfflineClient)
    monkeypatch.setenv("ACCOUNTING_TEST_KEY", "offline-mock-key")
    output = tmp_path / "new-cli"
    args = parser().parse_args(
        [
            "run",
            "--split",
            "development",
            "--case-id",
            "D10",
            "--mode",
            "live",
            "--allow-paid",
            "--provider",
            "DeepSeek",
            "--endpoint",
            "https://api.deepseek.com",
            "--model",
            "deepseek-flash",
            "--api-key-env",
            "ACCOUNTING_TEST_KEY",
            "--max-total-tokens",
            str(token_limit),
            "--charge-from",
            str(previous),
            "--output",
            str(output),
        ]
    )
    result = await evaluate(args)
    assert result["executed_runs"] == 1 and result["provider_requests_this_run"] == 1
    assert result["provider_requests"] == 2 and len(requests) == 1
    assert result["token_budget"]["known_tokens"] == 7
    assert result["token_budget"]["max_total_tokens"] == token_limit
    assert not result["all_automatic_passed"] and not result["release_eligible"]
    run_meta = json.loads((output / "run.json").read_text())
    assert run_meta["inherited_attempts"] == []
    assert run_meta["budget_carry_mode"] == "accounting_only"
    assert run_meta["paid_request_limit"] == 13  # One prior + this Task's hard 12.
    assert not (output / "case-1").exists()
    current = json.loads((output / "D10-1/additional.json").read_text())
    assert current["task"]["graph_version"] == "diagnosis-v16"
    assert current["provider_trace"][0]["sequence"] == 2
    reconciled = prior_spend(
        output,
        provider="DeepSeek",
        endpoint="https://api.deepseek.com",
        model="deepseek-flash",
        token_limit=token_limit,
        require_stopped=False,
    )
    assert reconciled["known_tokens"] == 7 and reconciled["provider_requests"] == 2


@pytest.mark.parametrize(
    "alter", [None, "prompt", "data", "plan", "missing_repeat", "critical_failure", "skip_case"]
)
def test_resume_requires_frozen_business_implementation_and_all_failed_attempts(previous, alter):
    meta_path = previous / "run.json"
    meta = json.loads(meta_path.read_text())
    meta.update(
        planned={"mode": "live", "case_ids": ["D01", "D02"], "repeats": 1},
        source_manifest={"app/planner.py": "unchanged"},
        cases_file_sha256="fixed-data",
    )
    record_path = previous / "case-1/agent.json"
    record = json.loads(record_path.read_text())
    record["measurement"].update(
        case_id="D01", repeat=1, group="agent", critical=False, automatic_passed=False
    )
    record["grading"] = {
        "automatic_passed": False,
        "assertions": {"facts_match_raw_records": True, "expected_outcome": False},
    }
    record_path.write_text(json.dumps(record))
    (previous / "progress.jsonl").write_text(json.dumps(record["measurement"]) + "\n")
    meta_path.write_text(json.dumps(meta))
    history = reconcile(previous)
    planned, signatures, manifest = (
        dict(meta["planned"]),
        {"cases_file_sha256": "fixed-data"},
        dict(meta["source_manifest"]),
    )
    if alter == "prompt":
        manifest["app/planner.py"] = "modified"
    elif alter == "data":
        signatures["cases_file_sha256"] = "modified"
    elif alter == "plan":
        planned["repeats"] = 2
    elif alter == "missing_repeat":
        history["_rows"][0]["repeat"] = 2
    elif alter == "critical_failure":
        record["grading"]["assertions"]["facts_match_raw_records"] = False
        record_path.write_text(json.dumps(record))
    elif alter == "skip_case":
        history["_rows"][0]["case_id"] = "D02"
    if alter:
        with pytest.raises(ValueError):
            resume_cases(
                history, planned, signatures, manifest, {"D01": ["agent"], "D02": ["agent"]}
            )
    else:
        assert resume_cases(
            history, planned, signatures, manifest, {"D01": ["agent"], "D02": ["agent"]}
        ) == {("D01", 1)}
        assert history["_rows"][0]["automatic_passed"] is False


@pytest.mark.parametrize("depth", [17, 64])
def test_long_valid_budget_ancestry_preserves_every_receipt(previous, tmp_path, depth):
    import shutil

    root = previous
    for index in range(1, depth):
        history = reconcile(root)
        child = tmp_path / f"segment-{index}"
        shutil.copytree(previous, child)
        meta = json.loads((child / "run.json").read_text())
        meta.update(
            run_id=f"segment-{index}",
            prior_paid_budget={k: v for k, v in history.items() if not k.startswith("_")},
        )
        (child / "run.json").write_text(json.dumps(meta))
        summary = json.loads((child / "summary.json").read_text())
        summary["run_id"] = meta["run_id"]
        (child / "summary.json").write_text(json.dumps(summary))
        (child / "budget-journal.jsonl").write_text(
            json.dumps(
                {
                    "event": "carried_prior_usage",
                    "spent_tokens": 4,
                    "reserved_tokens": 0,
                    "provider_requests": 1,
                    "prior_run_id": history["prior_run"]["run_id"],
                }
            )
            + "\n"
        )
        root = child
    assert reconcile(root)["known_tokens"] == 4
    assert reconcile(root)["provider_requests"] == 1
    original = previous / "case-1/agent.json"
    original.write_text(original.read_text() + " ")
    with pytest.raises(ValueError, match="预算链"):
        reconcile(root)


def test_budget_ancestry_still_rejects_cycles_and_depth_overflow(previous):
    from evaluations.diagnosis_budget import MAX_BUDGET_ANCESTRY

    kwargs = dict(
        provider="test", endpoint="https://provider.invalid", model="chosen", token_limit=25
    )
    with pytest.raises(ValueError, match="循环或过长"):
        prior_spend(previous, **kwargs, _seen=(previous.resolve(),))
    with pytest.raises(ValueError, match="循环或过长"):
        prior_spend(previous, **kwargs, _seen=tuple(range(MAX_BUDGET_ANCESTRY)))
