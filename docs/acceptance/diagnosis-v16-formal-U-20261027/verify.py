"""Metadata-only U packet preflight; a PASS never grants paid admission."""

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

from prepare import BLOCKERS, ROOT, module, read, save, slots

from evaluations.diagnosis import implementation_manifest
from evaluations.diagnosis_budget import critical_failure, prior_spend
from evaluations.diagnosis_cases import load_cases
from evaluations.diagnosis_plan import historical_question_sources, input_hash, validate_cases
from evaluations.diagnosis_probe import (
    audit_probe,
    require_prior_review,
    validate_probe_http_budget,
)
from evaluations.diagnosis_probe_accounting import HOLD, LIMIT, file_hash
from evaluations.diagnosis_reconciliation import inspect

BASE = Path(__file__).resolve().parent
PARENT = ROOT / "evaluations/results/model-probe-H07-prior-boundary-20261027-01"
REFERENCE = ROOT / "evaluations/results/diagnosis-v14-h01-h20-live-20261025-01"


def need(ok, message):
    if not ok:
        raise ValueError(message)


def metadata(directory):
    directory = Path(directory)
    cases, contract = validate_cases(directory / "cases.json")
    authored = read(directory / "answer-obligations.json")
    sem = read(directory / "semantic-contract.json")
    need(
        sem["schema_version"] == 4 and sem["author_obligations"] == authored,
        "semantic sidecar binding",
    )
    independent = module(
        "u_independent_raw_checker", ROOT / "docs/acceptance/diagnosis-v15-h01-h20/verify.py"
    )
    from evaluations.diagnosis_request_checks_v2 import expected_entities, requested_dimensions

    totals = {
        "raw_matrix_items": 0,
        "scope_matrix_items": 0,
        "daily_items": 0,
        "contribution_groups": 0,
    }
    coverage = []
    for c, a in zip(cases, authored["cases"], strict=True):
        raw = c.model_dump(mode="json")
        selected = (
            [c.harness.clarification_choice]
            if c.harness.clarification_choice
            else expected_entities(c.query, c.scope_skus, raw["sales"]["products"])
        )
        need(
            a["case_id"] == c.case_id and a["query"] == c.query and a["final_skus"] == selected,
            "author scope/question",
        )
        need(
            requested_dimensions(c.query)
            == ([a["contribution_dimension"]] if a["contribution_dimension"] else []),
            "author contribution dimension",
        )
        need(
            a["required_assertions"] == c.expected.required_assertions
            and a["expected_stop_reasons"] == c.expected.stop_reasons,
            "author assertions/stop",
        )
        for table in ["raw_matrix", "scope_matrix"]:
            totals[table + "_items"] += len(a[table])
            for item in a[table]:
                members = selected if item["sku"] == ",".join(sorted(selected)) else [item["sku"]]
                spans = (
                    ["period", "comparison_period"]
                    if item["part"] == "delta"
                    else ["period" if item["part"] == "current" else "comparison_period"]
                )
                values = [independent.rows(raw, members, raw[s]) for s in spans]
                applicable = all(cov["expected_records"] > 0 for _, cov in values)
                complete = applicable and all(cov["complete"] for _, cov in values)
                value = (
                    (
                        values[0][0][item["metric"]]
                        - (values[1][0][item["metric"]] if len(values) == 2 else 0)
                    )
                    if complete
                    else None
                )
                count = item["metric"].endswith("units")
                need(
                    item["raw_integer"] == value
                    and item["unit"] == ("count" if count else "CNY")
                    and item["scale"] == (1 if count else 100),
                    "independent raw amount/unit",
                )
                need(
                    item["status"]
                    == (
                        "required"
                        if complete
                        else "not_applicable"
                        if not applicable
                        else "missing_source"
                    ),
                    "raw availability",
                )
        expected_daily = set()
        if sem["cases"][c.case_id]["dimension"] == "each_sku_daily":
            for sku in selected:
                for span in ["period", "comparison_period"]:
                    day, last = (date.fromisoformat(raw[span][k]) for k in ["start", "end"])
                    while day <= last:
                        expected_daily.add((sku, span, str(day)))
                        day += timedelta(days=1)
        need(
            expected_daily == {(x["sku"], x["period"], x["day"]) for x in a["daily_matrix"]},
            "complete daily obligation inventory",
        )
        for item in a["daily_matrix"]:
            amounts, cov = independent.rows(
                raw, [item["sku"]], {"start": item["day"], "end": item["day"]}
            )
            need(
                amounts["net_revenue"] == item["net_cents"] and cov == item["coverage"],
                "independent daily rows",
            )
            totals["daily_items"] += 1
        contributions = a["raw_contributions"]
        if a["contribution_dimension"]:
            dim = a["contribution_dimension"]
            groups = {}
            for p in raw["sales"]["products"]:
                if p["sku"] in selected:
                    groups.setdefault(p[dim], []).append(p["sku"])
            need(
                {x["entity"]: x["skus"] for x in contributions} == dict(sorted(groups.items())),
                "complete contribution inventory",
            )
        else:
            need(not contributions, "unexpected contribution obligations")
        for item in contributions:
            now, cc = independent.rows(raw, item["skus"], raw["period"])
            prior, pc = independent.rows(raw, item["skus"], raw["comparison_period"])
            value = (
                now["net_revenue"] - prior["net_revenue"]
                if cc["complete"] and pc["complete"]
                else None
            )
            need(item["delta_cents"] == value, "independent contribution rows")
            totals["contribution_groups"] += 1
        for members, key in [
            (selected, "selected_aggregate_expected"),
            (c.scope_skus, "authorized_aggregate_expected"),
        ]:
            now, cc = independent.rows(raw, members, raw["period"])
            prior, pc = independent.rows(raw, members, raw["comparison_period"])
            comparable = all(v["complete"] and v["expected_records"] > 0 for v in [cc, pc])
            expect = {
                "net_cents": now["net_revenue"],
                "comparison_net_cents": prior["net_revenue"],
                "comparable": comparable,
                "delta_cents": now["net_revenue"] - prior["net_revenue"] if comparable else None,
            }
            need(a[key] == expect, "independent aggregate oracle")
        coverage.append({"case_id": c.case_id, "current": cc, "comparison": pc})
    c7 = next(c for c in coverage if c["case_id"] == "H07")
    need(
        c7["comparison"]["expected_records"] == c7["comparison"]["missing_records"] == 0,
        "H07 must prove pre-activation, not missing rows",
    )
    c6 = next(c for c in coverage if c["case_id"] == "H06")
    need(
        any(c6[span]["missing_records"] > 0 for span in ["current", "comparison"]),
        "H06 must retain real missing rows",
    )
    schedule = read(directory / "execution-plan.json")
    need(
        schedule["slots"] == slots(cases)
        and schedule["total_slots"] == 96
        and schedule["all_slots_unexecuted"] is True,
        "all 96 frozen sequential slots",
    )
    need(
        schedule["require_sequential_launch"]
        and schedule["no_automatic_resume_or_retry"]
        and schedule["no_sdk_retries"]
        and schedule["no_fallback_extra_calls"]
        and not schedule["release_eligible"],
        "first-error stop policy",
    )
    need(
        set(schedule["stop_reasons"])
        == {
            "protocol_error",
            "proposal_or_tool_rejected",
            "call_error",
            "automatic_grade_failed",
            "manual_review_failed",
            "usage_unknown",
            "budget_insufficient",
        },
        "all failure classes stop",
    )
    for slot in schedule["slots"]:
        c = next(c for c in cases if c.case_id == slot["case_id"])
        validate_probe_http_budget(c, slot["group"], slot["max_http"])
    return {
        "contract": contract,
        "totals": totals,
        "coverage": coverage,
        "max_http_all_slots": sum(s["max_http"] for s in schedule["slots"]),
    }


def live_context(cases):
    parent = audit_probe(PARENT)
    require_prior_review(parent, implementation_manifest()[1])
    need(
        not PARENT.with_name(PARENT.name + ".budget-continuation.json").exists(),
        "budget leaf already claimed; do not reuse",
    )
    known, source_files = historical_question_sources(parent["legacy_root"])
    from app.diagnosis.inputs import digest

    observed = {}
    for p in (ROOT / "evaluations/results").rglob("business-input.json"):
        raw = read(p)
        raw.pop("query", None)
        observed[str(p.relative_to(ROOT))] = {"sha256": file_hash(p), "input_hash": digest(raw)}
    known.update(r["input_hash"] for r in observed.values())
    need(not {input_hash(c) for c in cases} & known, "new packet overlaps known input")
    refmeta = read(REFERENCE / "run.json")
    ref = prior_spend(
        REFERENCE,
        provider="DeepSeek",
        endpoint="https://api.deepseek.com",
        model="deepseek-flash",
        token_limit=refmeta["max_total_tokens"],
        require_stopped=False,
    )
    summary = read(REFERENCE / "summary.json")
    need(
        summary["executed_runs"] == 96 and summary["stopped"] is None,
        "reference must be a complete same-sized round",
    )
    observed_tokens = ref["known_tokens"] - refmeta["prior_paid_budget"]["known_tokens"]
    occupied = parent["known_tokens"] + parent["frozen_unknown_tokens"]
    budget = {
        "cumulative_limit": LIMIT,
        "known_tokens": parent["known_tokens"],
        "legacy_unknown_hold_tokens": parent["frozen_unknown_tokens"],
        "effective_occupied_tokens": occupied,
        "remaining_tokens": LIMIT - occupied,
        "reference_round_new_tokens": observed_tokens,
        "reference_round": str(REFERENCE.relative_to(ROOT)),
        "single_request_upper_bound": HOLD,
        "estimated_round_plus_final_request_capacity": occupied + observed_tokens + HOLD,
        "estimated_margin": LIMIT - occupied - observed_tokens - HOLD,
        "double_reference_stress_margin": LIMIT - occupied - 2 * observed_tokens - HOLD,
        "estimate_not_completion_guarantee": True,
    }
    need(
        budget["estimated_margin"] >= 0
        and parent["known_tokens"] == 11049569
        and parent["frozen_unknown_tokens"] == 1051576,
        "frozen accounting totals",
    )
    history = {
        "prior_source_files": source_files,
        "observed_business_inputs": observed,
        "distinct_known_data_hashes": len(known),
        "known_input_hashes": sorted(known),
    }
    return parent, budget, history


def verify(directory):
    directory = Path(directory)
    meta = metadata(directory)
    m = read(directory / "manifest.json")
    need(
        m["schema_version"] == "u-formal-preparation-v1"
        and m["status"] == "PREPARED_LOCAL_ONLY_PAID_ADMISSION_BLOCKED",
        "preparation status must not grant paid permission",
    )
    need(
        m["paid_admission"] is False
        and m["release_eligible"] is False
        and m["blockers"] == BLOCKERS,
        "paid blockers cannot be silently removed",
    )
    need(
        m["source_manifest"] == implementation_manifest()[0]
        and m["implementation_sha256"] == implementation_manifest()[1],
        "implementation source drift",
    )
    need(
        set(m["packet_files_sha256"])
        == {
            "cases.json",
            "answer-obligations.json",
            "semantic-contract.json",
            "execution-plan.json",
            "provenance.json",
            "packet-contract.json",
            "history-provenance.json",
            "budget-precheck.json",
            "prepare.py",
            "verify.py",
        },
        "complete frozen file inventory",
    )
    for name, checksum in m["packet_files_sha256"].items():
        need(file_hash(directory / name) == checksum, "packet hash changed")
    need(meta == m["metadata"], "frozen metadata drift")
    cases = load_cases(directory / "cases.json")[0]
    parent, budget, history = live_context(cases)
    need(
        m["parent_accounting"] == parent
        and m["budget"] == budget
        and read(directory / "history-provenance.json") == history,
        "accounting or historical input provenance drift",
    )
    need(
        inspect(Path(parent["legacy_root"]))["status"] == "RECONCILIATION_BLOCKED",
        "do not fabricate old reconciliation",
    )
    need(
        not critical_failure(
            {
                "automatic_passed": False,
                "assertions": {
                    "expected_outcome": False,
                    "bounded_execution": True,
                    "facts_match_raw_records": True,
                },
            }
        ),
        "old stop-policy assumption changed; re-audit entrypoint",
    )
    return {
        "local_preparation_verified": True,
        "paid_admission": False,
        "blockers": BLOCKERS,
        "fresh_case_graph_tool_model_executions": 0,
        "provider_requests": 0,
        "metadata": meta,
        "budget": budget,
    }


def freeze():
    cases = load_cases(BASE / "cases.json")[0]
    meta = metadata(BASE)
    parent, budget, history = live_context(cases)
    save(BASE / "history-provenance.json", history)
    save(BASE / "budget-precheck.json", budget)
    names = [
        "cases.json",
        "answer-obligations.json",
        "semantic-contract.json",
        "execution-plan.json",
        "provenance.json",
        "packet-contract.json",
        "history-provenance.json",
        "budget-precheck.json",
        "prepare.py",
        "verify.py",
    ]
    files, sha = implementation_manifest()
    save(
        BASE / "manifest.json",
        {
            "schema_version": "u-formal-preparation-v1",
            "status": "PREPARED_LOCAL_ONLY_PAID_ADMISSION_BLOCKED",
            "implementation_sha256": sha,
            "source_manifest": files,
            "metadata": meta,
            "parent_accounting": parent,
            "budget": budget,
            "packet_files_sha256": {n: file_hash(BASE / n) for n in names},
            "blockers": BLOCKERS,
            "paid_admission": False,
            "release_eligible": False,
            "fresh_case_executions": 0,
        },
    )
    result = verify(BASE)
    save(BASE / "local-preflight.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "metadata"}))


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("--freeze", action="store_true")
    cli.add_argument("--directory", type=Path, default=BASE)
    args = cli.parse_args()
    if args.freeze:
        freeze()
    else:
        print(json.dumps(verify(args.directory)))
