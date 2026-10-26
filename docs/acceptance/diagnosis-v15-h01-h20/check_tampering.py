"""Reject altered copied metadata; no graph/model execution and no provider requests."""

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from verify import verify


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))


def main(output):
    root = Path(__file__).resolve().parent
    network = []

    def deny(event, args):
        if event == "socket.connect":
            network.append(event)
            raise RuntimeError("network forbidden")

    sys.addaudithook(deny)
    mutations = [
        "question",
        "raw_sales",
        "target_only_boundary",
        "missing_required_item",
        "wrong_runtime",
        "wrong_fixed_runtime",
        "daily_scope",
        "author_integer",
        "author_unit",
        "author_daily",
        "budget_limit",
        "unknown_assertion",
    ]
    rows = []
    for mode in mutations:
        with TemporaryDirectory(prefix="v15-packet-control-") as temp:
            directory = Path(temp) / "packet"
            shutil.copytree(root, directory)
            cases = json.loads((directory / "cases.json").read_bytes())
            sem = json.loads((directory / "semantic-contract.json").read_bytes())
            answer = json.loads((directory / "answer-obligations.json").read_bytes())
            manifest = json.loads((directory / "manifest.json").read_bytes())
            if mode == "question":
                cases["cases"][2]["query"] += "只要本期。"
            if mode == "raw_sales":
                cases["cases"][2]["sales"]["daily_sales"][0]["revenue_cents"] += 1
            if mode == "target_only_boundary":
                cases["cases"][5]["expected"]["stop_reasons"] = ["target_covered"]
            if mode == "missing_required_item":
                answer["cases"][2]["scope_matrix"].pop()
            if mode == "wrong_runtime":
                sem["expected_runtime"]["graph_version"] = "diagnosis-v14"
            if mode == "wrong_fixed_runtime":
                sem["expected_fixed_runtime"]["tool_registry_version"] = "commerce-review-bundle-v3"
            if mode == "daily_scope":
                cases["cases"][12]["scope_skus"] = cases["cases"][12]["scope_skus"][:1]
            if mode == "author_integer":
                answer["cases"][2]["scope_matrix"][0]["raw_integer"] += 1
            if mode == "author_unit":
                answer["cases"][2]["scope_matrix"][0]["unit"] = "count"
            if mode == "author_daily":
                answer["cases"][12]["daily_matrix"][0]["net_cents"] += 1
            if mode == "budget_limit":
                manifest["budget"]["proposed_cumulative_limit"] += 1
            if mode == "unknown_assertion":
                cases["cases"][0]["expected"]["required_assertions"].append("assume_pass")
            if mode.startswith("author_") or mode == "missing_required_item":
                sem["author_obligations"] = answer
            save(directory / "cases.json", cases)
            save(directory / "semantic-contract.json", sem)
            save(directory / "answer-obligations.json", answer)
            # Independently recomputed arithmetic must reject even when a caller
            # updates the manifest's sidecar hash to match its forged author table.
            if mode.startswith("author_"):
                manifest["semantic_contract_sha256"] = hashlib.sha256(
                    (directory / "semantic-contract.json").read_bytes()
                ).hexdigest()
            save(directory / "manifest.json", manifest)
            rejected = False
            try:
                verify(directory)
            except (ValueError, KeyError):
                rejected = True
            rows.append({"mutation": mode, "rejected": rejected})
    result = {
        "checks": len(rows),
        "passed": sum(r["rejected"] for r in rows),
        "formal_packet_executions": 0,
        "provider_requests": 0,
        "network_attempts": len(network),
        "rows": rows,
    }
    with output.open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    assert all(r["rejected"] for r in rows), result
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args().output)
