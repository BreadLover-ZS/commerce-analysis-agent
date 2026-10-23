"""Free preparation must reject stale plans and preserve paid accounting."""

import json
from types import SimpleNamespace

import httpx
import pytest

from evaluations import diagnosis_plan as planning
from evaluations.diagnosis_cases import DEFAULT_CASES


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False))


@pytest.fixture
def prepared_inputs(tmp_path, monkeypatch):
    def no_http(*args, **kwargs):
        pytest.fail("Plan preparation must not send HTTP")

    monkeypatch.setattr(httpx.Client, "send", no_http)
    monkeypatch.setattr(httpx.AsyncClient, "send", no_http)
    directory = tmp_path / "plan"
    directory.mkdir()
    raw = json.loads(DEFAULT_CASES.read_bytes())
    raw["cases"] = [c for c in raw["cases"] if c["split"] == "holdout"]
    raw["splits"] = {"development": 0, "regression": 0, "holdout": 20}
    cases = directory / "cases.json"
    save(cases, raw)
    _, contract = planning.validate_cases(cases)
    attempts = contract["total_tasks"]
    prior = tmp_path / "paid"
    prior.mkdir()
    meta = {
        "run_id": "paid-reference",
        "planned": {"mode": "live", "split": "holdout"},
        "provider": "DeepSeek",
        "endpoint": "https://api.deepseek.com",
        "model": "deepseek-flash",
        "max_total_tokens": 6000000,
    }
    rows, events = [], []
    for i in range(1, attempts + 1):
        row = {"receipt": f"attempt-{i}.json", "provider_requests": 1}
        metrics = {"usage_complete": True, "input_tokens": 3, "output_tokens": 1}
        trace = {
            "sequence": i,
            "http_status": 200,
            "known_tokens": 4,
            "token_reservation": planning.REQUEST_RESERVATION,
            "response": {"usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}},
        }
        save(
            prior / row["receipt"],
            {"measurement": row, "task": {"metrics": metrics}, "provider_trace": [trace]},
        )
        rows.append(row)
        events += [
            {
                "event": "reserved_before_send",
                "sequence": i,
                "reservation": planning.REQUEST_RESERVATION,
                "spent_tokens": (i - 1) * 4,
                "reserved_tokens": planning.REQUEST_RESERVATION,
            },
            {
                "event": "known_usage",
                "sequence": i,
                "usage_tokens": 4,
                "spent_tokens": i * 4,
                "reserved_tokens": 0,
            },
        ]
    save(prior / "run.json", meta)
    save(
        prior / "summary.json",
        {
            "run_id": meta["run_id"],
            "stopped": None,
            "executed_runs": attempts,
            "provider_requests": attempts,
            "token_budget": {
                "max_total_tokens": 6000000,
                "known_tokens": attempts * 4,
                "unresolved_reserved_tokens": 0,
                "upper_bound_charged_tokens": attempts * 4,
            },
        },
    )
    for filename, values in [("progress.jsonl", rows), ("budget-journal.jsonl", events)]:
        (prior / filename).write_text("".join(json.dumps(v) + "\n" for v in values))
    return directory, cases, prior


def freeze(inputs):
    directory, cases, prior = inputs
    return planning.freeze(cases, directory, prior, 8000000, prior)[0]


def test_freeze_and_check_make_no_paid_claim_or_http_and_do_not_overwrite(prepared_inputs):
    directory, _, prior = prepared_inputs
    before = {p.name: p.read_bytes() for p in prior.iterdir()}
    plan = freeze(prepared_inputs)
    result = planning.check(directory)
    assert result["validation_passed"] and result["provider_requests_this_check"] == 0
    assert not result["release_eligible"] and result["budget"]["authorization_required"]
    assert plan["history"]["known_tokens"] == plan["total_tasks"] * 4
    assert not prior.with_name(prior.name + ".budget-continuation.json").exists()
    assert {p.name: p.read_bytes() for p in prior.iterdir()} == before
    frozen = (directory / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        freeze(prepared_inputs)
    assert (directory / "manifest.json").read_bytes() == frozen


@pytest.mark.parametrize("tamper", ["query", "expected", "source", "targets", "history", "claim"])
def test_changed_plan_or_accounting_cannot_pass_preflight(prepared_inputs, monkeypatch, tamper):
    directory, cases, prior = prepared_inputs
    freeze(prepared_inputs)
    if tamper in {"query", "expected"}:
        raw = json.loads(cases.read_bytes())
        if tamper == "query":
            raw["cases"][0]["query"] += "，修改问法"
        else:
            raw["cases"][0]["expected"]["net_cents"] += 1
        save(cases, raw)
    elif tamper == "source":
        monkeypatch.setattr(planning, "implementation_manifest", lambda: ({}, "new-source"))
    elif tamper == "targets":
        changed = directory / "targets.json"
        changed.write_bytes(planning.TARGETS.read_bytes() + b"\n")
        monkeypatch.setattr(planning, "TARGETS", changed)
    elif tamper == "history":
        receipt = prior / "attempt-1.json"
        raw = json.loads(receipt.read_bytes())
        raw["task"]["metrics"]["usage_complete"] = False
        save(receipt, raw)
    else:
        prior.with_name(prior.name + ".budget-continuation.json").write_text("{}")
    with pytest.raises(ValueError):
        planning.check(directory)


def test_changing_only_question_does_not_create_new_data(prepared_inputs):
    directory, cases, _ = prepared_inputs
    used, _ = planning.validate_cases(cases)
    raw = json.loads(cases.read_bytes())
    raw["cases"][0]["query"] += "，请核对"
    save(cases, raw)
    with pytest.raises(ValueError, match="业务数据重复"):
        planning.validate_cases(cases, [planning.input_hash(used[0])])
    assert not (directory / "manifest.json").exists()


@pytest.mark.parametrize("tamper", ["count", "fault", "assertion", "tool", "clarification", "pair"])
def test_incomplete_or_scripted_corpus_never_becomes_formal_plan(prepared_inputs, tamper):
    directory, cases, _ = prepared_inputs
    raw = json.loads(cases.read_bytes())
    if tamper == "count":
        raw["cases"].pop()
        raw["splits"]["holdout"] = 19
    elif tamper == "fault":
        raw["cases"][0].update(execution="fault", harness={"mode": "cache_loop"})
    elif tamper in {"assertion", "tool"}:
        field = "required_assertions" if tamper == "assertion" else "required_tools"
        raw["cases"][0]["expected"][field] = ["unimplemented"]
    elif tamper == "clarification":
        raw["cases"][0]["harness"] = {"mode": "clarify", "clarification_choice": "foreign"}
    else:
        for case in raw["cases"]:
            case["expected"]["baseline_capability"] = "additional"
    save(cases, raw)
    with pytest.raises(ValueError):
        freeze(prepared_inputs)
    assert not (directory / "manifest.json").exists()


def test_budget_projection_preserves_spend_and_full_request_reservation():
    history = {"known_tokens": 4749932, "previous_cumulative_limit": 6000000}
    result = planning.budget_projection(history, 8000000, 1161798)
    assert result["estimated_cumulative_with_reservation"] == 6963306
    assert not result["existing_limit_estimated_sufficient"]
    assert result["proposed_limit_estimated_sufficient"]
    assert result["proposed_margin_after_reference_round_and_reservation"] == 1036694
    assert result["authorization_required"] and result["monetary_cost"] is None
    assert history["known_tokens"] == 4749932


@pytest.mark.parametrize("mismatch", [None, "cases", "mode", "subset", "limit", "resume", "cap"])
def test_actual_run_arguments_are_bound_to_frozen_plan(prepared_inputs, mismatch):
    directory, cases, prior = prepared_inputs
    freeze(prepared_inputs)
    args = SimpleNamespace(
        frozen_plan=directory,
        cases=cases,
        charge_paid_run=prior,
        prior_paid_run=None,
        mode="live",
        split="holdout",
        repeats=3,
        final_holdout=True,
        case_ids=None,
        max_paid_requests=None,
        max_total_tokens=8000000,
        provider="DeepSeek",
        endpoint="https://api.deepseek.com",
        model="deepseek-flash",
    )
    if mismatch == "cases":
        args.cases = DEFAULT_CASES
    elif mismatch == "mode":
        args.mode = "demo"
    elif mismatch == "subset":
        args.case_ids = ["H01"]
    elif mismatch == "limit":
        args.max_total_tokens = 9000000
    elif mismatch == "resume":
        args.prior_paid_run = prior
    elif mismatch == "cap":
        args.max_paid_requests = 2
    if mismatch is None:
        planning.guard_run(args)
    else:
        with pytest.raises(ValueError, match="运行参数"):
            planning.guard_run(args)


async def test_stale_frozen_plan_is_rejected_before_credentials_or_http(
    prepared_inputs, monkeypatch
):
    from evaluations import diagnosis

    directory, _, _ = prepared_inputs
    freeze(prepared_inputs)
    monkeypatch.setattr(planning, "implementation_manifest", lambda: ({}, "different-code"))

    def no_credentials(*args):
        pytest.fail("A stale plan must fail before loading credentials")

    monkeypatch.setattr(diagnosis, "live_configuration", no_credentials)
    with pytest.raises(ValueError, match="实现指纹"):
        await diagnosis.evaluate(SimpleNamespace(frozen_plan=directory, charge_paid_run=None))


def test_preflight_checks_ancestry_after_adding_the_new_round(prepared_inputs, monkeypatch):
    monkeypatch.setattr(planning, "MAX_BUDGET_ANCESTRY", 1)
    with pytest.raises(ValueError, match="加入新轮后"):
        freeze(prepared_inputs)
    assert not (prepared_inputs[0] / "manifest.json").exists()
