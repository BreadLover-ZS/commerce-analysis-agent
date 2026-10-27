"""Contrast missing applicable rows with a pre-activation period; never call a provider."""

import asyncio
import copy
import json
import socket
from pathlib import Path

import httpx

from evaluations.diagnosis import implementation_manifest, live_model, write
from evaluations.diagnosis_cases import load_cases
from evaluations.diagnosis_grading import grade
from evaluations.diagnosis_probe_accounting import file_hash
from evaluations.diagnosis_runtime import ROOT, admit, collect, execute, new_task, sandbox

BASE = Path(__file__).resolve().parent


async def main():
    def forbidden(*args, **kwargs):
        raise AssertionError("Local boundary check forbids network")

    socket.socket.connect = forbidden
    source = ROOT / "docs/acceptance/diagnosis-v15-h01-h20/cases.json"
    original_hash = file_hash(source)
    case = load_cases(source, case_ids=["H07"])[0][0]
    variant = copy.deepcopy(case)
    variant.sales.products = [
        p.model_copy(update={"active_from": case.period.start}) for p in case.sales.products
    ]
    output = ROOT / "evaluations/results/H07-coverage-contrast-local-20261027-01"
    output.mkdir(exist_ok=False)
    rows = []

    async def unexpected(request):
        raise AssertionError("Both boundaries must finish without model I/O")

    for label, c, expected, missing, code in [
        ("original_missing_applicable_rows", case, 2, 2, "missing_sales_rows"),
        ("local_pre_activation_variant", variant, 0, 0, "period_not_applicable"),
    ]:
        with sandbox(
            agent_mode="live",
            openai_api_key="offline-only",
            openai_base_url="https://api.deepseek.com",
            llm_model="deepseek-flash",
            diagnosis_runtime="v16",
            stop_after_model_failure=True,
            max_model_calls=1,
            model_decision_reserve=0,
        ) as (settings, repo, user, other):
            conv, bundle, _ = admit(repo, user, other, c)
            task = new_task(repo, user, conv, bundle, c.query, "agent")
            async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
                await execute(repo, settings, model=live_model(settings, client))
            record = collect(repo, task["id"], "agent")
            rep = record["task"]["report"]
            cov = next(
                e["payload"]["coverage"]["comparison"]
                for e in record["evidence"]
                if e["payload"]["tool"] == "inspect_dataset"
            )
            assert cov["expected_records"] == expected and cov["missing_records"] == missing
            assert (
                not record["model_invocations"]
                and rep["answer_completion"]["status"] == "boundary_explained"
            )
            assert [g["code"] for g in rep["gap_records"]] == [code]
            if missing == 0:
                assert not any("存在启用后缺行" in o["text"] for o in rep["observations"])
                assert not any(
                    f["metric_key"] in ["net_revenue.comparison", "net_revenue.delta"]
                    for f in rep["facts"]
                )
            grading = grade(c, bundle, record)
            assert grading["automatic_passed"], grading["errors"]
            path = output / label / "receipt.json"
            path.parent.mkdir()
            write(path, {**record, "grading": grading, "local_variant_not_formal_or_paid": True})
            rows.append(
                {
                    "variant": label,
                    "comparison_coverage": cov,
                    "gap_code": code,
                    "answer_completion": rep["answer_completion"]["status"],
                    "real_model_calls": 0,
                    "automatic_passed": True,
                    "receipt": str(path.relative_to(ROOT)),
                }
            )
    assert file_hash(source) == original_hash
    write(
        BASE / "H07-coverage-contrast.json",
        {
            "implementation_sha256": implementation_manifest()[1],
            "original_case_file_unchanged": True,
            "original_case_file_sha256": original_hash,
            "provider_requests": 0,
            "rows": rows,
        },
    )
    print(json.dumps(rows))


if __name__ == "__main__":
    asyncio.run(main())
