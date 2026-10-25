"""Durable response/checkpoint interruption, only executed development input and SDK stub."""

import argparse
import asyncio
import contextlib
import json
from pathlib import Path

from app.diagnosis.model_calls import DiagnosisModelGateway
from app.tasks.worker import Worker, checkpoint_store
from evaluations.diagnosis_cases import load_cases
from evaluations.diagnosis_runtime import admit, collect, new_task, sandbox
from tests.test_diagnosis_graph import tool_proposal
from tests.test_diagnosis_model_calls import ScriptedDecisionModel

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True)
ROOT = parser.parse_args().output
ROOT.mkdir(parents=True, exist_ok=False)


async def run(runtime, limit):
    case = next(c for c in load_cases()[0] if c.case_id == "D01")
    with sandbox(diagnosis_runtime=runtime, max_model_calls=limit) as (settings, repo, user, other):
        conv, bundle, _ = admit(repo, user, other, case)
        task = new_task(repo, user, conv, bundle, case.query, "agent")
        model = ScriptedDecisionModel(
            tool_proposal("rank_contributors", {}), tool_proposal("analyze_refunds", {})
        )
        original = DiagnosisModelGateway.invoke
        saved = asyncio.Event()
        hold = asyncio.Event()

        async def interrupted(*args, **kwargs):
            result = await original(*args, **kwargs)
            saved.set()
            await hold.wait()
            return result

        async with checkpoint_store(settings) as saver:
            first = Worker(repo, settings, saver, model=model)
            DiagnosisModelGateway.invoke = interrupted
            execution = asyncio.create_task(first.run_once())
            try:
                await asyncio.wait_for(saved.wait(), 10)
                first.stopping.set()
                execution.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await execution
            finally:
                DiagnosisModelGateway.invoke = original
            before = collect(repo, task["id"], "agent")
            await Worker(repo, settings, saver, model=model).run_once()
            after = collect(repo, task["id"], "agent")
        for name, obj in [("before", before), ("after", after)]:
            (ROOT / f"recovery-{runtime}-{limit}-{name}.json").write_text(
                json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n"
            )
        assert before["model_invocations"][0]["status"] == "SUCCEEDED" and before["steps"] == []
        assert after["task"]["validation"]["passed"] and after["steps"][0]["status"] == "SUCCEEDED"
        assert after["model_invocations"][0] == before["model_invocations"][0]
        assert len(model.requests) == (1 if limit == 1 else 2)
        assert after["task"]["report"]["stop_reason"] == (
            "model_budget" if limit == 1 else "target_covered"
        )
        print(
            json.dumps(
                {
                    "runtime": runtime,
                    "limit": limit,
                    "before": before["task"]["status"],
                    "saved_call": [
                        (m["status"], m["usage_complete"]) for m in before["model_invocations"]
                    ],
                    "after": after["task"]["status"],
                    "error": after["task"].get("error_code"),
                    "stop": (after["task"].get("report") or {}).get("stop_reason"),
                    "stub_calls": len(model.requests),
                    "steps": len(after["steps"]),
                }
            ),
            flush=True,
        )


async def main():
    for runtime in ["v13", "v14"]:
        for limit in [12, 1]:
            await run(runtime, limit)
    (ROOT / "summary.json").write_text(
        json.dumps(
            {
                "counterfactual_probes": 4,
                "passed": True,
                "provider_requests": 0,
                "fresh_formal_executions": 0,
            },
            indent=2,
        )
        + "\n"
    )


asyncio.run(main())
