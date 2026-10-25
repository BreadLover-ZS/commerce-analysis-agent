"""Exercise only copied packet metadata; never run a Task, tool or model."""

import argparse
import json
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from verify import verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    package = Path(__file__).resolve().parent
    network = []

    def prohibit_network(event, arguments):
        if event == "socket.connect":
            network.append(event)
            raise RuntimeError("network prohibited in metadata controls")

    sys.addaudithook(prohibit_network)
    mutations = [
        (
            "query",
            "cases.json",
            lambda x: x["cases"][0].update(query=x["cases"][0]["query"] + " 修改"),
        ),
        (
            "raw_payment_count",
            "cases.json",
            lambda x: x["cases"][0]["sales"]["daily_sales"][0].update(
                paid_units=x["cases"][0]["sales"]["daily_sales"][0]["paid_units"] + 1
            ),
        ),
        (
            "positive_exploration_stop",
            "cases.json",
            lambda x: x["cases"][0]["expected"]["stop_reasons"].append("exploration_complete"),
        ),
        (
            "excluded_author_scope",
            "cases.json",
            lambda x: x["cases"][13].update(scope_skus=x["cases"][13]["scope_skus"][1:]),
        ),
        (
            "daily_scope",
            "cases.json",
            lambda x: x["cases"][12].update(scope_skus=x["cases"][12]["scope_skus"][:1]),
        ),
        (
            "category_dimension",
            "semantic-contract.json",
            lambda x: x["cases"]["H09"].update(dimension="sku"),
        ),
        ("missing_case", "semantic-contract.json", lambda x: x["cases"].pop("H15")),
        (
            "missing_count_item",
            "answer-obligations.json",
            lambda x: x["cases"][14]["raw_matrix"].pop(),
        ),
        (
            "wrong_amount",
            "answer-obligations.json",
            lambda x: x["cases"][0]["raw_matrix"][0].update(raw_integer=999),
        ),
        (
            "gap_limit",
            "answer-obligations.json",
            lambda x: x["cases"][0]["gap_inventory_policy"].update(display_limit=19),
        ),
        ("stale_source", "manifest.json", lambda x: x.update(implementation_sha256="0" * 64)),
        ("history_tokens", "manifest.json", lambda x: x["history"].update(known_tokens=0)),
        (
            "budget_limit",
            "manifest.json",
            lambda x: x["budget"].update(proposed_cumulative_limit=12000000),
        ),
        ("fake_provenance", "provenance.json", lambda x: x.update(input_hashes=[])),
    ]
    mutations.append(
        (
            "missing_scope_item",
            "answer-obligations.json",
            lambda x: x["cases"][1]["scope_matrix"].pop(),
        )
    )
    controls = []
    with TemporaryDirectory(prefix="commerce-v14-plan-controls-") as temporary:
        baseline = Path(temporary) / "baseline"
        shutil.copytree(package, baseline)
        assert verify(baseline)["validation_passed"]
        controls.append(
            {"control": "unchanged_copy", "passed": True, "expected": "PASS", "actual": "PASS"}
        )
        for name, filename, mutate in mutations:
            directory = Path(temporary) / name
            shutil.copytree(package, directory)
            path = directory / filename
            value = json.loads(path.read_bytes())
            mutate(value)
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            try:
                verify(directory)
            except (ValueError, KeyError) as exc:
                controls.append(
                    {
                        "control": name,
                        "passed": True,
                        "expected": "REJECT",
                        "actual": "REJECT",
                        "reason": str(exc),
                    }
                )
            else:
                raise AssertionError("tampered metadata accepted: " + name)
    assert not network
    result = {
        "controls": controls,
        "passed": len(controls),
        "failed": 0,
        "http_attempts": 0,
        "task_model_tool_executions": 0,
        "scope": "fresh packet metadata copies only; no graph, tool, model or Task execution",
    }
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
