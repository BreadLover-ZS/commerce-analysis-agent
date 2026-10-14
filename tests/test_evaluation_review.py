import json
from uuid import uuid4

import pytest

from evaluations.review import annotation_template, compare_runs


def make_run(tmp_path, strategy, mode="live"):
    run_id = str(uuid4())
    data = {
        "run_id": run_id,
        "mode": mode,
        "evaluation_type": "live_application_evaluation",
        "cases_sha256": "cases",
        "fixture_sha256": "fixtures",
        "implementation_sha256": "code",
        "configuration": {"model": "protocol-fixture", "max_tokens": 1000},
        "dataset_size": 1,
        "executed": 1,
        "strategy": strategy,
        "sources_frozen": False,
        "results": [
            {
                "case_id": "case",
                "rubric": "人工核验",
                "passed": True,
                "events": [],
                "task": {
                    "validation": {},
                    "metrics": {
                        "runtime_ms": 10,
                        "usage_complete": True,
                        "model_calls": 2,
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "estimated_cost": None,
                    },
                },
            }
        ],
    }
    path = tmp_path / f"{run_id}.json"
    path.write_text(json.dumps(data))
    return path


def write_labels(path, directory, complete=True):
    labels = annotation_template(path)
    if complete:
        labels["cases"][0]["scores"] = {key: 2 for key in labels["cases"][0]["scores"]}
    destination = directory / f"{labels['run_id']}.json"
    destination.write_text(json.dumps(labels))
    return destination


def test_comparison_requires_live_complete_annotations_and_three_rounds(tmp_path):
    directory = tmp_path / "annotations"
    directory.mkdir()
    demo = make_run(tmp_path, "single", mode="demo")
    with pytest.raises(ValueError, match="demo"):
        compare_runs([demo], directory)
    one = make_run(tmp_path, "single")
    write_labels(one, directory, complete=False)
    with pytest.raises(ValueError, match="评分未完成"):
        compare_runs([one], directory)
    write_labels(one, directory)
    with pytest.raises(ValueError, match="至少3"):
        compare_runs([one], directory)
    with pytest.raises(ValueError, match="重复run_id"):
        compare_runs([one, one], directory)


def test_comparison_scores_only_matching_human_labels_and_preserves_limits(tmp_path):
    directory = tmp_path / "annotations"
    directory.mkdir()
    paths = [
        make_run(tmp_path, strategy)
        for strategy in ("workflow", "single", "multi")
        for _ in range(3)
    ]
    for path in paths:
        write_labels(path, directory)
    output = compare_runs(paths, directory)
    assert output["quality_source"] == "human_annotations"
    assert output["strategies"]["single"]["human_scores_0_to_2"]["support"] == 2
    assert output["strategies"]["multi"]["actual_delegation_rate"] == 0
    assert output["strategies"]["single"]["average_estimated_model_cost"] is None
    assert any("未冻结" in warning for warning in output["warnings"])
    data = json.loads(paths[0].read_text())
    data["configuration"]["model"] = "different-model"
    paths[0].write_text(json.dumps(data))
    write_labels(paths[0], directory)
    with pytest.raises(ValueError, match="不一致"):
        compare_runs(paths, directory)


def test_annotations_are_bound_to_exact_result_bytes(tmp_path):
    directory = tmp_path / "annotations"
    directory.mkdir()
    path = make_run(tmp_path, "single")
    write_labels(path, directory)
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="不匹配"):
        compare_runs([path], directory)
