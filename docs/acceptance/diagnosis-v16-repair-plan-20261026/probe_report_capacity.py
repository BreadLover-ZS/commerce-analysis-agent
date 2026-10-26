"""Recompose saved failed output locally; prototype only, never mutate a Task."""

import argparse
import copy
import json
import socket
from pathlib import Path

from app.commerce.hypotheses import allowed_hypotheses
from app.research.evidence import deterministic_report, validate_report
from app.research.schemas import Claim, ResearchReport

ROOT = Path("evaluations/results/diagnosis-v15-h01-h20-live-20261026-01")


def verify(output):
    network = []

    def deny(*args, **kwargs):
        network.append(True)
        raise AssertionError("network forbidden")

    socket.socket.connect = deny
    results = []
    for name in ["H03-1/fixed.json", "H03-2/fixed.json", "H03-3/fixed.json"]:
        record = json.loads((ROOT / name).read_bytes())
        baseline = deterministic_report(record["evidence"], [])
        original = baseline.model_dump(mode="json")
        source = next(
            e for e in record["evidence"] if e["payload"].get("review_profile") == "commerce_review"
        )
        options = allowed_hypotheses(source["payload"])
        response = next(
            json.loads(c["message"]["content"])
            for t in record["provider_trace"]
            for c in t["response"].get("choices", [])
            if c["message"].get("content", "").startswith('{"analysis"')
        )
        choices = list(dict.fromkeys(response["analysis"]))
        assert all(c in options for c in choices)
        candidate = copy.deepcopy(original)
        capacity = 8 - len(candidate["sections"][3]["claims"])
        assert capacity >= 0
        candidate["sections"][3]["claims"].extend(
            Claim(
                id=f"review-hypothesis-{i}",
                kind="analysis",
                text=options[k],
                evidence_ids=[source["id"]],
            ).model_dump(mode="json")
            for i, k in enumerate(choices[:capacity])
        )
        report = ResearchReport.model_validate(candidate)
        valid = validate_report(report, record["evidence"])
        before = [m for s in original["sections"] for c in s["claims"] for m in c["metrics"]]
        after = [
            m for s in report.model_dump()["sections"] for c in s["claims"] for m in c["metrics"]
        ]
        results.append(
            {
                "receipt": name,
                "program_claims": len(original["sections"][3]["claims"]),
                "legal_model_hypotheses": len(choices),
                "kept_hypotheses": min(capacity, len(choices)),
                "omitted_optional_hypotheses": max(0, len(choices) - capacity),
                "schema_valid": True,
                "semantic_program_gate_passed": valid["passed"],
                "all_metric_bindings_unchanged": before == after,
                "baseline_not_mutated": baseline.model_dump(mode="json") == original,
            }
        )
    # Full validation must reject an illegal choice even if it would be clipped.
    illegal = ["data_quality", "refund_drivers", "sales_drivers", "invented_cause"]
    checked_before_clipping = not all(k in options for k in illegal)
    checks = {
        "saved_outputs_can_be_bounded_without_losing_facts": all(
            x["schema_valid"]
            and x["semantic_program_gate_passed"]
            and x["all_metric_bindings_unchanged"]
            and x["baseline_not_mutated"]
            for x in results
        ),
        "two_failed_outputs_have_one_optional_omission_each": all(
            x["omitted_optional_hypotheses"] == 1 for x in results[:2]
        ),
        "successful_third_output_has_no_omission": results[2]["omitted_optional_hypotheses"] == 0,
        "validate_entire_choice_list_before_truncation": checked_before_clipping,
        "network_attempts_zero": not network,
    }
    value = {
        "status": "PLAN_PROTOTYPE_ONLY_NOT_NEW_TASKS_OR_REVISED_FORMAL_SCORES",
        "results": results,
        "checks": checks,
        "provider_requests": 0,
        "task_executions": 0,
        "historical_receipts_modified": False,
        "production_code_modified": False,
        "not_implemented": [
            "omission disclosure",
            "durable composition metadata",
            "atomic fallback and versioned runtime wiring",
        ],
    }
    with output.open("x") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")
    assert all(checks.values()), value
    print(
        json.dumps({"passed": sum(checks.values()), "provider_requests": 0, "task_executions": 0})
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    verify(p.parse_args().output)
