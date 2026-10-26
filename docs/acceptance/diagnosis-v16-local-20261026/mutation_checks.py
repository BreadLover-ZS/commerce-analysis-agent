"""Fault injection in disposable source copies; never edit the working implementation."""

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from evaluations.diagnosis import implementation_manifest

ROOT = Path(__file__).resolve().parents[3]
MUTATIONS = [
    (
        "unbounded_report",
        "app/commerce/review_report_v5.py",
        '    slots = 8 - len(candidate["sections"][3]["claims"])',
        "    slots = 8",
        "tests/test_diagnosis_v16.py::test_capacity_preserves_all_mandatory_facts_and_baseline[4]",
    ),
    (
        "clip_before_validation",
        "app/commerce/review_report_v5.py",
        "    narrative = ReviewNarrativeV3.model_validate(",
        '    if isinstance(narrative, dict):\n        narrative = {**narrative, "analysis": narrative["analysis"][:3]}\n    narrative = ReviewNarrativeV3.model_validate(',
        "tests/test_diagnosis_v16.py::test_entire_proposal_is_validated_before_truncation[illegal_fourth]",
    ),
    (
        "exception_text_means_zero",
        "app/diagnosis/model_calls.py",
        "            proof = trusted_denial(exc, attempt_state) if self.protocol == 2 else None",
        '            if any("evaluation_token_budget_capacity" in str(e) for e in (exc, exc.__cause__)):\n                return {**reserved, "status": "NOT_SENT", "usage_complete": True}\n            proof = trusted_denial(exc, attempt_state) if self.protocol == 2 else None',
        "tests/test_diagnosis_v16.py::test_unproven_errors_remain_unknown[same_text]",
    ),
    (
        "all_errors_mean_unsent",
        "app/diagnosis/model_calls.py",
        "            proof = trusted_denial(exc, attempt_state) if self.protocol == 2 else None",
        '            return {**reserved, "status": "NOT_SENT", "usage_complete": True}\n            proof = trusted_denial(exc, attempt_state) if self.protocol == 2 else None',
        "tests/test_diagnosis_v16.py::test_unproven_errors_remain_unknown[timeout]",
    ),
    (
        "skip_unknown_without_certificate",
        "evaluations/diagnosis_budget.py",
        'metrics.get("usage_complete") is not True and not amended',
        "False",
        "tests/test_diagnosis_budget.py::test_prior_charge_tampering_cannot_create_free_budget[unknown]",
    ),
]


def main(output):
    manifest, fingerprint = implementation_manifest()
    results = []
    with TemporaryDirectory(prefix="commerce-v16-mutants-") as temp:
        root = Path(temp)
        for name in manifest:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        shutil.copytree(ROOT / "data", root / "data")
        packet = root / "docs/acceptance/diagnosis-v14-h01-h20-12m"
        packet.mkdir(parents=True)
        shutil.copyfile(
            ROOT / "docs/acceptance/diagnosis-v14-h01-h20-12m/cases.json", packet / "cases.json"
        )
        env = {**os.environ, "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"}
        env.pop("NO_PROVIDER_AUDIT", None)
        for name, path, old, new, test in MUTATIONS:
            source = root / path
            original = (ROOT / path).read_text()
            assert original.count(old) == 1, name
            source.write_text(original)
            args = [
                str(ROOT / ".venv/bin/python"),
                "-m",
                "pytest",
                "-q",
                "-p",
                "tests.no_provider",
                "--disable-warnings",
                test,
            ]
            normal = subprocess.run(
                args, cwd=root, env=env, capture_output=True, text=True, timeout=30
            )
            if normal.returncode:
                raise AssertionError(
                    (name, "baseline_not_green", normal.stdout[-3000:], normal.stderr[-1000:])
                )
            source.write_text(original.replace(old, new))
            mutated = subprocess.run(
                args, cwd=root, env=env, capture_output=True, text=True, timeout=30
            )
            results.append(
                {
                    "mutation": name,
                    "test": test,
                    "baseline_exit_code": normal.returncode,
                    "mutant_exit_code": mutated.returncode,
                    "killed": mutated.returncode == 1,
                    "failure_excerpt": mutated.stdout[-2200:],
                }
            )
            source.write_text(original)
    assert implementation_manifest()[1] == fingerprint
    value = {
        "implementation_sha256": fingerprint,
        "status": "DISPOSABLE_COPY_MUTATIONS",
        "provider_requests": 0,
        "working_source_modified": False,
        "results": results,
    }
    with output.open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    assert all(r["killed"] for r in results), results
    print(json.dumps({"mutants_killed": len(results), "provider_requests": 0}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args().output)
