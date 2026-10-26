"""Recompose original 4/4/3 H03 responses with v5; not new Tasks or quality scores."""

import argparse
import copy
import hashlib
import json
import socket
from pathlib import Path

from pypdf import PdfReader

from app.commerce.review_report_v5 import compose
from app.research.evidence import render_markdown, validate_report
from app.utils.word_converter import convert_md_to_pdf
from evaluations.diagnosis import implementation_manifest

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "evaluations/results/diagnosis-v15-h01-h20-live-20261026-01"


def main(directory):
    def no_network(*args, **kwargs):
        raise AssertionError("Saved-response replay forbids network")

    socket.socket.connect = no_network
    directory.mkdir()
    results = []
    for number in (1, 2, 3):
        path = RAW / f"H03-{number}/fixed.json"
        original = path.read_bytes()
        record = json.loads(original)
        evidence = copy.deepcopy(record["evidence"])
        next(e for e in evidence if e["payload"].get("review_profile") == "commerce_review")[
            "payload"
        ]["answer_version"] = 5
        response = next(
            json.loads(choice["message"]["content"])
            for trace in record["provider_trace"]
            for choice in trace["response"].get("choices", [])
            if choice["message"].get("content", "").startswith('{"analysis"')
        )
        report, composition = compose(evidence, [], response)
        validation = validate_report(report, evidence)
        assert validation["passed"]
        task = copy.deepcopy(record["task"])
        task["mode"] = "demo"
        markdown = (
            "> 本文件为保存响应的离线重组验证，不是新Task或新的正式成绩。\n\n"
            + render_markdown(task, report, evidence, validation)
        )
        stem = directory / f"H03-{number}"
        stem.with_suffix(".json").write_text(
            json.dumps(
                {
                    "scope": "OFFLINE_RECOMPOSITION_NOT_A_TASK_OR_FORMAL_SCORE",
                    "report": report.model_dump(mode="json"),
                    "composition": composition,
                    "validation": validation,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        stem.with_suffix(".md").write_text(markdown)
        assert convert_md_to_pdf(stem.with_suffix(".md"), stem.with_suffix(".pdf")).startswith(
            "成功转换:"
        )
        pdf = "".join(page.extract_text() for page in PdfReader(stem.with_suffix(".pdf")).pages)
        pdf = "".join(pdf.split())
        values = {
            m.value
            for section in report.sections
            for claim in section.claims
            for m in claim.metrics
        }
        assert all(str(value) in pdf for value in values)
        assert ("报告容量省略" in pdf) == (number in (1, 2))
        assert path.read_bytes() == original
        results.append(
            {
                "receipt": f"H03-{number}/fixed.json",
                "original_sha256": hashlib.sha256(original).hexdigest(),
                "selected": len(response["analysis"]),
                "kept": len(composition["kept"]),
                "omitted": len(composition["omitted"]),
                "max_claims": max(len(s.claims) for s in report.sections),
                "program_validation_passed": True,
                "json_md_pdf_values_and_disclosure_agree": True,
                "pdf_pages": len(PdfReader(stem.with_suffix(".pdf")).pages),
            }
        )
    value = {
        "implementation_sha256": implementation_manifest()[1],
        "scope": "SAVED_RESPONSE_COMPOSITION_ONLY_NOT_WORKER_OR_FORMAL_ACCEPTANCE",
        "results": results,
        "provider_requests": 0,
        "task_executions": 0,
        "original_receipts_modified": False,
    }
    (directory / "receipt.json").write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {"replayed": len(results), "json_md_pdf_consistent": True, "provider_requests": 0}
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args().output)
