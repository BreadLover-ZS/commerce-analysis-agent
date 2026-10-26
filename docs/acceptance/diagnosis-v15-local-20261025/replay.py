"""Local regression on previously executed Q inputs only, never on the fresh T packet."""

import argparse
import asyncio
import json
import socket
from pathlib import Path

from evaluations.diagnosis_cases import load_cases
from evaluations.diagnosis_grading import grade
from evaluations.diagnosis_harness import answer_clarification
from evaluations.diagnosis_runtime import admit, collect, execute, new_task, sandbox

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "docs/acceptance/diagnosis-v14-h01-h20-12m/cases.json"


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


async def main(output):
    output.mkdir(parents=True, exist_ok=False)
    rows = []

    # Assert no external provider transport can be reached by this replay.
    def forbidden(*args, **kwargs):
        raise AssertionError("local replay forbids socket.connect")

    socket.socket.connect = forbidden
    for old in load_cases(SOURCE)[0]:
        expected = (
            old.expected.model_copy(update={"stop_reasons": ["evidence_gap"]})
            if old.case_id in {"H06", "H07", "H17"}
            else old.expected
        )
        case = old.model_copy(update={"expected": expected})
        for group in (
            ["agent", "fixed"] if case.expected.baseline_capability == "common" else ["additional"]
        ):
            with sandbox(diagnosis_runtime="v15", commerce_review_answer_version=4) as (
                settings,
                repo,
                user,
                other,
            ):
                conv, bundle, _ = admit(repo, user, other, case)
                task = new_task(repo, user, conv, bundle, case.query, group)
                await execute(repo, settings)
                if group != "fixed" and case.harness.clarification_choice:
                    await answer_clarification(
                        repo, settings, user, task["id"], case.harness.clarification_choice
                    )
                run = collect(repo, task["id"], group)
                result = grade(case, bundle, run)
                folder = output / f"{case.case_id}-{group}"
                folder.mkdir()
                save(folder / "bundle.json", bundle)
                save(folder / "receipt.json", {**run, "grading": result})
                rows.append(
                    {
                        "case": case.case_id,
                        "group": group,
                        "passed": result["automatic_passed"],
                        "failed": [k for k, v in result["assertions"].items() if not v],
                        "status": run["task"]["status"],
                        "completion": (run["task"].get("report") or {})
                        .get("answer_completion", {})
                        .get("status"),
                    }
                )
                print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    summary = {
        "provider_requests": 0,
        "new_formal_packet_executions": 0,
        "source": str(SOURCE.relative_to(ROOT)),
        "executed": len(rows),
        "passed": sum(r["passed"] for r in rows),
        "rows": rows,
    }
    save(output / "summary.json", summary)
    assert all(r["passed"] for r in rows), summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main(parser.parse_args().output))
