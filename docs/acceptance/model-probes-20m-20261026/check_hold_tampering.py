"""Adversarial conservative-hold checks on disposable copies, never old originals."""

import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from evaluations.diagnosis_probe_accounting import legacy_hold

ROOT = Path(__file__).resolve().parents[3]
LEGACY = ROOT / "evaluations/results/diagnosis-v15-h01-h20-live-20261026-01"
SOURCE = ROOT / "evaluations/results/diagnosis-v16-plan-feasibility-20261026-01/source-snapshot"
AUTH = Path(__file__).with_name("authorization.json")


def main():
    original = legacy_hold(LEGACY, SOURCE, AUTH)
    rows = [json.loads(line) for line in (LEGACY / "progress.jsonl").read_bytes().splitlines()]
    names = [
        "run.json",
        "summary.json",
        "progress.jsonl",
        "budget-journal.jsonl",
        "cases-source.json",
    ] + [r["receipt"] for r in rows]
    outcomes = []
    for mode in [
        "authorization_cap",
        "hold",
        "source",
        "journal_spent",
        "http_usage",
        "record_usage",
        "unknown_zero",
        "extra_unknown",
        "missing_receipt",
        "duplicate_progress",
        "different_run",
        "case_source",
    ]:
        with TemporaryDirectory(prefix="commerce-hold-counterexample-") as d:
            root = Path(d) / "legacy"
            root.mkdir()
            for name in names:
                dst = root / name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(LEGACY / name, dst)
            auth = Path(d) / "authorization.json"
            shutil.copyfile(AUTH, auth)
            source = SOURCE

            def mutate(path, fn):
                v = json.loads(path.read_bytes())
                fn(v)
                path.write_text(json.dumps(v))

            if mode in {"authorization_cap", "hold"}:
                mutate(
                    auth,
                    lambda v: v.update(
                        **(
                            {"authorized_cumulative_token_limit": 19999999}
                            if mode == "authorization_cap"
                            else {"authorized_legacy_unknown_hold_tokens": 0}
                        )
                    ),
                )
            elif mode == "source":
                source = Path(d) / "source"
                shutil.copytree(SOURCE, source)
                (source / "evaluations/diagnosis.py").write_text("changed")
            elif mode == "journal_spent":
                events = [
                    json.loads(line)
                    for line in (root / "budget-journal.jsonl").read_bytes().splitlines()
                ]
                events[-1]["spent_tokens"] -= 1
                (root / "budget-journal.jsonl").write_text(
                    "".join(json.dumps(e) + "\n" for e in events)
                )
            elif mode in {"http_usage", "record_usage"}:
                path = root / rows[0]["receipt"]
                if mode == "http_usage":
                    mutate(
                        path,
                        lambda v: v["provider_trace"][0].update(
                            known_tokens=v["provider_trace"][0]["known_tokens"] + 1
                        ),
                    )
                else:
                    mutate(
                        path,
                        lambda v: v["task"]["metrics"].update(
                            input_tokens=v["task"]["metrics"]["input_tokens"] + 1
                        ),
                    )
            elif mode == "unknown_zero":
                path = root / original["unknown_receipt"]

                def zero(v):
                    c = next(
                        c
                        for c in v["model_invocations"]
                        if c["call_id"] == original["unknown_call_id"]
                    )
                    c.update(
                        status="SUCCEEDED",
                        usage_complete=True,
                        usage={"input_tokens": 0, "output_tokens": 0},
                    )
                    v["task"]["metrics"]["usage_complete"] = True

                mutate(path, zero)
            elif mode == "extra_unknown":
                row = next(r for r in rows if r["group"] != "fixed")

                def unknown(v):
                    v["model_invocations"][0].update(
                        status="UNKNOWN", usage_complete=False, usage=None
                    )
                    v["task"]["metrics"]["usage_complete"] = False

                mutate(root / row["receipt"], unknown)
            elif mode == "missing_receipt":
                (root / rows[0]["receipt"]).unlink()
            elif mode == "duplicate_progress":
                path = root / "progress.jsonl"
                path.write_bytes(path.read_bytes() + json.dumps(rows[0]).encode() + b"\n")
            elif mode == "different_run":
                mutate(root / "run.json", lambda v: v.update(run_id="different"))
            else:
                (root / "cases-source.json").write_text("{}")
            try:
                legacy_hold(root, source, auth)
            except (ValueError, KeyError, TypeError, OSError):
                outcomes.append({"mutation": mode, "rejected": True})
            else:
                outcomes.append({"mutation": mode, "rejected": False})
    assert original == legacy_hold(LEGACY, SOURCE, AUTH), "Original evidence changed"
    output = Path(__file__).with_name("hold-tampering.json")
    with output.open("x") as f:
        json.dump(
            {
                "provider_requests": 0,
                "raw_originals_modified": False,
                "known_tokens": original["known_tokens"],
                "hold_tokens": original["frozen_unknown_tokens"],
                "results": outcomes,
            },
            f,
            indent=2,
        )
    assert all(r["rejected"] for r in outcomes)
    print(json.dumps({"mutations_rejected": len(outcomes), "provider_requests": 0}))


if __name__ == "__main__":
    main()
