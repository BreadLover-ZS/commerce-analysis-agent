"""Mock-only formal admission/SDK/cost tests. Frozen U cases are never executed."""

import copy
import json
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from evaluations import diagnosis_formal as formal
from evaluations import diagnosis_formal_plan as plans
from evaluations import diagnosis_one as one
from evaluations import diagnosis_probe as probe
from evaluations.diagnosis import implementation_manifest
from evaluations.diagnosis_probe_accounting import HOLD, LIMIT, file_hash
from tests.test_diagnosis_probe import completion


def save(p, value):
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def dev_copy(value):
    # Old executed synthetic T data with distinct DEV identifiers. This does
    # not import the fresh U packet or run its reserved questions.
    return json.loads(re.sub(r"T(\d{3})", r"DEV\1", json.dumps(value, ensure_ascii=False)))


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    directory = tmp_path / "plan"
    directory.mkdir()
    old = Path("docs/acceptance/diagnosis-v15-h01-h20")
    for name in ["cases.json", "semantic-contract.json", "answer-obligations.json"]:
        save(directory / name, dev_copy(json.loads((old / name).read_bytes())))
    sem = json.loads((directory / "semantic-contract.json").read_bytes())
    from evaluations.diagnosis_grading import VERSION_CONTRACTS

    sem.update(
        schema_version=5,
        accounting_protocol=2,
        expected_runtime=VERSION_CONTRACTS["diagnosis-v16"],
        expected_fixed_runtime=VERSION_CONTRACTS["commerce-review-bundle-v6"],
        expected_answer_contract_version="answer-contract-v5",
        expected_answer_boundary_version=2,
    )
    save(directory / "semantic-contract.json", sem)
    cases = probe.load_cases(directory / "cases.json")[0]
    slots = []
    for c in cases:
        for repeat in range(1, 4):
            for group in (
                ["fixed", "agent"] if c.expected.baseline_capability == "common" else ["additional"]
            ):
                slots.append(
                    {
                        "slot_id": f"{c.case_id}-{repeat}/{group}",
                        "case_id": c.case_id,
                        "repeat": repeat,
                        "group": group,
                        "max_http": 2 if group == "fixed" else 4,
                        "model_decision_reserve": 0,
                        "stop_after_model_failure": True,
                        "manual_review_required_before_next_slot": True,
                        "status": "UNEXECUTED",
                    }
                )
    save(
        directory / "execution-plan.json",
        {
            "slots": slots,
            "require_sequential_launch": True,
            "no_automatic_resume_or_retry": True,
            "no_sdk_retries": True,
            "no_fallback_extra_calls": True,
            "stop_reasons": [
                "protocol_error",
                "proposal_or_tool_rejected",
                "call_error",
                "automatic_grade_failed",
                "manual_review_failed",
                "usage_unknown",
                "budget_insufficient",
            ],
        },
    )
    save(directory / "provenance.json", {"scope": "MOCK_UNIT_FIXTURE_NOT_REAL_AUTHORIZATION"})
    anchor = tmp_path / "known-mock-anchor"
    anchor.mkdir()
    save(anchor / "receipt.json", {"mock_base_only": True})
    save(
        anchor / "review.json",
        {
            "receipt_sha256": file_hash(anchor / "receipt.json"),
            "verdict": "PASS",
            "reviewer": "MOCK_ONLY",
            "checks": {"mock_base": True},
        },
    )
    base = {
        "known_tokens": 4,
        "frozen_unknown_tokens": HOLD,
        "confirmed_http_requests": 1,
        "directory": str(anchor),
        "legacy_root": str(anchor),
        "run_id": "MOCK_ONLY",
        "parent_failed": False,
        "parent_receipt_sha256": file_hash(anchor / "receipt.json"),
        "parent_implementation_sha256": "prior-mock",
        "ancestry_depth": 1,
    }
    original = probe.load_parent

    def loader(path, _seen=()):
        if Path(path).resolve() == anchor:
            return copy.deepcopy(base)
        return original(path, _seen)

    monkeypatch.setattr(probe, "load_parent", loader)
    monkeypatch.setattr(plans, "load_parent", loader)
    monkeypatch.setattr(formal, "load_parent", loader)
    # Accounting/corpus provenance is mocked ONLY at the synthetic anchor; new
    # child SDK invocations, traces, proofs, totals and source bytes use real audit.
    monkeypatch.setattr(plans, "history", lambda p: {"known_input_hashes": [], "case_files": {}})
    log = tmp_path / "local-proof.log"
    log.write_text("MOCK fixture evidence, not a real readiness approval\n")
    proof = tmp_path / "local-proof.json"
    save(
        proof,
        {
            "implementation_sha256": implementation_manifest()[1],
            "local_verification_passed": True,
            "provider_requests": 0,
            "evidence_files": [{"file": str(log), "sha256": file_hash(log)}],
        },
    )
    plans.freeze(directory, anchor, proof)
    m = plans.check(directory)
    auth = tmp_path / "mock-authorization.json"
    save(
        auth,
        {
            "kind": "FORMAL_SINGLE_TASK_AUTHORIZATION",
            "authorized_by": "HUMAN_USER_EXPLICIT_INSTRUCTION",
            "granted": True,
            "new_packet_transmission_granted": True,
            "formal_conservative_carry_granted": True,
            "plan_sha256": file_hash(directory / "manifest.json"),
            "cases_sha256": m["packet_files_sha256"]["cases.json"],
            "implementation_sha256": m["implementation_sha256"],
            "model": m["model"],
            "cumulative_token_limit": LIMIT,
            "parent_anchor_receipt_sha256": base["parent_receipt_sha256"],
            "legacy_unknown_hold_tokens": HOLD,
            "test_fixture_only": "NEVER_A_REAL_USER_GRANT",
        },
    )
    monkeypatch.setenv("FORMAL_TEST_KEY", "mock-offline-only")
    return SimpleNamespace(directory=directory, anchor=anchor, auth=auth, tmp=tmp_path, base=base)


def args(frozen, parent=None, slot="H01-1/fixed", output="slot-one"):
    return SimpleNamespace(
        plan=frozen.directory,
        parent=parent or frozen.anchor,
        slot_id=slot,
        output=frozen.tmp / output,
        authorization=frozen.auth,
        allow_paid=True,
        max_total_tokens=LIMIT,
        api_key_env="FORMAL_TEST_KEY",
    )


def reviewed(path, verdict="PASS"):
    save(
        path / "review.json",
        {
            "receipt_sha256": file_hash(path / "receipt.json"),
            "verdict": verdict,
            "reviewer": "OFFLINE_TEST_ONLY",
            "checks": {"not_real_review": True},
        },
    )


def sdk_transport(monkeypatch, mode="valid"):
    sent = []

    async def transport(request):
        body = json.loads(request.content)
        sent.append(body)
        if mode == "timeout":
            raise httpx.ReadTimeout("mock timeout")
        from app.core.model_boundary import CURRENT_ATTEMPT

        phase = CURRENT_ATTEMPT.get().identity["phase"]
        body["_mock_phase"] = phase
        if phase == "propose":
            count = sum(x.get("_mock_phase") == "propose" for x in sent)
            value = {
                "action": "tool",
                "question": "核对已有授权金额",
                "hypothesis_code": "sales_concentration" if count == 1 else "refund_amount_change",
                "tool_name": "rank_contributors" if count == 1 else "analyze_refunds",
                "arguments": {
                    "dimension": "sku",
                    "top_k": 3,
                    "filters": {"skus": ["DEV011", "DEV012", "DEV013"]},
                }
                if count == 1
                else {"filters": {"skus": ["DEV011", "DEV012", "DEV013"]}},
            }
            if mode == "diagnostic_scope":
                value["arguments"] = {"filters": {"skus": ["FOREIGN"]}}
            if mode == "diagnostic_no_information":
                value.update(
                    tool_name="inspect_dataset", hypothesis_code="data_coverage", arguments={}
                )
            return completion(
                value, body["tools"][0]["function"]["name"] if body.get("tools") else None
            )
        if len(sent) % 2:
            value = {
                "analysis": ["invalid_code"] if mode == "schema" else [],
                "recommendations": [],
            }
        else:
            value = {
                "unsupported_claim_ids": ["review-summary"] if mode == "semantic" else [],
                "unsupported_limitation_indices": [],
                "explanation": "offline mock",
            }
        response = completion(value)
        if mode == "unknown":
            raw = response.json()
            raw.pop("usage")
            response = httpx.Response(200, json=raw)
        return response

    original = httpx.AsyncClient

    class OfflineClient(original):
        def __init__(self, **kw):
            super().__init__(**kw, transport=httpx.MockTransport(transport))

    monkeypatch.setattr(httpx, "AsyncClient", OfflineClient)
    return sent


async def test_two_adjacent_sdk_tasks_require_review_and_carry_exact_charges(frozen, monkeypatch):
    sent = sdk_transport(monkeypatch)
    a = args(frozen)
    assert await formal.run(a) is None
    first = probe.audit_probe(a.output)
    assert len(sent) == 2 and first["known_tokens"] == 12 and first["confirmed_http_requests"] == 3
    assert (a.output / "recovery-workspace/app.sqlite").is_file()
    assert (a.output / "http-response-1.json").is_file()
    b = args(frozen, a.output, "H01-1/agent", "slot-two")
    with pytest.raises(ValueError, match="review_pending"):
        await formal.run(b)
    assert len(sent) == 2
    reviewed(a.output)
    parent_db_hash = file_hash(a.output / "recovery-workspace/app.sqlite")
    assert await formal.run(b) is None
    assert file_hash(a.output / "recovery-workspace/app.sqlite") == parent_db_hash
    second = probe.audit_probe(b.output)
    first_receipt = json.loads((a.output / "receipt.json").read_bytes())
    second_receipt = json.loads((b.output / "receipt.json").read_bytes())
    assert first_receipt["task"]["bundle_id"] == second_receipt["task"]["bundle_id"]
    assert (
        first_receipt["task"]["parameters"]["bundle_hash"]
        == second_receipt["task"]["parameters"]["bundle_hash"]
    )
    assert (
        len(sent) == 4 and second["known_tokens"] == 20 and second["confirmed_http_requests"] == 5
    )
    reviewed(b.output)
    audited = formal.audit_round(frozen.directory, b.output)
    assert audited["executed_slots_reviewed"] == 2 and not audited["formal_round_complete"]
    assert not audited["release_eligible"]


@pytest.mark.parametrize(
    "mode,expected_http", [("schema", 1), ("unknown", 1), ("timeout", 1), ("ordinary_grade", 2)]
)
async def test_first_failure_stops_and_never_admits_a_following_slot(
    frozen, monkeypatch, mode, expected_http
):
    sent = sdk_transport(monkeypatch, mode)
    if mode == "ordinary_grade":
        original = one.grade

        def failed(*a, **kw):
            result = original(*a, **kw)
            result["automatic_passed"] = False
            result["assertions"]["v15_required_values_displayed"] = False
            return result

        monkeypatch.setattr(one, "grade", failed)
    a = args(frozen)
    assert await formal.run(a)
    assert len(sent) == expected_http
    summary = json.loads((a.output / "summary.json").read_bytes())
    record = json.loads((a.output / "receipt.json").read_bytes())
    assert (a.output / "recovery-workspace/app.sqlite").is_file()
    if mode in {"unknown", "timeout"}:
        assert summary["budget"]["unresolved_reserved_tokens"] == HOLD
        assert summary["new_known_tokens"] == 0
        assert any(c["status"] == "UNKNOWN" for c in record["model_invocations"])
    else:
        assert summary["new_known_tokens"] == expected_http * 4
    reviewed(a.output)  # A forged PASS review cannot override an automatic failure.
    b = args(frozen, a.output, "H01-1/agent", "forbidden-next")
    with pytest.raises(ValueError):
        await formal.run(b)
    assert len(sent) == expected_http and not b.output.exists()


@pytest.mark.parametrize(
    "damage",
    [
        "no_grant",
        "probe_only",
        "wrong_plan",
        "wrong_model",
        "raise_limit",
        "clear_hold",
        "wrong_slot",
        "false_paid_flag",
        "claim",
        "source",
    ],
)
async def test_admission_damage_is_rejected_before_credentials_or_output(
    frozen, monkeypatch, damage
):
    a = args(frozen)
    auth = json.loads(frozen.auth.read_bytes())
    if damage == "no_grant":
        auth["granted"] = False
    elif damage == "probe_only":
        auth["formal_conservative_carry_granted"] = False
    elif damage == "wrong_plan":
        auth["plan_sha256"] = "0" * 64
    elif damage == "wrong_model":
        auth["model"]["model"] = "unapproved"
    elif damage == "raise_limit":
        a.max_total_tokens = LIMIT + 1
    elif damage == "clear_hold":
        auth["legacy_unknown_hold_tokens"] = 0
    elif damage == "wrong_slot":
        a.slot_id = "H01-1/agent"
    elif damage == "false_paid_flag":
        a.allow_paid = False
    elif damage == "claim":
        save(frozen.anchor.with_name(frozen.anchor.name + ".budget-continuation.json"), {})
    elif damage == "source":
        monkeypatch.setattr(plans, "implementation_manifest", lambda: ({}, "changed-source"))
    save(frozen.auth, auth)

    def forbidden(*a, **kw):
        pytest.fail("credentials reached before admission")

    monkeypatch.setattr(formal, "live_configuration", forbidden)
    with pytest.raises(ValueError):
        await formal.run(a)
    assert not a.output.exists()


@pytest.mark.parametrize("verdict", ["FAIL", "PENDING", "WRONG_HASH"])
async def test_manual_review_failure_blocks_the_second_task(frozen, monkeypatch, verdict):
    sent = sdk_transport(monkeypatch)
    a = args(frozen)
    assert await formal.run(a) is None
    reviewed(a.output, "PASS" if verdict == "WRONG_HASH" else verdict)
    if verdict == "WRONG_HASH":
        value = json.loads((a.output / "review.json").read_bytes())
        value["receipt_sha256"] = "0" * 64
        save(a.output / "review.json", value)
    b = args(frozen, a.output, "H01-1/agent", "next")
    with pytest.raises(ValueError, match="review"):
        await formal.run(b)
    assert len(sent) == 2 and not b.output.exists()


def test_full_96_slot_metadata_chain_fits_bound_without_executing_business_cases(
    frozen, monkeypatch
):
    plan = plans.check(frozen.directory)
    assert frozen.base["ancestry_depth"] + 96 < probe.MAX_PROBE_ANCESTRY
    seal = file_hash(frozen.directory / "manifest.json")

    def only_metadata_parent(path, *a, **kw):
        return {**frozen.base, "parent_failed": False}

    monkeypatch.setattr(formal, "load_parent", only_metadata_parent)
    # This isolates ordering, limit and claim validation; no fake quality or
    # fake charges leave tmp_path or become real formal evidence.
    mocked_grade = {"automatic_passed": True, "assertions": {"metadata_only": True}}
    monkeypatch.setattr(formal, "grade", lambda *a, **kw: mocked_grade)
    parent = frozen.anchor
    cases = {c.case_id: c for c in probe.load_cases(frozen.directory / "cases.json")[0]}
    from evaluations.diagnosis_cases import business_input

    for index, slot in enumerate(plan["slots"]):
        child = frozen.tmp / f"metadata-only-{index}"
        child.mkdir()
        record = {
            "task": {
                "bundle_id": f"pair-{slot['case_id']}-{slot['repeat']}",
                "metrics": {"usage_complete": True},
                "validation": {"passed": True},
                "status": "succeeded",
                "report": {"stop_reason": "target_covered"},
                "parameters": {
                    "bundle_hash": f"pair-{slot['case_id']}-{slot['repeat']}",
                    "answer_boundary_version": 2,
                    "accounting_protocol": 2,
                    "budget": {
                        "max_model_calls": slot["max_http"],
                        "decision_call_reserve": 0,
                        "stop_after_model_failure": True,
                    },
                },
            },
            "grading": mocked_grade,
        }
        save(child / "business-input.json", business_input(cases[slot["case_id"]]))
        save(child / "summary.json", {"failure_reason": None})
        save(child / "receipt.json", record)
        save(child / "bundle.json", {})
        meta = {
            "kind": "FORMAL_SLOT",
            "formal_plan_sha256": seal,
            "formal_plan_directory": str(frozen.directory),
            "implementation_sha256": plan["implementation_sha256"],
            "slot_index": index,
            "slot_id": slot["slot_id"],
            "case_id": slot["case_id"],
            "group": slot["group"],
            "max_http": slot["max_http"],
            "parent": str(parent),
            "run_id": f"metadata-{index}",
            "parent_accounting": {
                "run_id": f"metadata-{index - 1}" if index else frozen.base["run_id"],
                "known_tokens": 4,
            },
        }
        save(child / "probe.json", meta)
        reviewed(child)
        save(
            parent.with_name(parent.name + ".budget-continuation.json"),
            {
                "next_directory": str(child),
                "next_run_id": meta["run_id"],
                "prior_run_id": meta["parent_accounting"]["run_id"],
                "carried_tokens": 4,
            },
        )
        parent = child
    _, rows = formal.progress(frozen.directory, parent)
    assert len(rows) == 96 and rows[-1]["slot_index"] == 95


@pytest.mark.parametrize(
    "damage", ["packet", "oracle", "sidecar", "verification", "slot", "drop_hash"]
)
async def test_frozen_preflight_tampering_blocks_before_key(frozen, monkeypatch, damage):
    m = json.loads((frozen.directory / "manifest.json").read_bytes())
    if damage == "packet":
        p = frozen.directory / "cases.json"
        p.write_bytes(p.read_bytes() + b"\n")
    elif damage == "oracle":
        p = frozen.directory / "answer-obligations.json"
        a = json.loads(p.read_bytes())
        a["cases"][0]["raw_matrix"][0]["raw_integer"] += 1
        save(p, a)
        sem = json.loads((frozen.directory / "semantic-contract.json").read_bytes())
        sem["author_obligations"] = a
        save(frozen.directory / "semantic-contract.json", sem)
        for name in ["answer-obligations.json", "semantic-contract.json"]:
            m["packet_files_sha256"][name] = file_hash(frozen.directory / name)
    elif damage == "sidecar":
        p = frozen.directory / "semantic-contract.json"
        a = json.loads(p.read_bytes())
        a["expected_answer_boundary_version"] = 1
        save(p, a)
        m["packet_files_sha256"]["semantic-contract.json"] = file_hash(p)
    elif damage == "verification":
        p = Path(m["local_verification"]["file"])
        a = json.loads(p.read_bytes())
        a["local_verification_passed"] = False
        save(p, a)
    elif damage == "slot":
        m["slots"][0]["max_http"] = 1
    elif damage == "drop_hash":
        m["packet_files_sha256"].pop("provenance.json")
    save(frozen.directory / "manifest.json", m)

    def forbidden(*a, **kw):
        pytest.fail("credentials read before frozen material verification")

    monkeypatch.setattr(formal, "live_configuration", forbidden)
    a = args(frozen)
    with pytest.raises(ValueError):
        await formal.run(a)
    assert not a.output.exists()


@pytest.mark.parametrize("mode", ["diagnostic_scope", "diagnostic_no_information"])
async def test_first_bad_diagnostic_step_never_spends_on_correction(frozen, monkeypatch, mode):
    sent = sdk_transport(monkeypatch, mode)
    a = args(frozen)
    assert await formal.run(a) is None
    reviewed(a.output)
    b = args(frozen, a.output, "H01-1/agent", "bad-agent")
    assert await formal.run(b)
    assert len(sent) == 3
    record = json.loads((b.output / "receipt.json").read_bytes())
    if mode == "diagnostic_no_information":
        assert record["task"]["report"]["stop_reason"] == "no_information"
    else:
        assert any(s["status"] == "REJECTED" for s in record["steps"]) or any(
            m["status"] == "FAILED" for m in record["model_invocations"]
        )
    assert probe.audit_probe(b.output)["known_tokens"] == 16


async def test_semantic_rejection_stops_even_if_a_grader_says_pass(frozen, monkeypatch):
    sent = sdk_transport(monkeypatch, "semantic")
    original = one.grade

    def overly_optimistic(*a, **kw):
        value = original(*a, **kw)
        value["automatic_passed"] = True
        return value

    monkeypatch.setattr(one, "grade", overly_optimistic)
    a = args(frozen)
    assert await formal.run(a)
    assert len(sent) == 2
    record = json.loads((a.output / "receipt.json").read_bytes())
    # Rejected model text is replaced by a valid deterministic report. The valid
    # fallback must still stop the paid round, even if a grader approves it.
    assert record["task"]["validation"]["fallback_used"] is True
    assert json.loads((a.output / "summary.json").read_bytes())["failure_reason"] == (
        "model_report_fallback"
    )
    assert probe.audit_probe(a.output)["known_tokens"] == 12


@pytest.mark.parametrize("location", ["existing", "inside_parent", "inside_plan", "ancestor"])
async def test_output_path_rejected_before_credentials(frozen, monkeypatch, location):
    a = args(frozen)
    a.output = {
        "existing": frozen.anchor,
        "inside_parent": frozen.anchor / "nested",
        "inside_plan": frozen.directory / "nested",
        "ancestor": frozen.tmp,
    }[location]

    def forbidden(*a, **kw):
        pytest.fail("credentials read before output path admission")

    monkeypatch.setattr(formal, "live_configuration", forbidden)
    with pytest.raises(ValueError, match="formal_output"):
        await formal.run(a)


async def test_missing_pair_recovery_blocks_before_keys_and_never_reruns_fixed(frozen, monkeypatch):
    sent = sdk_transport(monkeypatch)
    a = args(frozen)
    assert await formal.run(a) is None
    reviewed(a.output)
    (a.output / "recovery-workspace/app.sqlite").unlink()
    b = args(frozen, a.output, "H01-1/agent", "forbidden-pair")

    def forbidden(*a, **kw):
        pytest.fail("key read before paired immutable input can be recovered")

    monkeypatch.setattr(formal, "live_configuration", forbidden)
    with pytest.raises(ValueError, match="paired_workspace"):
        await formal.run(b)
    assert len(sent) == 2 and not b.output.exists()


async def test_interruption_retains_paid_response_and_workspace_without_reissue(
    frozen, monkeypatch
):
    sent = sdk_transport(monkeypatch)
    original = formal.execute

    async def interrupted(*a, **kw):
        await original(*a, **kw)
        raise InterruptedError("offline simulated interruption after persisted model response")

    monkeypatch.setattr(formal, "execute", interrupted)
    a = args(frozen)
    assert await formal.run(a) == "execution_exception"
    assert len(sent) == 2
    assert (a.output / "recovery-workspace/app.sqlite").is_file()
    for n in (1, 2):
        assert json.loads((a.output / f"http-response-{n}.json").read_bytes())
    assert probe.audit_probe(a.output)["known_tokens"] == 12
    reviewed(a.output)  # A spurious manual PASS cannot override the interruption.
    with pytest.raises(ValueError, match="failed_round"):
        await formal.run(args(frozen, a.output, "H01-1/agent", "blocked-after-interruption"))
    with pytest.raises(ValueError, match="output_already_exists"):
        await formal.run(a)
    assert len(sent) == 2


@pytest.mark.parametrize("margin", [0, -1])
def test_exact_next_request_capacity_counts_old_frozen_unknown(frozen, margin):
    # Synthetic anchor changes isolate the arithmetic gate, never a real ledger.
    known = LIMIT - 2 * HOLD - margin
    frozen.base["known_tokens"] = known
    m = json.loads((frozen.directory / "manifest.json").read_bytes())
    m["parent_accounting"]["known_tokens"] = known
    save(frozen.directory / "manifest.json", m)
    auth = json.loads(frozen.auth.read_bytes())
    auth["plan_sha256"] = file_hash(frozen.directory / "manifest.json")
    save(frozen.auth, auth)
    if margin == 0:
        _, parent, _, _, _ = formal.validate_entry(args(frozen))
        assert parent["known_tokens"] + parent["frozen_unknown_tokens"] + HOLD == LIMIT
    else:
        with pytest.raises(ValueError, match="budget_insufficient"):
            formal.validate_entry(args(frozen))
