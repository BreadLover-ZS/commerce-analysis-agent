"""Metadata and raw-integer preflight only; no Task, tool, model, or network."""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from app.diagnosis.inputs import digest  # noqa: E402
from evaluations.diagnosis_plan import check, input_hash, validate_cases  # noqa: E402
from evaluations.diagnosis_request_checks_v2 import expected_entities, requested_dimensions  # noqa: E402


def need(ok, message):
    if not ok:
        raise ValueError(message)


def rows(case, skus, span):
    selected = [
        r
        for r in case["sales"]["daily_sales"]
        if r["sku"] in skus and span["start"] <= r["day"] <= span["end"]
    ]
    expected = sum(
        max(
            0,
            (
                date.fromisoformat(span["end"])
                - max(date.fromisoformat(span["start"]), date.fromisoformat(p["active_from"]))
            ).days
            + 1,
        )
        for p in case["sales"]["products"]
        if p["sku"] in skus
    )
    sums = {
        key: sum(r[field] for r in selected)
        for key, field in [
            ("gross_revenue", "revenue_cents"),
            ("refund_amount", "refund_cents"),
            ("paid_units", "paid_units"),
            ("refunded_units", "refunded_units"),
        ]
    }
    sums["net_revenue"] = sums["gross_revenue"] - sums["refund_amount"]
    return sums, {
        "expected_records": expected,
        "observed_records": len(selected),
        "missing_records": expected - len(selected),
        "complete": expected == len(selected),
    }


def verify(directory):
    directory = Path(directory)
    frozen = check(directory)
    cases, contract = validate_cases(directory / "cases.json")
    answer = json.loads((directory / "answer-obligations.json").read_bytes())
    semantic = json.loads((directory / "semantic-contract.json").read_bytes())
    need(
        semantic["schema_version"] == 2 and semantic["author_obligations"] == answer,
        "sidecar mismatch",
    )
    totals = {"required_items": 0, "daily_items": 0, "contribution_groups": 0}
    for case, author in zip(cases, answer["cases"], strict=True):
        raw = case.model_dump(mode="json")
        selected = author["final_skus"]
        expected_scope = (
            [case.harness.clarification_choice]
            if case.harness.clarification_choice
            else expected_entities(case.query, case.scope_skus, raw["sales"]["products"])
        )
        need(selected == expected_scope, "author entity scope mismatch")
        expected_dims = requested_dimensions(case.query)
        need(
            expected_dims
            == ([author["contribution_dimension"]] if author["contribution_dimension"] else []),
            "author dimension mismatch",
        )
        need(
            author["case_id"] == case.case_id and author["query"] == case.query,
            "case/question mismatch",
        )
        need(author["expected_stop_reasons"] == case.expected.stop_reasons, "stop mismatch")
        for item in author["raw_matrix"] + author["scope_matrix"]:
            members = selected if item["sku"] == ",".join(sorted(selected)) else [item["sku"]]
            parts = (
                ["period", "comparison_period"]
                if item["part"] == "delta"
                else ["period" if item["part"] == "current" else "comparison_period"]
            )
            values = [rows(raw, members, raw[p]) for p in parts]
            applicable = all(c["expected_records"] > 0 for _, c in values)
            complete = applicable and all(c["complete"] for _, c in values)
            value = (
                (
                    values[0][0][item["metric"]] - values[1][0][item["metric"]]
                    if len(values) == 2
                    else values[0][0][item["metric"]]
                )
                if complete
                else None
            )
            count = item["metric"].endswith("units")
            need(
                item["raw_integer"] == value
                and item["unit"] == ("count" if count else "CNY")
                and item["scale"] == (1 if count else 100),
                "raw amount/count mismatch",
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
                "availability mismatch",
            )
            totals["required_items"] += 1
        for item in author["daily_matrix"]:
            value, cov = rows(raw, [item["sku"]], {"start": item["day"], "end": item["day"]})
            need(
                value["net_revenue"] == item["net_cents"] and cov == item["coverage"],
                "daily mismatch",
            )
            totals["daily_items"] += 1
        for item in author["raw_contributions"]:
            now, _ = rows(raw, item["skus"], raw["period"])
            before, _ = rows(raw, item["skus"], raw["comparison_period"])
            need(
                item["delta_cents"] == now["net_revenue"] - before["net_revenue"],
                "contribution mismatch",
            )
            totals["contribution_groups"] += 1
        now, cv = rows(raw, selected, raw["period"])
        prior, pv = rows(raw, selected, raw["comparison_period"])
        comparable = all(c["complete"] and c["expected_records"] > 0 for c in [cv, pv])
        need(
            author["selected_aggregate_expected"]
            == {
                "net_cents": now["net_revenue"],
                "comparison_net_cents": prior["net_revenue"],
                "comparable": comparable,
                "delta_cents": now["net_revenue"] - prior["net_revenue"] if comparable else None,
            },
            "aggregate mismatch",
        )
    executed = set()
    checked = 0
    for path in (ROOT / "evaluations/results").rglob("business-input.json"):
        old = json.loads(path.read_bytes())
        old.pop("query", None)
        executed.add(digest(old))
        checked += 1
    need(not {input_hash(c) for c in cases} & executed, "fresh packet was executed")
    return {
        **frozen,
        **totals,
        "author_obligations_verified": True,
        "independent_integer_rows_verified": True,
        "executed_inputs_checked": checked,
        "fresh_input_overlap": 0,
        "formal_packet_executions": 0,
        "provider_requests_this_check": 0,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path(__file__).resolve().parent)
    attempts = []

    def no_network(event, args):
        if event == "socket.connect":
            attempts.append(event)
            raise RuntimeError("network forbidden")

    sys.addaudithook(no_network)
    result = verify(parser.parse_args().directory)
    result["network_attempts"] = len(attempts)
    print(json.dumps(result, ensure_ascii=False, indent=2))
