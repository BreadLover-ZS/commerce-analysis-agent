"""Run old and new defenses against defects in disposable source copies only."""

import argparse
import importlib.util
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "prior_mutations", ROOT / "docs/acceptance/diagnosis-v16-local-20261026/mutation_checks.py"
)
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)

# Same historical defects; adapt the replacement anchor to the new schema selector.
MUTATIONS = [
    (
        name,
        path,
        "    narrative = schema.model_validate(" if name == "clip_before_validation" else old,
        '    if isinstance(narrative, dict):\n        narrative = {**narrative, "analysis": narrative["analysis"][:3]}\n    narrative = schema.model_validate('
        if name == "clip_before_validation"
        else new,
        test,
    )
    for name, path, old, new, test in prior.MUTATIONS
] + [
    (
        "ignore_negative_polarity",
        "app/commerce/explanation_contract_v6.py",
        "        negative = bool(re.search(DENY, clause, re.I))",
        "        negative = False",
        "tests/test_pre_model_preflight.py::test_global_denial_allows_empty_analysis_and_forbids_model_hypotheses",
    ),
    (
        "skip_final_report_revalidation",
        "app/research/graph.py",
        '        if v6 and result["passed"]:',
        '        if False and result["passed"]:',
        "tests/test_pre_model_preflight.py::test_semantic_budget_fallback_is_revalidated_before_publication[True]",
    ),
    (
        "omit_ancestral_freshness_check",
        "evaluations/diagnosis_plan.py",
        "        used_inputs.update(ancestor_inputs)",
        "        used_inputs.update(())",
        "tests/test_pre_model_plan.py::test_freshness_cannot_be_claimed_without_matching_historical_questions[reused_inputs]",
    ),
    (
        "allow_formal_entry_without_plan",
        "evaluations/diagnosis.py",
        '        raise ValueError("保留集正式模型测试必须提供冻结计划，禁止提前读取凭据或发送HTTP")',
        "        pass",
        "tests/test_pre_model_preflight.py::test_formal_entry_refuses_missing_frozen_plan_before_credentials",
    ),
]


def main(output):
    import json

    from evaluations.diagnosis import implementation_manifest

    manifest, fingerprint = implementation_manifest()
    results = []
    with TemporaryDirectory(prefix="commerce-preflight-mutants-") as temp:
        root = Path(temp)
        for name in manifest:
            dst = root / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, dst)
        shutil.copytree(ROOT / "data", root / "data")
        for packet in ("diagnosis-v14-h01-h20-12m", "diagnosis-v15-h01-h20"):
            shutil.copytree(ROOT / "docs/acceptance" / packet, root / "docs/acceptance" / packet)
        env = {**os.environ, "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"}
        env.pop("NO_PROVIDER_AUDIT", None)
        for name, path, old, new, test in MUTATIONS:
            source, original = root / path, (ROOT / path).read_text()
            assert original.count(old) == 1, name
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
                args, cwd=root, env=env, capture_output=True, text=True, timeout=45
            )
            if normal.returncode:
                raise AssertionError(
                    (name, "baseline_not_green", normal.stdout[-3500:], normal.stderr[-1000:])
                )
            source.write_text(original.replace(old, new))
            mutated = subprocess.run(
                args, cwd=root, env=env, capture_output=True, text=True, timeout=45
            )
            results.append(
                {
                    "mutation": name,
                    "test": test,
                    "baseline_exit_code": 0,
                    "mutant_exit_code": mutated.returncode,
                    "killed": mutated.returncode == 1,
                    "failure_excerpt": mutated.stdout[-2500:],
                }
            )
            source.write_text(original)
            print(name, "KILLED" if results[-1]["killed"] else "SURVIVED", flush=True)
    assert implementation_manifest()[1] == fingerprint
    with output.open("x") as stream:
        json.dump(
            {
                "implementation_sha256": fingerprint,
                "provider_requests": 0,
                "working_source_modified": False,
                "results": results,
            },
            stream,
            ensure_ascii=False,
            indent=2,
        )
        stream.write("\n")
    assert all(r["killed"] for r in results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args().output)
