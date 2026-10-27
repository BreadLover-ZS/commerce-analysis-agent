"""Kill paid-probe guard defects in disposable source copies, with network forbidden."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from evaluations.diagnosis import implementation_manifest

ROOT = Path(__file__).resolve().parents[3]
PREFIX = "tests/test_diagnosis_probe.py::"
MUTANTS = [
    (
        "drop_historical_hold_from_cap",
        "evaluations/diagnosis.py",
        "self.spent_tokens + self.reserved_tokens + self.frozen_unknown_tokens + reservation",
        "self.spent_tokens + self.reserved_tokens + reservation",
        "test_historical_hold_counts_towards_cap_without_becoming_known_usage",
    ),
    (
        "pay_after_failed_parse",
        "app/diagnosis/model_calls.py",
        'ModelInvocation.status == "FAILED"',
        'ModelInvocation.status == "IMPOSSIBLE"',
        "test_failed_durable_invocation_blocks_another_identity_without_spending",
    ),
    (
        "review_after_invalid_composition",
        "app/research/graph.py",
        'and state.get("draft_fallback")',
        "and False",
        "test_first_fixed_failure_never_spends_on_repair_or_semantic_review[scope]",
    ),
    (
        "correct_rejected_proposal",
        "app/diagnosis/graph.py",
        'if task["parameters"]["budget"].get("stop_after_model_failure") and any(',
        "if False and any(",
        "test_business_rejected_diagnosis_step_stops_before_a_corrective_proposal",
    ),
    (
        "pay_for_second_nonprogress",
        "app/diagnosis/graph.py",
        '1 if calls and task["parameters"]["budget"].get("stop_after_model_failure") else 2',
        "2",
        "test_cached_nonprogress_does_not_pay_for_a_second_proposal",
    ),
    (
        "missing_zero_call_journal",
        "evaluations/diagnosis_probe.py",
        "    budget.journal.touch(mode=0o600, exist_ok=False)",
        "    pass",
        "test_complete_one_task_probe_has_exact_spend_and_stops_on_first_error[zero-0]",
    ),
    (
        "accept_new_unknown",
        "evaluations/diagnosis_probe.py",
        '    validate_record_accounting(record, root, meta["run_id"])',
        "    pass",
        "test_complete_one_task_probe_has_exact_spend_and_stops_on_first_error[valid-2]",
    ),
    (
        "ignore_pending_review",
        "evaluations/diagnosis_probe.py",
        'elif review.get("verdict") != "PASS":',
        "elif False:",
        "test_review_and_repair_are_bound_before_starting_next_batch",
    ),
    (
        "ignore_grade_failure",
        "evaluations/diagnosis_probe.py",
        'if not grading.get("automatic_passed"):',
        "if False:",
        "test_all_failure_types_stop_even_if_a_fallback_report_looks_valid[grade_failed]",
    ),
]
MUTANTS.append(
    (
        "remove_both_rejection_guards",
        "app/diagnosis/graph.py",
        'if task["parameters"]["budget"].get("stop_after_model_failure") and any(',
        "if False and any(",
        "test_business_rejected_diagnosis_step_stops_before_a_corrective_proposal",
    )
)

MUTANTS.extend(
    [
        (
            "task_http_budget_mismatch",
            "evaluations/diagnosis_probe.py",
            "max_model_calls=args.max_http,",
            "max_model_calls=12,",
            "tests/test_probe_budget_repair.py::test_driver_freezes_its_two_http_limit_in_the_task",
        ),
        (
            "wrong_decision_reserve",
            "app/diagnosis/planner_budget.py",
            'reserve = budget.get("decision_call_reserve", 3)',
            "reserve = 3",
            "tests/test_probe_budget_repair.py::test_explicit_zero_reserve_is_reported_as_four_decision_calls",
        ),
        (
            "omit_actual_gross_capability",
            "app/diagnosis/planner_v16.py",
            '"scope.gross_revenue.current/comparison/delta",',
            '"scope.unknown.current/comparison/delta",',
            "tests/test_probe_budget_repair.py::test_refund_capability_declares_the_gross_amounts_it_actually_returns",
        ),
        (
            "accept_structurally_insufficient_budget",
            "evaluations/diagnosis_probe.py",
            "if limit < minimum:",
            "if False:",
            "tests/test_probe_budget_repair.py::test_one_call_fixed_probe_refuses_before_loading_credentials",
        ),
    ]
)


def main(reuse=None, output=None):
    files, fingerprint = implementation_manifest()
    results = []
    if reuse is not None:
        prior = json.loads(Path(reuse).read_bytes())
        assert prior["implementation_sha256"] == fingerprint
        results = prior["results"]
        assert len(results) == 9
        assert {r["mutation"] for r in results} == {
            m[0] for m in MUTANTS if m[0] != "remove_both_rejection_guards"
        }
        assert all(r["baseline_exit_code"] == 0 for r in results)

    with TemporaryDirectory(prefix="commerce-probe-mutants-") as d:
        root = Path(d)
        for name in files:
            dst = root / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, dst)
        shutil.copytree(ROOT / "data", root / "data")
        for name in ["diagnosis-v14-h01-h20-12m", "diagnosis-v15-h01-h20"]:
            shutil.copytree(ROOT / "docs/acceptance" / name, root / "docs/acceptance" / name)
        env = {**os.environ, "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"}
        env.pop("NO_PROVIDER_AUDIT", None)
        for name, path, old, new, test in MUTANTS:
            if any(r["mutation"] == name for r in results):
                continue
            target = root / path
            original = (ROOT / path).read_text()
            assert original.count(old) == 1, name
            args = [
                str(ROOT / ".venv/bin/python"),
                "-m",
                "pytest",
                "-q",
                "-p",
                "tests.no_provider",
                "--disable-warnings",
                test if test.startswith("tests/") else PREFIX + test,
            ]
            baseline = subprocess.run(
                args, cwd=root, env=env, capture_output=True, text=True, timeout=45
            )
            assert baseline.returncode == 0, (
                name,
                "baseline_failed",
                baseline.stdout[-2400:],
                baseline.stderr[-500:],
            )
            changed = original.replace(old, new)
            if name == "remove_both_rejection_guards":
                secondary = '1 if calls and task["parameters"]["budget"].get("stop_after_model_failure") else 2'
                assert changed.count(secondary) == 1
                changed = changed.replace(secondary, "2")
            target.write_text(changed)
            mutant = subprocess.run(
                args, cwd=root, env=env, capture_output=True, text=True, timeout=45
            )
            results.append(
                {
                    "mutation": name,
                    "test": test if test.startswith("tests/") else PREFIX + test,
                    "baseline_exit_code": baseline.returncode,
                    "mutant_exit_code": mutant.returncode,
                    "killed": mutant.returncode == 1,
                    "failure_excerpt": mutant.stdout[-1800:],
                }
            )
            target.write_text(original)
            print(name, "KILLED" if results[-1]["killed"] else "SURVIVED", flush=True)
    assert implementation_manifest()[1] == fingerprint
    with (output or Path(__file__).with_name("stop-mutations.json")).open("x") as f:
        json.dump(
            {
                "implementation_sha256": fingerprint,
                "provider_requests": 0,
                "working_source_modified": False,
                "results": results,
                "single_guard_control": "correct_rejected_proposal survives because the separate no-information guard still stops the Task",
                "behavior_breaking_mutants_killed": sum(r["killed"] for r in results),
                "reuse_evidence": str(reuse) if reuse else None,
            },
            f,
            indent=2,
        )
    assert all(r["killed"] for r in results if r["mutation"] != "correct_rejected_proposal")
    assert (
        next(r for r in results if r["mutation"] == "correct_rejected_proposal")["mutant_exit_code"]
        == 0
    )


if __name__ == "__main__":
    import argparse

    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--reuse", type=Path)
    cli.add_argument("--output", type=Path)
    args = cli.parse_args()
    main(args.reuse, args.output)
