"""Read-only per-slot inspection, including full text or a diff to a reviewed repeat."""
import difflib
import json
import re
import sys
from pathlib import Path

from evaluations.diagnosis_probe import audit_probe
from evaluations.diagnosis_probe_accounting import file_hash

ROOT = Path(__file__).resolve().parents[3]
RUN = ROOT / "evaluations/results/diagnosis-boundary-v2-U-live-20261027-01"
PACKET = ROOT / "docs/acceptance/diagnosis-boundary-v2-formal-U-20261027"


def read(p):
    return json.loads(p.read_bytes())


def text(p):
    files = sorted((p / "artifacts").glob("report*.md"))
    return "\n".join(f.read_text() for f in files)


def normalized(value):
    value = re.sub(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", "<UUID>", value)
    value = re.sub(r"\b[0-9a-f]{64}\b", "<HASH>", value)
    value = re.sub(r"\b[EF]-[0-9a-f]+\b", "<EVIDENCE_ID>", value)
    value = re.sub(r"获取时间：[^；\n]+", "获取时间：<TIMESTAMP>", value)
    value = re.sub(r'"(?:input_tokens|output_tokens)": \d+', '"usage_tokens": <COUNT>', value)
    return value


def main():
    p = RUN / sys.argv[1]
    meta, record, summary = (read(p / n) for n in ["probe.json", "receipt.json", "summary.json"])
    task = record["task"]
    print("SUMMARY", json.dumps(summary, ensure_ascii=False))
    print("TASK", json.dumps({k: task.get(k) for k in ["query", "status", "metrics", "bundle_id"]}, ensure_ascii=False))
    print("VALIDATION", json.dumps(task.get("validation"), ensure_ascii=False))
    print("GRADING", json.dumps(record["grading"], ensure_ascii=False))
    print("MODELS", json.dumps([{k: m.get(k) for k in ["phase", "status", "usage", "validated_output"]}
                                 for m in record["model_invocations"] if m.get("phase") != "SemanticReview"], ensure_ascii=False))
    if task["metrics"]["usage_complete"]:
        print("VERIFIED_ACCOUNTING", json.dumps(audit_probe(p), ensure_ascii=False))
    author = next(a for a in read(PACKET / "answer-obligations.json")["cases"] if a["case_id"] == meta["case_id"])
    prior = RUN / (meta["case_id"] + "-1-" + meta["group"])
    if prior != p and (prior / "review.json").is_file():
        review = read(prior / "review.json")
        assert review["verdict"] == "PASS" and review["receipt_sha256"] == file_hash(prior / "receipt.json")
        diff = "\n".join(difflib.unified_diff(normalized(text(prior)).splitlines(),
                                             normalized(text(p)).splitlines(), lineterm=""))
        print("DIFF_TO_REVIEWED_FIRST_REPEAT", diff or "IDENTICAL_EXCEPT_IDS_USAGE_AND_TIMESTAMPS")
    else:
        print("AUTHOR", json.dumps(author, ensure_ascii=False))
        print("FULL_REPORT", text(p))
    print("END_SLOT", meta["slot_id"], file_hash(p / "receipt.json"))


if __name__ == "__main__":
    main()
