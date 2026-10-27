"""Read-only analysis of executed H01 only; append audits, never regrade or call a model."""
import json
import socket
from pathlib import Path

from app.research.context import evidence_package
from evaluations.diagnosis import implementation_manifest
from evaluations.diagnosis_formal_plan import check
from evaluations.diagnosis_probe import load_parent
from evaluations.diagnosis_probe_accounting import file_hash

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent
RUN = ROOT / "evaluations/results/diagnosis-boundary-v2-U-live-20261027-01"
PACKET = ROOT / "docs/acceptance/diagnosis-boundary-v2-formal-U-20261027"


def read(p):
    return json.loads(p.read_bytes())


def save(p, v):
    with p.open("x") as f:
        f.write(json.dumps(v, ensure_ascii=False, indent=2) + "\n")


def main():
    def forbidden(*a, **kw):
        raise AssertionError("stopped-round analysis forbids all network")

    socket.socket.connect = forbidden
    m = check(PACKET)
    rows, evidence_checks = [], []
    failed = RUN / "H01-3-fixed"
    for slot in m["slots"][:5]:
        p = RUN / slot["slot_id"].replace("/", "-")
        r, summary, meta, review = (read(p / n) for n in ["receipt.json", "summary.json", "probe.json", "review.json"])
        receipt_hash = file_hash(p / "receipt.json")
        assert meta["implementation_sha256"] == m["implementation_sha256"]
        assert review["receipt_sha256"] == receipt_hash
        context_pass = None
        if slot["group"] == "fixed":
            request = read(p / "http-request-2.json")["request"]
            header, body = request["messages"][-1]["content"].rsplit("\n证据：\n", 1)
            sent, attached = json.loads(header), json.loads(body)
            actual = evidence_package(r["evidence"], r["task"]["query"])
            expanded = evidence_package(r["evidence"], r["task"]["query"], max_chars=1000000)
            assert attached == actual["sources"] == []
            assert len(expanded["sources"]) == 1
            item = expanded["sources"][0]
            chars = len(json.dumps([item], ensure_ascii=False))
            claim_refs = sorted({eid for section in sent["report"]["sections"]
                                 for c in section["claims"] for eid in c["evidence_ids"]})
            assert set(claim_refs) == {r["evidence"][0]["id"]}
            context_pass = not (claim_refs and not attached)
            assert context_pass is False and chars == 28659 and actual["manifest"]["max_characters"] == 28000
            audit = {"receipt_sha256": receipt_hash,
                     "original_review_sha256": file_hash(p / "review.json"),
                     "original_review_verdict_preserved": review["verdict"],
                     "supplemental_formal_verdict": "FAIL",
                     "reason": "semantic_review_sent_with_no_referenced_authoritative_evidence",
                     "report_numeric_content_is_not_the_same_as_a_grounded_model_review": True,
                     "original_receipt_grading_and_review_modified": False,
                     "provider_requests": 0}
            save(p / "post-audit.json", audit)
            save(HERE / (p.name + "-post-audit.json"), audit)
            evidence_checks.append({"slot_id": slot["slot_id"], "receipt_sha256": receipt_hash,
                                    "http_request_sha256": file_hash(p / "http-request-2.json"),
                                    "source_evidence_id": item["id"], "referenced_source_ids": claim_refs,
                                    "sent_source_count": len(attached), "candidate_characters": chars,
                                    "maximum_characters": 28000, "overflow_characters": chars - 28000,
                                    "payload_field_characters": {k: len(json.dumps(v, ensure_ascii=False))
                                                                 for k, v in item["payload"].items()},
                                    "model_rejected_claims": r["model_invocations"][-1]["validated_output"]["unsupported_claim_ids"]})
        rows.append({"slot_id": slot["slot_id"], "directory": str(p.relative_to(ROOT)),
                     "receipt_sha256": receipt_hash, "original_review_sha256": file_hash(p / "review.json"),
                     "automatic_passed": r["grading"]["automatic_passed"],
                     "initial_review": review["verdict"], "execution_failure": summary["failure_reason"],
                     "semantic_context_admission_passed": context_pass,
                     "strict_joint_passed": review["verdict"] == "PASS" and context_pass is not False,
                     "new_http_requests": summary["new_http_requests"],
                     "new_known_tokens": summary["new_known_tokens"],
                     "model_unknowns": sum(x["status"] == "UNKNOWN" for x in r["model_invocations"])})
    accounting = load_parent(failed)
    paid = sum(x["new_known_tokens"] for x in rows)
    requests = sum(x["new_http_requests"] for x in rows)
    assert paid == 72552 and requests == 10 and accounting["known_tokens"] == 11049569 + paid
    assert accounting["confirmed_http_requests"] == 1587 + requests
    assert not failed.with_name(failed.name + ".budget-continuation.json").exists()
    assert len(list(RUN.glob("H*-*/receipt.json"))) == 5
    assert all(not (RUN / s["slot_id"].replace("/", "-")).exists() for s in m["slots"][5:])
    baseline = read(ROOT / "docs/acceptance/boundary-formal-repair-20261027/baseline.json")
    assert all(file_hash(ROOT / name) == checksum for name, checksum in baseline["protected_files"].items())
    save(HERE / "root-cause-evidence.json", {"source_sha256": implementation_manifest()[1],
         "executed_case_ids": ["H01"], "remaining_U_cases_not_replayed": True,
         "local_scope": "Read persisted actual requests and call context packer on executed H01 only; no Worker/tool/model execution",
         "provider_requests_in_analysis": 0, "fixed_contexts": evidence_checks})
    save(HERE / "results.json", {"status": "FAIL_STOPPED_NO_AUTOMATIC_RETRY",
         "implementation_sha256": m["implementation_sha256"], "formal_plan_sha256": file_hash(PACKET / "manifest.json"),
         "slots_planned": 96, "slots_executed": 5, "slots_unexecuted": 91,
         "automatic_passed": 5, "initial_manual_passed": 4,
         "strict_joint_passed_after_context_audit": 2, "fixed_context_admission_failures": 3,
         "new_http_requests": requests, "new_known_tokens": paid,
         "new_unknown": 0, "known_cumulative_tokens": accounting["known_tokens"],
         "confirmed_cumulative_http_requests": accounting["confirmed_http_requests"],
         "legacy_unknown_count": 1, "legacy_unknown_frozen_tokens": 1051576,
         "occupied_tokens": accounting["known_tokens"] + 1051576,
         "remaining_tokens": 20000000 - accounting["known_tokens"] - 1051576,
         "cumulative_limit": 20000000, "raw_failure_preserved": True,
         "protected_original_files_unchanged": 330, "business_and_evaluation_source_modified": False,
         "G6": "NOT_RELEASED", "G7": "EXCLUDED", "rows": rows,
         "unexecuted_slots": [s["slot_id"] for s in m["slots"][5:]]})
    print(json.dumps({"executed": 5, "unexecuted": 91, "new_http": requests, "new_tokens": paid,
                      "strict_joint_passed": 2, "known_tokens": accounting["known_tokens"],
                      "remaining_tokens": 20000000 - accounting["known_tokens"] - 1051576}))


if __name__ == "__main__":
    main()
