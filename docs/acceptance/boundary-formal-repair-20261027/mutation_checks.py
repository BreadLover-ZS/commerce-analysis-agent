"""Behavioral guard mutations in disposable copies; no real provider or U execution."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from evaluations.diagnosis import implementation_manifest

ROOT = Path(__file__).resolve().parents[3]
PREFIX = "tests/test_formal_single_task.py::"
MUTANTS = [
    ("lose_boundary_synonym", "app/commerce/answer_contract_v5.py",
     "boundary = _boundary(text)", "boundary = False",
     "tests/test_boundary_semantics_v2.py::test_production_boundary_matches_literal_author"),
    ("independent_checker_loses_synonym", "evaluations/diagnosis_request_checks_v4.py",
     "if old_style or action and limit and (local or unavailable):", "if old_style:",
     "tests/test_boundary_semantics_v2.py::test_independent_boundary_matches_literal_author"),
    ("ignore_frozen_boundary_policy", "app/commerce/answer_versions.py",
     'value = parameters.get("answer_boundary_version", 1)', "value = 1",
     "tests/test_boundary_semantics_v2.py::test_boundary_policy_is_frozen_through_worker_and_report"),
    ("accept_failed_manual_review", "evaluations/diagnosis_formal.py",
     'value.get("verdict") == "PASS"', 'value.get("verdict") in {"PASS", "FAIL"}',
     PREFIX + "test_manual_review_failure_blocks_the_second_task[FAIL]"),
    ("drop_formal_hold_from_capacity", "evaluations/diagnosis_formal.py",
     'parent["known_tokens"] + parent["frozen_unknown_tokens"] + HOLD <= LIMIT',
     'parent["known_tokens"] + HOLD <= LIMIT',
     PREFIX + "test_exact_next_request_capacity_counts_old_frozen_unknown[-1]"),
    ("reuse_new_input_instead_of_paired_bundle", "evaluations/diagnosis_formal.py",
     'reuse_inputs=paired_inputs(args.parent, case) if slot["group"] == "agent" else None,',
     "reuse_inputs=None,",
     PREFIX + "test_two_adjacent_sdk_tasks_require_review_and_carry_exact_charges"),
    ("ignore_any_automatic_failure", "evaluations/diagnosis_probe.py",
     'if not grading.get("automatic_passed"):', "if False:",
     PREFIX + "test_first_failure_stops_and_never_admits_a_following_slot[ordinary_grade-2]"),
    ("spend_review_after_invalid_draft", "app/research/graph.py",
     'and state.get("draft_fallback")', "and False",
     "tests/test_diagnosis_probe.py::test_first_fixed_failure_never_spends_on_repair_or_semantic_review[scope]"),
    ("drop_zero_call_accounting_journal", "evaluations/diagnosis_one.py",
     "budget.journal.touch(mode=0o600, exist_ok=False)", "pass",
     "tests/test_diagnosis_probe.py::test_complete_one_task_probe_has_exact_spend_and_stops_on_first_error[zero-0]"),
]


def main():
    files, fingerprint = implementation_manifest()
    results = []
    with TemporaryDirectory(prefix="boundary-formal-mutants-") as d:
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
            target = root / path
            original = target.read_text()
            assert original.count(old) == 1, (name, "mutation_anchor_not_unique")
            command = [str(ROOT / ".venv/bin/python"), "-m", "pytest", "-q", "-p",
                       "tests.no_provider", "--disable-warnings", test]
            baseline = subprocess.run(command, cwd=root, env=env, capture_output=True,
                                      text=True, timeout=90)
            assert baseline.returncode == 0, (name, baseline.stdout[-2000:], baseline.stderr[-500:])
            target.write_text(original.replace(old, new))
            mutant = subprocess.run(command, cwd=root, env=env, capture_output=True,
                                    text=True, timeout=90)
            target.write_text(original)
            result = {"mutation": name, "test": test, "baseline_exit_code": baseline.returncode,
                      "mutant_exit_code": mutant.returncode,
                      "killed": mutant.returncode == 1 and "failed" in mutant.stdout
                                and "ERROR collecting" not in mutant.stdout,
                      "failure_excerpt": mutant.stdout[-2200:]}
            results.append(result)
            print(name, "KILLED" if result["killed"] else "SURVIVED", flush=True)
    assert implementation_manifest()[1] == fingerprint
    result = {"implementation_sha256": fingerprint, "provider_requests": 0,
              "working_source_modified": False, "results": results,
              "all_behavioral_mutants_killed": all(r["killed"] for r in results)}
    Path(__file__).with_name("mutation-results.json").write_text(json.dumps(result, indent=2) + "\n")
    assert result["all_behavioral_mutants_killed"]


if __name__ == "__main__":
    main()
