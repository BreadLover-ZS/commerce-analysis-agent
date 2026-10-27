"""Adversarial copied-metadata and denied-entry checks; no fresh Task execution."""

import asyncio
import copy
import json
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from prepare import ROOT, read, save
from verify import BASE, PARENT, verify

from evaluations import diagnosis, diagnosis_probe
from evaluations.diagnosis_probe_accounting import LIMIT, file_hash

MODES = [
    "question",
    "raw_amount",
    "author_amount",
    "author_unit",
    "missing_obligation",
    "daily_omission",
    "excluded_scope",
    "runtime",
    "fixed_runtime",
    "protocol",
    "one_sku_daily",
    "slot_omission",
    "http_cap",
    "decision_reserve",
    "stop_flag",
    "review_flag",
    "retry_flag",
    "stop_classes",
    "paid_admission",
    "drop_blockers",
    "source_hash",
    "known_spend",
    "hold_zero",
    "token_increase",
    "drop_hash_inventory",
    "history_hashes",
    "pretend_paid_ready",
]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def copied_test(mode):
    with TemporaryDirectory(prefix="u-preflight-copy-") as d:
        p = Path(d)
        for name in read(BASE / "manifest.json")["packet_files_sha256"]:
            shutil.copyfile(BASE / name, p / name)
        shutil.copyfile(BASE / "manifest.json", p / "manifest.json")
        cases = read(p / "cases.json")
        a = read(p / "answer-obligations.json")
        s = read(p / "semantic-contract.json")
        m = read(p / "manifest.json")
        slots = read(p / "execution-plan.json")
        if mode == "question":
            cases["cases"][0]["query"] = "只要本期净销售额。"
        elif mode == "raw_amount":
            cases["cases"][0]["sales"]["daily_sales"][0]["revenue_cents"] += 1
        elif mode == "author_amount":
            a["cases"][0]["raw_matrix"][0]["raw_integer"] += 1
        elif mode == "author_unit":
            a["cases"][0]["raw_matrix"][0]["unit"] = "count"
        elif mode == "missing_obligation":
            a["cases"][0]["raw_matrix"].pop()
        elif mode == "daily_omission":
            a["cases"][12]["daily_matrix"].pop()
        elif mode == "excluded_scope":
            a["cases"][13]["final_skus"] = cases["cases"][13]["scope_skus"]
        elif mode == "runtime":
            s["expected_runtime"]["graph_version"] = "diagnosis-v15"
        elif mode == "fixed_runtime":
            s["expected_fixed_runtime"]["prompt_version"] = "commerce-review-v5"
        elif mode == "protocol":
            s["accounting_protocol"] = 1
        elif mode == "one_sku_daily":
            cases["cases"][12]["scope_skus"] = cases["cases"][12]["scope_skus"][:1]
        elif mode == "slot_omission":
            slots["slots"].pop()
        elif mode == "http_cap":
            slots["slots"][1]["max_http"] = 1
        elif mode == "decision_reserve":
            slots["slots"][1]["model_decision_reserve"] = 3
        elif mode == "stop_flag":
            slots["slots"][1]["stop_after_model_failure"] = False
        elif mode == "review_flag":
            slots["slots"][1]["manual_review_required_before_next_slot"] = False
        elif mode == "retry_flag":
            slots["no_sdk_retries"] = False
        elif mode == "stop_classes":
            slots["stop_reasons"].remove("automatic_grade_failed")
        elif mode == "paid_admission":
            m["paid_admission"] = True
        elif mode == "drop_blockers":
            m["blockers"] = []
        elif mode == "source_hash":
            m["implementation_sha256"] = "0" * 64
        elif mode == "known_spend":
            m["parent_accounting"]["known_tokens"] -= 1
        elif mode == "hold_zero":
            m["budget"]["legacy_unknown_hold_tokens"] = 0
        elif mode == "token_increase":
            m["budget"]["cumulative_limit"] = LIMIT + 1
        elif mode == "drop_hash_inventory":
            m["packet_files_sha256"].pop("prepare.py")
        elif mode == "history_hashes":
            history = read(p / "history-provenance.json")
            history["known_input_hashes"] = []
            write(p / "history-provenance.json", history)
        elif mode == "pretend_paid_ready":
            m["status"] = "FROZEN_AWAITING_EXPLICIT_PAID_AUTHORIZATION"
        if mode.startswith("author_") or mode in [
            "missing_obligation",
            "daily_omission",
            "excluded_scope",
        ]:
            s["author_obligations"] = copy.deepcopy(a)
        write(p / "cases.json", cases)
        write(p / "answer-obligations.json", a)
        write(p / "semantic-contract.json", s)
        write(p / "execution-plan.json", slots)
        # Arithmetic, inventory and policy checks must reject even after a
        # copied manifest's sidecar hashes are synchronized with forged tables.
        for name in m["packet_files_sha256"]:
            m["packet_files_sha256"][name] = file_hash(p / name)
        write(p / "manifest.json", m)
        try:
            verify(p)
        except (ValueError, KeyError, TypeError):
            return {"mutation": mode, "rejected": True, "sidecar_hashes_rebound_in_copy": True}
        return {"mutation": mode, "rejected": False, "sidecar_hashes_rebound_in_copy": True}


async def denied_entry_checks():
    counters = {"credential_boundary": 0, "task_execution": 0, "http_send": 0}

    def credentials(*a, **kw):
        counters["credential_boundary"] += 1
        raise AssertionError("Must reject before credentials")

    async def task(*a, **kw):
        counters["task_execution"] += 1
        raise AssertionError("Fresh formal cases must never execute in preparation")

    async def http_send(*a, **kw):
        counters["http_send"] += 1
        raise AssertionError("No HTTP permitted")

    rows = []
    with (
        patch.object(diagnosis, "live_configuration", credentials),
        patch.object(diagnosis_probe, "live_configuration", credentials),
        patch.object(diagnosis, "execute", task),
        patch.object(diagnosis_probe, "execute", task),
        patch("httpx.AsyncClient.send", http_send),
    ):
        args = SimpleNamespace(
            frozen_plan=BASE,
            charge_paid_run=PARENT,
            prior_reconciliation=None,
            mode="live",
            split="holdout",
            final_holdout=True,
        )
        try:
            await diagnosis.evaluate(args)
        except ValueError as error:
            assert "冻结计划版本或状态无效" in str(error)
            rows.append(
                {
                    "entrypoint": "legacy_formal_cli",
                    "rejected_before_credentials": True,
                    "reason": "preparation_is_not_a_paid_frozen_plan",
                }
            )
        else:
            raise AssertionError("Legacy formal entry accepted this preparation")
        args = SimpleNamespace(
            parent=PARENT,
            legacy_root=ROOT / "evaluations/results/diagnosis-v15-h01-h20-live-20261026-01",
            cases=BASE / "cases.json",
            case_id="H01",
            group="agent",
        )
        try:
            await diagnosis_probe.run(args)
        except ValueError:
            rows.append(
                {
                    "entrypoint": "used_input_probe_cli",
                    "rejected_before_credentials": True,
                    "reason": "fresh_formal_packet_cannot_be_smuggled_as_used_probe",
                }
            )
        else:
            raise AssertionError("Probe entry accepted new formal input")
    assert all(v == 0 for v in counters.values()), counters
    return {"rows": rows, "counters": counters}


def main():
    attempts = []

    def no_network(event, args):
        if event == "socket.connect":
            attempts.append(event)
            raise AssertionError("Preparation controls forbid all network")

    sys.addaudithook(no_network)
    baseline = verify(BASE)
    assert baseline["local_preparation_verified"] and not baseline["paid_admission"]
    rows = []
    for mode in MODES:
        rows.append(copied_test(mode))
        print(mode, "REJECTED" if rows[-1]["rejected"] else "SURVIVED", flush=True)
    entries = asyncio.run(denied_entry_checks())
    result = {
        "baseline_passed": True,
        "mutations": rows,
        "all_rejected": all(r["rejected"] for r in rows),
        "entrypoint_controls": entries,
        "network_attempts": len(attempts),
        "provider_requests": 0,
        "fresh_formal_case_executions": 0,
    }
    save(BASE / "control-results.json", result)
    assert result["all_rejected"] and not attempts, result
    print(json.dumps({k: v for k, v in result.items() if k != "mutations"}))


if __name__ == "__main__":
    main()
