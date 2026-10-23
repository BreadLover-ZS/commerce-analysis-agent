"""Literal v13 repair acceptance, executed development inputs only, no provider."""

import argparse
import asyncio
import copy
import json
from pathlib import Path

from app.diagnosis.reporting import validate_report
from evaluations.diagnosis_cases import Case, business_input, load_cases
from evaluations.diagnosis_request_checks import completion_checks
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
from tests.test_diagnosis_v3 import stop

AMOUNTS = "请对每个授权商品分别列出支付金额、退款金额和净销售额的本期值、比较期值及差额。"
COUNTS = "逐商品列出本期与比较期的支付件数、退款件数及差额。"
CATEGORY = "按品类核对两期净销售额的变化贡献并对账。"
SKU = "按商品核对两期净销售额的变化贡献并对账。"


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def tool(name, **arguments):
    return tool_proposal(name, arguments)


async def probe(
    out, name, query, proposals, *, selected=None, metrics=(), partial=False, three=False
):
    base = next(c for c in load_cases()[0] if c.case_id == "D01")
    raw = base.model_dump(mode="json")
    raw["query"] = query
    if three:
        raw["sales"]["products"].append({**raw["sales"]["products"][0], "sku": "C"})
        raw["sales"]["daily_sales"].extend(
            [{**r, "sku": "C"} for r in list(raw["sales"]["daily_sales"]) if r["sku"] == "A"]
        )
        raw["scope_skus"].append("C")
    case = Case.model_validate(raw)
    selected = selected or case.scope_skus
    with sandbox(diagnosis_runtime="v13", max_model_calls=1 if partial else 12) as (
        settings,
        repo,
        user,
        other,
    ):
        assert not settings.openai_api_key
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, query, "agent")
        model = ScriptedDecisionModel(*proposals)
        await execute(repo, settings, model=model)
        run = collect(repo, task["id"], "agent")
        directory = out / name
        directory.mkdir()
        save(directory / "business-input.json", business_input(case))
        save(directory / "bundle.json", bundle)
        save(directory / "agent.json", run)
        export_artifacts(repo, task["id"], directory / "artifacts")
        report = run["task"]["report"]
        assert report is not None, run["task"]
        checks = {
            "program_validation": run["task"]["validation"]["passed"],
            "literal_scope": report["scope"]["skus"] == selected,
            "literal_stop": report["stop_reason"]
            == ("model_budget" if partial else "target_covered"),
            "full_gap_counts": report["gap_summary"]
            == {
                "total": len(report["open_question_details"]),
                "displayed": len(report["open_questions"]),
                "omitted": len(report["open_question_details"]) - len(report["open_questions"]),
            },
            **completion_checks(
                business_input(case), report, [e["payload"] for e in run["evidence"]], selected
            ),
        }
        required = [
            (sku, metric, part)
            for sku in selected
            for metric in metrics
            for part in ("current", "comparison", "delta")
        ]
        missing = [
            i
            for i in required
            if not any(
                f["entity_id"] == i[0]
                and f["metric_key"] in (f"{i[1]}.{i[2]}", f"component.{i[0]}.{i[1]}.{i[2]}")
                and f["unit"] == ("count" if i[1].endswith("units") else "CNY")
                for f in report["facts"]
            )
        ]
        checks["literal_component_facts"] = len(missing) == (len(required) if partial else 0)
        if partial:
            checks["partial_gap_present"] = bool(report["open_question_details"])
            forged = copy.deepcopy(report)
            forged.update(
                stop_reason="target_covered",
                open_questions=[],
                open_question_details=[],
                gap_summary={"total": 0, "displayed": 0, "omitted": 0},
            )
            checks["forged_completion_rejected"] = not validate_report(
                forged, run["task"], bundle, run["evidence"], run["steps"]
            )["passed"]
            independent = completion_checks(
                business_input(case), forged, [e["payload"] for e in run["evidence"]], selected
            )
            checks["independent_forgery_rejected"] = not all(independent.values())
        if three:
            checks["literal_27_20_7"] = report["gap_summary"] == {
                "total": 27,
                "displayed": 20,
                "omitted": 7,
            }
        verdict = {
            "probe": name,
            "task_id": task["id"],
            "checks": checks,
            "passed": all(checks.values()),
            "stop_reason": report["stop_reason"],
            "scope": selected,
            "gap_summary": report["gap_summary"],
            "missing_items": missing,
            "scripted_protocol_calls": len(model.requests),
            "new_provider_requests": 0,
        }
        save(directory / "independent-verdict.json", verdict)
        print(json.dumps(verdict, ensure_ascii=False), flush=True)
        assert verdict["passed"], verdict
        return verdict


async def main(output):
    output.mkdir(parents=True, exist_ok=False)
    results = []
    specs = [
        ("literal-control", "请直接列出本期与比较期的净销售额及差额。", [], {}),
        ("excluded-sku", "比较除商品 A 以外商品的两期净销售额及差额。", [], {"selected": ["B"]}),
        (
            "component-partial",
            AMOUNTS,
            [tool("analyze_refunds")],
            {"partial": True, "metrics": ("gross_revenue", "refund_amount", "net_revenue")},
        ),
        (
            "component-repaired",
            AMOUNTS,
            [tool("analyze_refunds"), stop(), tool("rank_contributors", top_k=1)],
            {"metrics": ("gross_revenue", "refund_amount", "net_revenue")},
        ),
        (
            "counts-partial",
            COUNTS,
            [tool("analyze_refunds")],
            {"partial": True, "metrics": ("paid_units", "refunded_units")},
        ),
        (
            "counts-repaired",
            COUNTS,
            [tool("analyze_refunds"), stop(), tool("rank_contributors", top_k=1)],
            {"metrics": ("paid_units", "refunded_units")},
        ),
        ("category-control", CATEGORY, [tool("rank_contributors", dimension="category")], {}),
        (
            "wrong-category-partial",
            CATEGORY,
            [tool("rank_contributors", dimension="sku")],
            {"partial": True},
        ),
        (
            "wrong-category-repaired",
            CATEGORY,
            [
                tool("rank_contributors", dimension="sku"),
                stop(),
                tool("rank_contributors", dimension="category"),
            ],
            {},
        ),
        (
            "wrong-sku-partial",
            SKU,
            [tool("rank_contributors", dimension="category")],
            {"partial": True},
        ),
        (
            "wrong-sku-repaired",
            SKU,
            [
                tool("rank_contributors", dimension="category"),
                stop(),
                tool("rank_contributors", dimension="sku"),
            ],
            {},
        ),
        (
            "all-27-gaps",
            "逐商品列出两期支付金额、退款金额和净销售额及差额。",
            [tool("analyze_refunds")],
            {
                "partial": True,
                "three": True,
                "metrics": ("gross_revenue", "refund_amount", "net_revenue"),
            },
        ),
        (
            "equivalent-count-path",
            "逐商品列出两期支付件数及差额。",
            [
                tool("compare_periods", metric="paid_units", filters={"skus": [sku]})
                for sku in ["A", "B"]
            ],
            {"metrics": ("paid_units",)},
        ),
    ]
    for name, query, proposals, options in specs:
        results.append(await probe(output, name, query, proposals, **options))
    save(
        output / "summary.json",
        {
            "probes": results,
            "passed": all(r["passed"] for r in results),
            "new_provider_requests": 0,
            "formal_packet_executions": 0,
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main(parser.parse_args().output))
