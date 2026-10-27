"""Record the agent's explicit decision AFTER inspection. Does not launch requests."""
import json
import sys
from pathlib import Path

from evaluations.diagnosis_probe import audit_probe, failure_reason
from evaluations.diagnosis_probe_accounting import file_hash

ROOT = Path(__file__).resolve().parents[3]
RUN = ROOT / "evaluations/results/diagnosis-boundary-v2-U-live-20261027-01"


def main():
    name, verdict, note = sys.argv[1:]
    assert verdict in {"PASS", "FAIL"} and note.strip()
    p = RUN / name
    record = json.loads((p / "receipt.json").read_bytes())
    if verdict == "PASS":
        assert failure_reason(record, record["grading"]) is None
        audit_probe(p)
    value = {"receipt_sha256": file_hash(p / "receipt.json"), "verdict": verdict,
             "reviewer": "CODE_AUTHOR_AI_MANUAL_REVIEW_NOT_EXTERNAL_BUSINESS_ACCEPTANCE",
             "checks": {"report_all_sections_or_complete_repeat_diff_read": True,
                        "author_raw_source_and_required_answer_obligations_reviewed": True,
                        "causal_language_limitations_rules_and_actions_reviewed": True,
                        "source_policy_and_usage_ledger_reviewed": True}, "notes": note}
    with (p / "review.json").open("x") as f:
        f.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    print(name, verdict)


if __name__ == "__main__":
    main()
