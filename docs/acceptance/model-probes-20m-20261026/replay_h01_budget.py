"""Replay saved H01 proposals and a two-tool alternative; network is forbidden."""

import asyncio
import copy
import json
import socket
from pathlib import Path
from uuid import uuid4

import httpx

from evaluations.diagnosis import RequestBudget, implementation_manifest, live_model, write
from evaluations.diagnosis_cases import load_cases
from evaluations.diagnosis_grading import grade
from evaluations.diagnosis_probe_accounting import file_hash
from evaluations.diagnosis_runtime import ROOT, admit, collect, execute, new_task, sandbox
from tests.test_diagnosis_probe import completion

BASE = Path(__file__).resolve().parent
ORIGINAL = ROOT / "evaluations/results/model-probe-H01-amounts-20261026-01/receipt.json"


async def main(output, evidence):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline replay forbids network")

    socket.socket.connect = forbidden
    original = json.loads(ORIGINAL.read_bytes())
    paid = [c for c in original["model_invocations"] if c["status"] == "SUCCEEDED"]
    assert len(paid) == 2
    case = load_cases(ROOT / "docs/acceptance/diagnosis-v15-h01-h20/cases.json", case_ids=["H01"])[
        0
    ][0]
    first = copy.deepcopy(paid[0]["validated_output"])
    efficient = {
        "action": "tool",
        "question": "一次补齐同范围支付及退款金额对账证据",
        "hypothesis_code": "refund_amount_change",
        "tool_name": "analyze_refunds",
        "arguments": {"filters": {"skus": case.scope_skus}},
    }
    output.mkdir(exist_ok=False)
    rows = []
    for label, choices in [
        ("original_order", [first, paid[1]["validated_output"]]),
        ("efficient_order", [first, efficient]),
    ]:
        folder = output / label
        folder.mkdir()
        requests = []
        budgets = []
        frontiers = []

        async def transport(request):
            body = json.loads(request.content)
            requests.append(1)
            for message in body["messages"]:
                try:
                    context = json.loads(message.get("content", ""))
                except (ValueError, TypeError):
                    continue
                if "remaining_budget" in context:
                    budgets.append(context["remaining_budget"])
                    frontiers.append(context["capability_frontier"]["analyze_refunds"])
            assert len(requests) <= 2
            return completion(choices[len(requests) - 1])

        with sandbox(
            agent_mode="live",
            openai_api_key="offline-replay-only",
            openai_base_url="https://api.deepseek.com",
            llm_model="deepseek-flash",
            diagnosis_runtime="v16",
            stop_after_model_failure=True,
            max_model_calls=2,
            model_decision_reserve=0,
        ) as (settings, repo, user, other):
            conversation, bundle, _ = admit(repo, user, other, case)
            task = new_task(repo, user, conversation, bundle, case.query, "agent")
            budget = RequestBudget(
                2,
                protocol=2,
                run_id=str(uuid4()),
                max_total_tokens=20000000,
                context_ceiling=1048576,
                journal=folder / "mock-journal.jsonl",
            )
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(transport),
                event_hooks={"request": [budget.before_send], "response": [budget.after_response]},
            ) as client:
                await execute(repo, settings, model=live_model(settings, client))
            record = collect(repo, task["id"], "agent")
            g = grade(case, bundle, record)
            write(
                folder / "receipt.json",
                {**record, "grading": g, "simulated_usage_not_billable": True},
            )
            rep = record["task"]["report"]
            assert record["task"]["parameters"]["budget"]["max_model_calls"] == 2
            assert [b["model_calls"] for b in budgets] == [2, 1]
            assert [b["decision_calls"] for b in budgets] == [2, 1]
            assert all(any("gross_revenue" in x for x in f["outputs"]) for f in frontiers)
            if label == "original_order":
                assert not g["automatic_passed"] and rep["answer_completion"]["remaining_work"] == [
                    "accounting_amount_identity"
                ]
            else:
                assert g["automatic_passed"] and rep["answer_completion"]["status"] == "fulfilled"
                assert (
                    len([x for x in rep["request_obligations"] if x.get("status") == "acquired"])
                    == 27
                )
                assert any(
                    w["capability"] == "accounting_amount_identity" and len(w["fact_refs"]) == 9
                    for w in rep["capability_witnesses"]
                )
            rows.append(
                {
                    "variant": label,
                    "actual_provider_requests": 0,
                    "mock_sdk_calls": len(requests),
                    "automatic_passed": g["automatic_passed"],
                    "completion": rep["answer_completion"]["status"],
                    "remaining_work": rep["answer_completion"]["remaining_work"],
                    "prompt_budgets": budgets,
                    "receipt": str((folder / "receipt.json").relative_to(ROOT)),
                    "simulated_usage_not_billable": True,
                }
            )
    value = {
        "implementation_sha256": implementation_manifest()[1],
        "original_failed_receipt_sha256": file_hash(ORIGINAL),
        "actual_provider_requests": 0,
        "original_paid_receipt_modified": False,
        "mock_usage_not_added_to_user_accounting": True,
        "rows": rows,
    }
    assert file_hash(ORIGINAL) == value["original_failed_receipt_sha256"]
    with evidence.open("x") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(json.dumps(value, ensure_ascii=False))


if __name__ == "__main__":
    import argparse

    cli = argparse.ArgumentParser()
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--evidence", type=Path, required=True)
    args = cli.parse_args()
    asyncio.run(main(args.output.resolve(), args.evidence.resolve()))
