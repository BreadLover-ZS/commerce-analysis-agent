import hashlib
import json

import pytest

from evaluations.run import load_cases, research_execution_ok


def test_selected_case_fingerprint_matches_executed_content_only(tmp_path):
    path = tmp_path / "cases.jsonl"
    first = b'{"id":"first","request":{"query":"a"}}\n'
    path.write_bytes(first + b'{"id":"second","request":{"query":"b"}}\n')
    selected, fingerprint = load_cases(path, ["first"])
    assert [case["id"] for case in selected] == ["first"]
    assert fingerprint == hashlib.sha256(first).hexdigest()
    path.write_bytes(first + b'{"id":"second","request":{"query":"changed"}}\n')
    assert load_cases(path, ["first"])[1] == fingerprint
    assert load_cases(path)[1] == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("selection", [["unknown"], ["first", "first"], []])
def test_case_selection_rejects_unknown_or_duplicate_ids(tmp_path, selection):
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps({"id": "first"}) + "\n")
    with pytest.raises(ValueError):
        load_cases(path, selection)


def test_duplicate_source_cases_are_rejected_before_execution(tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text('{"id":"first"}\n{"id":"first"}\n')
    with pytest.raises(ValueError, match="ID重复"):
        load_cases(path)


@pytest.mark.parametrize(
    "evidence,research,expected",
    [
        ([], None, False),
        ([{"kind": "document"}], {"status": "failed"}, False),
        ([{"kind": "document"}], {"status": "completed"}, True),
        ([{"kind": "metrics"}], {"status": "not_required"}, True),
        ([{"kind": "document"}], {"status": "not_required"}, False),
        ([{"kind": "document"}], {"status": "unknown"}, False),
    ],
)
def test_live_research_gate_requires_retained_stage_evidence(evidence, research, expected):
    assert research_execution_ok("live", evidence, research) is expected
