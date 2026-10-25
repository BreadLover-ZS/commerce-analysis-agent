"""Literal scope aggregate before/after: executed D01 data, protocol stub only."""

import argparse
import asyncio
import json
from pathlib import Path

from evaluations.diagnosis_cases import business_input, load_cases
from evaluations.diagnosis_request_checks_v2 import completion_checks
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


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


async def main(output):
    output.mkdir(parents=True, exist_ok=False)
    case = next(c for c in load_cases()[0] if c.case_id == "D01").model_copy(
        update={"query": "列出本期支付金额"}
    )
    verdicts = []
    for runtime in ("v13", "v14"):
        with sandbox(diagnosis_runtime=runtime) as (settings, repo, user, other):
            conv, bundle, _ = admit(repo, user, other, case)
            task = new_task(repo, user, conv, bundle, case.query, "agent")
            model = ScriptedDecisionModel(
                tool_proposal("rank_contributors", {}),
                stop(),
                tool_proposal("compare_periods", {"metric": "gross_revenue"}),
            )
            await execute(repo, settings, model=model)
            run = collect(repo, task["id"], "agent")
            report = run["task"]["report"]
            directory = output / runtime
            directory.mkdir()
            save(directory / "business-input.json", business_input(case))
            save(directory / "bundle.json", bundle)
            save(directory / "agent.json", run)
            export_artifacts(repo, task["id"], directory / "artifacts")
            checks = completion_checks(
                business_input(case),
                report,
                [e["payload"] for e in run["evidence"]],
                case.scope_skus,
            )
            assert run["task"]["validation"]["passed"] and report["stop_reason"] == "target_covered"
            assert checks["requested_scope_items"] is (runtime == "v14")
            if runtime == "v14":
                assert run["steps"][1]["status"] == "REJECTED"
            verdicts.append(
                {
                    "runtime": runtime,
                    "independent_checks": checks,
                    "expected_scope_gap_detected": runtime == "v13",
                    "counterfactual_passed": True,
                    "provider_requests": 0,
                }
            )
    save(
        output / "summary.json",
        {"counterfactuals": verdicts, "passed": True, "provider_requests": 0},
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main(parser.parse_args().output))
