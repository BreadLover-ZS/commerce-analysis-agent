"""Plan-only root cause probes against v14 and used receipts. No repair or paid calls."""

import argparse
import asyncio
import copy
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from app.diagnosis.schemas_v2 import StepProposalV2  # noqa: E402
from app.diagnosis.validation_v2 import valid_interpretations  # noqa: E402
from evaluations.diagnosis import implementation_manifest  # noqa: E402
from evaluations.diagnosis_cases import load_cases  # noqa: E402
from evaluations.diagnosis_runtime import admit, collect, execute, new_task, sandbox  # noqa: E402
from tests.test_diagnosis_model_calls import ScriptedDecisionModel  # noqa: E402

USED = ROOT / "evaluations/results/diagnosis-v14-h01-h20-live-20261025-01"


def write(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)
        stream.write("\n")


def amount_identity(record, business):
    """Experimental numeric witness only, not an application or grader implementation."""
    skus = sorted(business["scope_skus"])
    entity = ",".join(skus)
    facts = record["task"]["report"]["facts"]
    values = {}
    for part, period_key in [("current", "period"), ("comparison", "comparison_period")]:
        span = business[period_key]
        rows = [
            r
            for r in business["sales"]["daily_sales"]
            if r["sku"] in skus and span["start"] <= r["day"] <= span["end"]
        ]
        # Fixture calendar is one day; this plan probe deliberately supports only that known receipt.
        if span["start"] != span["end"] or len(rows) != len(skus):
            return False
        gross = sum(r["revenue_cents"] for r in rows)
        refund = sum(r["refund_cents"] for r in rows)
        values[part] = {
            "gross_revenue": gross,
            "refund_amount": refund,
            "net_revenue": gross - refund,
        }
    values["delta"] = {m: values["current"][m] - values["comparison"][m] for m in values["current"]}
    for part in ["current", "comparison", "delta"]:
        for metric in values[part]:
            matching = [
                f
                for f in facts
                if f["metric_key"] == f"{metric}.{part}" and f["entity_id"] == entity
            ]
            if not matching or not any(
                f["unit"] == "CNY"
                and Decimal(f["value"]) * 100 == values[part][metric]
                and f["period"]
                == business["comparison_period" if part == "comparison" else "period"]
                for f in matching
            ):
                return False
    return (
        values["delta"]["net_revenue"]
        == values["delta"]["gross_revenue"] - values["delta"]["refund_amount"]
    )


async def main(out):
    out.mkdir(parents=True, exist_ok=False)
    source = implementation_manifest()[1]
    cases = {c.case_id: c for c in load_cases(USED / "cases-source.json")[0]}
    checks = []
    sockets = []

    def forbid_network(event, args):
        if event == "socket.connect":
            sockets.append(event)
            raise RuntimeError("plan probe network forbidden")

    sys.addaudithook(forbid_network)

    def check(name, passed, evidence):
        checks.append({"check": name, "passed": bool(passed), "evidence": evidence})
        assert passed, (name, evidence)

    for case_id, receipt_name in [("H02", "H02-3/agent.json"), ("H17", "H17-2/additional.json")]:
        original = json.loads((USED / receipt_name).read_text())
        proposals = [s["proposal"] for s in original["steps"]]
        bad = next(p for p in proposals if p.get("rule_interpretations"))
        StepProposalV2.model_validate(bad)
        check(
            case_id + "_schema_accepts_invalid_semantic_annotation",
            not valid_interpretations(bad["rule_interpretations"], original["evidence"]),
            "structurally accepted; all quote references unbound",
        )
        for scenario in ["original_sequence", "clean_proposal_control"]:
            # Counterfactual clean model output proves tool calculability. Production must never strip/rewrite an unsafe proposal and execute it.
            script = copy.deepcopy(proposals)
            if scenario == "clean_proposal_control":
                first = copy.deepcopy(bad)
                first["rule_interpretations"] = []
                script = [first]
                if case_id == "H17":
                    script.append(
                        {
                            "action": "stop",
                            "question": "明确缺提前期边界",
                            "stop_reason": "evidence_gap",
                            "rule_interpretations": [],
                        }
                    )
            with sandbox(diagnosis_runtime="v14") as (settings, repo, user, other):
                assert not settings.openai_api_key
                conv, bundle, _ = admit(repo, user, other, cases[case_id])
                task = new_task(repo, user, conv, bundle, cases[case_id].query, "agent")
                model = ScriptedDecisionModel(*script)
                await execute(repo, settings, model=model)
                run = collect(repo, task["id"], "agent")
                report = run["task"]["report"]
                check(
                    case_id + "_" + scenario + "_validation",
                    run["task"]["validation"]["passed"],
                    run["task"]["validation"],
                )
                wanted = (
                    "invalid_decision"
                    if scenario == "original_sequence"
                    else "target_covered"
                    if case_id == "H02"
                    else "evidence_gap"
                )
                check(
                    case_id + "_" + scenario + "_stop",
                    report["stop_reason"] == wanted,
                    report["stop_reason"],
                )
                if scenario == "original_sequence":
                    rejected = [s for s in run["steps"] if s["status"] == "REJECTED"]
                    check(
                        case_id + "_rejection_feedback_has_no_typed_correction",
                        bool(rejected)
                        and all("correction" not in s["assessment"] for s in rejected),
                        [s["assessment"] for s in rejected],
                    )
                else:
                    needed = "analyze_refunds" if case_id == "H02" else "inventory_risk"
                    check(
                        case_id + "_clean_control_tool_executes",
                        any(e["payload"]["tool"] == needed for e in run["evidence"]),
                        needed,
                    )
                write(out / f"{case_id}-{scenario}.json", run)
    original = json.loads((USED / "H03-1/agent.json").read_text())
    business = json.loads((USED / "H03-1/business-input.json").read_text())
    check(
        "H03_numeric_witness_present_despite_capability_fail",
        amount_identity(original, business)
        and original["grading"]["assertions"]["required_capabilities"] is False,
        "nine same-scope, same-period CNY values reconcile; frozen capability still FAIL",
    )
    for mutation in [
        "wrong_value",
        "wrong_unit",
        "wrong_scope",
        "wrong_period",
        "missing_refund_comparison",
    ]:
        forged = copy.deepcopy(original)
        facts = forged["task"]["report"]["facts"]
        target = next(
            f
            for f in facts
            if f["metric_key"] == "refund_amount.comparison"
            and f["entity_id"] == ",".join(business["scope_skus"])
        )
        if mutation == "wrong_value":
            target["value"] = str(Decimal(target["value"]) + 1)
        elif mutation == "wrong_unit":
            target["unit"] = "count"
        elif mutation == "wrong_scope":
            target["entity_id"] = business["scope_skus"][0]
        elif mutation == "wrong_period":
            target["period"] = business["period"]
        else:
            facts.remove(target)
        check(
            "H03_reject_" + mutation,
            not amount_identity(forged, business),
            "scratch copy only; original receipt untouched",
        )
    case_index = {
        c["case_id"]: c for c in json.loads((USED / "cases-source.json").read_text())["cases"]
    }
    for cid in ["H06", "H07", "H17"]:
        expected = case_index[cid]["expected"]
        check(
            cid + "_gap_target_only_collision",
            expected["outcome"] == "gap"
            and expected["positive"] is True
            and expected["stop_reasons"] == ["target_covered"],
            expected,
        )
    fixed = json.loads((USED / "H03-1/fixed.json").read_text())
    payload = fixed["evidence"][0]["payload"]
    keys = [
        m["key"]
        for s in fixed["task"]["report"]["sections"]
        for c in s["claims"]
        for m in c.get("metrics", [])
    ]
    check(
        "fixed_H03_fact_projection_omits_available_prior_gross",
        payload["previous_period"]["gross_revenue"] == "1249980.00"
        and "comparison_gross_revenue" not in payload["fact_catalog"]
        and "comparison_gross_revenue" not in keys,
        "raw calculation has the amount; fact catalog and report do not",
    )
    check("network_attempts_zero", not sockets, sockets)
    check("source_unchanged", implementation_manifest()[1] == source, source)
    result = {
        "status": "PLAN_ASSUMPTIONS_CONFIRMED_ON_V14_NOT_REPAIRED",
        "checks": checks,
        "passed": len(checks),
        "failed": 0,
        "worker_tasks": 4,
        "new_provider_requests": 0,
        "network_attempts": 0,
        "implementation_sha256": source,
        "scope": "Used v14 inputs only; SDK protocol stubs and experimental numeric witness; no v15 implementation or model-quality result",
    }
    write(out / "summary.json", result)
    print(
        json.dumps(
            {
                k: result[k]
                for k in ["status", "passed", "failed", "worker_tasks", "new_provider_requests"]
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main(parser.parse_args().output))
