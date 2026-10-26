"""Regrade retained v11/v14/v15 raw receipts without Task execution or HTTP."""

import argparse
import hashlib
import json
import socket
from pathlib import Path

from evaluations.diagnosis import implementation_manifest
from evaluations.diagnosis_cases import load_cases
from evaluations.diagnosis_grading import grade

ROOT = Path(__file__).resolve().parents[3]
RUNS = [
    ("diagnosis-v11-h01-h20-live-20261023-01", None),
    ("diagnosis-v14-h01-h20-live-20261025-01", None),
    (
        "diagnosis-v15-h01-h20-live-20261026-01",
        "docs/acceptance/diagnosis-v15-h01-h20/semantic-contract.json",
    ),
]


def main(output):
    def forbidden(*args, **kwargs):
        raise AssertionError("Regrading forbids network")

    socket.socket.connect = forbidden
    results = []
    for name, sidecar in RUNS:
        root = ROOT / "evaluations/results" / name
        meta = json.loads((root / "run.json").read_bytes())
        source = root / "cases-source.json"
        assert hashlib.sha256(source.read_bytes()).hexdigest() == meta["cases_file_sha256"]
        cases = {
            c.case_id: c
            for c in load_cases(
                source, split=meta["planned"]["split"], case_ids=meta["planned"]["case_ids"]
            )[0]
        }
        semantic = json.loads((ROOT / sidecar).read_bytes()) if sidecar else None
        checked = []
        for line in (root / "progress.jsonl").read_bytes().splitlines():
            row = json.loads(line)
            path = root / row["receipt"]
            raw = path.read_bytes()
            record = json.loads(raw)
            bundle = json.loads(path.with_name("bundle.json").read_bytes())
            current = grade(cases[row["case_id"]], bundle, record, semantic_contract=semantic)
            checked.append(
                {
                    "receipt": row["receipt"],
                    "original_sha256": hashlib.sha256(raw).hexdigest(),
                    "unchanged": current == record["grading"],
                    "automatic_passed": current["automatic_passed"],
                }
            )
            assert path.read_bytes() == raw
        results.append(
            {
                "run": name,
                "checked": len(checked),
                "passed": sum(x["automatic_passed"] for x in checked),
                "changed": [x["receipt"] for x in checked if not x["unchanged"]],
                "records": checked,
            }
        )
    value = {
        "status": "READ_ONLY_REGRADING_NO_MODEL_QUALITY_UPGRADE",
        "implementation_sha256": implementation_manifest()[1],
        "results": results,
        "provider_requests": 0,
        "task_executions": 0,
    }
    with output.open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    assert all(not r["changed"] for r in results), [(r["run"], r["changed"]) for r in results]
    print(
        json.dumps(
            [
                {"run": r["run"], "checked": r["checked"], "changed": len(r["changed"])}
                for r in results
            ]
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args().output)
