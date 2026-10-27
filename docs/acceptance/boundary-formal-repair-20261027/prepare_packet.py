"""Refreeze the still-unexecuted U source data; metadata only, never Worker/tools/model."""
import json
from pathlib import Path

from evaluations.diagnosis_grading import VERSION_CONTRACTS
from evaluations.diagnosis_packet_checks import check_packet
from evaluations.diagnosis_probe_accounting import file_hash

ROOT = Path(__file__).resolve().parents[3]
OLD = ROOT / "docs/acceptance/diagnosis-v16-formal-U-20261027"
NEW = ROOT / "docs/acceptance/diagnosis-boundary-v2-formal-U-20261027"


def save(name, value):
    (NEW / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main():
    NEW.mkdir(exist_ok=False)
    names = ["cases.json", "answer-obligations.json", "semantic-contract.json",
             "execution-plan.json", "provenance.json"]
    values = {name: json.loads((OLD / name).read_bytes()) for name in names}
    cases = values["cases.json"]
    rows = cases["cases"] if isinstance(cases, dict) else cases
    h07 = next(c for c in rows if c["case_id"] == "H07")
    assert "不可比较的边界" in h07["query"]
    h07["query"] = h07["query"].replace("不可比较的边界", "不适用的边界")
    author = values["answer-obligations.json"]
    next(c for c in author["cases"] if c["case_id"] == "H07")["query"] = h07["query"]
    semantic = values["semantic-contract.json"]
    semantic.update(schema_version=5, expected_answer_boundary_version=2,
                    expected_answer_contract_version="answer-contract-v5",
                    expected_runtime=VERSION_CONTRACTS["diagnosis-v16"],
                    expected_fixed_runtime=VERSION_CONTRACTS["commerce-review-bundle-v6"],
                    accounting_protocol=2, author_obligations=author)
    values["provenance.json"].update(
        refrozen_from=str(OLD.relative_to(ROOT)),
        refrozen_file_sha256={n: file_hash(OLD / n) for n in names},
        same_unexecuted_U_raw_data=True, refreeze_is_not_an_additional_fresh_family=True,
        fresh_case_graph_tool_model_executions=0,
        query_change="H07 natural synonym restored: 不适用的边界",
        semantic_contract="schema5 / answer-contract-v5 / frozen boundary policy2",
    )
    for name, value in values.items():
        save(name, value)
    _, _, contract = check_packet(NEW / "cases.json")
    save("packet-contract.json", contract)
    print(json.dumps(contract, ensure_ascii=False))


if __name__ == "__main__":
    main()
