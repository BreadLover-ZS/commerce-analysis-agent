"""A literal-author counterexample to a shared lexical blind spot; no Task execution."""

import json

from prepare import BASE, save

from app.commerce.answer_contract_v4 import request_contract
from evaluations.diagnosis import implementation_manifest
from evaluations.diagnosis_request_checks_v3 import boundary_request


def main():
    variants = [
        (
            "natural_synonym",
            "请给出授权范围本期已有净销售额，说明比较期（早于启用日）不适用的边界，不补造比较期零值。",
        ),
        (
            "frozen_canonical_wording",
            "请给出授权范围本期已有净销售额，说明比较期（早于启用日）不可比较的边界，不补造比较期零值。",
        ),
    ]
    rows = []
    for label, query in variants:
        production = request_contract(query)["boundary_intent"]
        independent = boundary_request(query)
        rows.append(
            {
                "label": label,
                "query": query,
                "literal_author_expected_boundary": True,
                "production_boundary_intent": production,
                "independent_boundary_intent": independent,
                "matches_author": production and independent,
            }
        )
    assert [r["matches_author"] for r in rows] == [False, True]
    result = {
        "status": "KNOWN_METADATA_SEMANTIC_FAILURE_NOT_REPAIRED",
        "implementation_sha256": implementation_manifest()[1],
        "root_cause": "Both lexical boundary detectors omit the synonymous pre-activation phrase; agreement is not independent semantic truth.",
        "current_formal_packet_uses_canonical_wording": True,
        "canonical_wording_is_not_a_fix_or_generalization_evidence": True,
        "required_before_formal_paid_testing": "Add literal-author independent paraphrase counterexamples, versioned semantic fix and local verification; refreeze source and packet afterward.",
        "fresh_case_graph_tool_model_executions": 0,
        "provider_requests": 0,
        "does_not_claim_final_report_behavior_was_executed": True,
        "rows": rows,
    }
    save(BASE / "known-language-boundary.json", result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
