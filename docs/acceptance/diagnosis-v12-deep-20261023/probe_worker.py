"""Independent local acceptance probes. Only executed development fixtures.

No frozen v12 holdout execution, no credentials, no real model. Full Worker,
evidence, task ledger and exports retained; expectations are literal author
requirements, not the production request parser or current automatic grade.
"""

import argparse
import asyncio
import json
from pathlib import Path

from app.commerce.answer_contract import request_contract
from app.diagnosis import planner_v12
from evaluations.diagnosis_cases import Case, business_input, load_cases, reference_answer
from evaluations.diagnosis_grading import grade
from evaluations.diagnosis_runtime import (
    admit,
    collect,
    execute,
    export_artifacts,
    new_task,
    sandbox,
)
from tests.test_diagnosis_graph import tool_proposal
from tests.test_diagnosis_model_calls import ScriptedDecisionModel

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True)
OUT = parser.parse_args().output
OUT.mkdir(parents=True, exist_ok=False)
CASES = {c.case_id: c for c in load_cases()[0] if c.split != "holdout"}


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


async def probe(
    name,
    query,
    proposals,
    *,
    scope=None,
    metric_obligations=(),
    expected_scope=None,
    category=False,
    three=False,
    expected_terminal=None,
    max_model_calls=12,
):
    raw = CASES["D01"].model_dump(mode="json")
    raw["query"] = query
    if scope:
        raw["scope_skus"] = scope
    if three:
        p = raw["sales"]["products"][0]
        raw["sales"]["products"].append({**p, "sku": "C", "name": "第三商品"})
        raw["sales"]["daily_sales"].extend(
            [{**r, "sku": "C"} for r in list(raw["sales"]["daily_sales"]) if r["sku"] == "A"]
        )
        raw["scope_skus"].append("C")
    case = Case.model_validate(raw)
    with sandbox(diagnosis_runtime="v12", max_model_calls=max_model_calls) as (
        settings,
        repo,
        user,
        other,
    ):
        assert not settings.openai_api_key and settings.app_env == "test"
        conv, bundle, gate = admit(repo, user, other, case)
        assert bundle, gate
        task = new_task(repo, user, conv, bundle, query, "agent")
        model = ScriptedDecisionModel(*proposals)
        await execute(repo, settings, model=model)
        run = collect(repo, task["id"], "agent")
        directory = OUT / name
        directory.mkdir(exist_ok=True)
        save(directory / "business-input.json", business_input(case))
        save(directory / "bundle.json", bundle)
        save(directory / "agent.json", run)
        export_artifacts(repo, task["id"], directory / "artifacts")
        report = run["task"]["report"] or {}
        facts = report.get("facts", [])
        required_scope = expected_scope or case.scope_skus
        missing = [
            f"{sku}/{metric}/{part}"
            for sku in required_scope
            for metric in metric_obligations
            for part in ("current", "comparison", "delta")
            if not any(
                f["entity_id"] == sku
                and f["metric_key"] in (f"{metric}.{part}", f"component.{sku}.{metric}.{part}")
                for f in facts
            )
        ]
        category_facts = [f for f in facts if f["entity_type"] == "category"]
        actual_scope = report.get("scope", {}).get("skus")
        accepted = (
            bool(run["task"].get("validation", {}).get("passed"))
            if run["task"].get("validation")
            else False
        )
        outcome = {
            "probe": name,
            "task_status": run["task"]["status"],
            "error_code": run["task"].get("error_code"),
            "stop_reason": report.get("stop_reason"),
            "program_validation_passed": accepted,
            "actual_scope": actual_scope,
            "expected_scope": required_scope,
            "missing_items": missing,
            "category_fact_count": len(category_facts),
            "model_stub_calls": len(model.requests),
            "tool_calls": run["task"]["metrics"]["tool_calls"],
            "open_questions": report.get("open_questions"),
            "parser": request_contract(query),
            "computed_gaps": planner_v12.missing_coverage(
                query,
                run["evidence"],
                report.get("scope", {"skus": case.scope_skus, "objective": None}),
            ),
            "new_provider_requests": 0,
        }
        ok = (
            accepted
            and not missing
            and actual_scope == required_scope
            and (not category or bool(category_facts))
        )
        if expected_terminal:
            ok = ok and report.get("stop_reason") == expected_terminal
        outcome["independent_acceptance_passed"] = ok
        save(directory / "independent-verdict.json", outcome)
        print(
            json.dumps(
                {
                    k: outcome[k]
                    for k in (
                        "probe",
                        "task_status",
                        "stop_reason",
                        "program_validation_passed",
                        "independent_acceptance_passed",
                        "model_stub_calls",
                    )
                }
            ),
            flush=True,
        )
        return outcome


async def main():
    outcomes = []
    for name, query in [
        ("literal-control", "请直接列出本期与比较期的净销售额及差额。"),
        ("excluded-sku", "比较除商品 A 以外商品的两期净销售额及差额。"),
    ]:
        outcomes.append(
            await probe(
                name,
                query,
                [],
                expected_scope=["B"] if name == "excluded-sku" else None,
                expected_terminal="target_covered",
            )
        )
    proposals = [
        tool_proposal("rank_contributors", {"dimension": "sku"}),
        tool_proposal("analyze_refunds", {}),
    ]
    outcomes.append(
        await probe(
            "component-control",
            "逐商品列出支付金额、退款金额和净销售额的本期值、比较期值及差额。",
            proposals,
            metric_obligations=("gross_revenue", "refund_amount", "net_revenue"),
            expected_terminal="target_covered",
        )
    )
    outcomes.append(
        await probe(
            "component-synonym",
            "请对每个授权商品分别列出支付金额、退款金额和净销售额的本期值、比较期值及差额。",
            [tool_proposal("analyze_refunds", {})],
            metric_obligations=("gross_revenue", "refund_amount", "net_revenue"),
            expected_terminal="target_covered",
        )
    )
    outcomes.append(
        await probe(
            "per-sku-counts",
            "逐商品列出本期与比较期的支付件数、退款件数及差额。",
            [tool_proposal("analyze_refunds", {})],
            metric_obligations=("paid_units", "refunded_units"),
            expected_terminal="target_covered",
        )
    )
    query = "按品类核对两期净销售额的变化贡献并对账。"
    outcomes.append(
        await probe(
            "category-control",
            query,
            [tool_proposal("rank_contributors", {"dimension": "category"})],
            category=True,
            expected_terminal="target_covered",
        )
    )
    outcomes.append(
        await probe(
            "wrong-contribution-dimension",
            query,
            [tool_proposal("rank_contributors", {"dimension": "sku"})],
            category=True,
            expected_terminal="target_covered",
        )
    )
    outcomes.append(
        await probe(
            "partial-budget-three-skus",
            "逐商品列出两期支付金额、退款金额和净销售额及差额。",
            [tool_proposal("analyze_refunds", {})],
            three=True,
            max_model_calls=1,
            expected_terminal="model_budget",
        )
    )
    # The original development category expectation checks numeric truth and
    # tool presence. Preserve its result separately from literal acceptance.
    for name in ("category-control", "wrong-contribution-dimension"):
        directory = OUT / name
        raw = CASES["D13"].model_dump(mode="json")
        raw.update(json.loads((directory / "business-input.json").read_bytes()))
        case = Case.model_validate(raw)
        raw["expected"].update(reference_answer(case))
        case = Case.model_validate(raw)
        result = grade(
            case,
            json.loads((directory / "bundle.json").read_bytes()),
            json.loads((directory / "agent.json").read_bytes()),
        )
        save(directory / "automatic-grade.json", result)

    save(
        OUT / "worker-probes.json",
        {"new_provider_requests": 0, "fresh_formal_packet_executions": 0, "probes": outcomes},
    )


if __name__ == "__main__":
    asyncio.run(main())
