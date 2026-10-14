"""Human annotation preparation and strict, evidence-bound strategy comparison."""

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

DIMENSIONS = ("correctness", "support", "completeness", "tool_behavior")


def annotation_template(path):
    run = json.loads(path.read_text())
    return {
        "run_id": run["run_id"],
        "results_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "scale": "0=错误/缺失；1=部分满足；2=满足；必须逐项人工核对原文，不预填评分",
        "cases": [
            {
                "case_id": result["case_id"],
                "rubric": result["rubric"],
                "scores": {key: None for key in DIMENSIONS},
                "comment": "",
            }
            for result in run["results"]
        ],
    }


def percentile(values, fraction):
    values = sorted(values)
    position = (len(values) - 1) * fraction
    lower = int(position)
    return values[lower] + (values[min(lower + 1, len(values) - 1)] - values[lower]) * (
        position - lower
    )


def compare_runs(paths, annotation_dir):
    groups, seen, signature = defaultdict(list), set(), None
    for path in paths:
        run = json.loads(path.read_text())
        if run["mode"] != "live" or run["evaluation_type"] != "live_application_evaluation":
            raise ValueError("demo不能用于Agent质量对比")
        if run["run_id"] in seen:
            raise ValueError("重复run_id不能充当重复实验")
        seen.add(run["run_id"])
        current = (
            run["cases_sha256"],
            run["fixture_sha256"],
            run["configuration"],
            run["implementation_sha256"],
        )
        if signature is not None and current != signature:
            raise ValueError("数据集、业务样例、代码或模型/预算配置不一致")
        signature = current
        if run["executed"] != run["dataset_size"]:
            raise ValueError("轮次未完成；先处理首个失败并重跑，不能将缺失用例计成通过")
        labels_path = annotation_dir / f"{run['run_id']}.json"
        labels = json.loads(labels_path.read_text())
        if (
            labels["run_id"] != run["run_id"]
            or labels["results_sha256"] != hashlib.sha256(path.read_bytes()).hexdigest()
        ):
            raise ValueError("人工标注与当前结果文件不匹配")
        if len({v["case_id"] for v in labels["cases"]}) != len(labels["cases"]):
            raise ValueError("人工标注用例重复")
        by_id = {v["case_id"]: v for v in labels["cases"]}
        if set(by_id) != {v["case_id"] for v in run["results"]}:
            raise ValueError("人工标注缺失或包含未知用例")
        for result in run["results"]:
            label = by_id[result["case_id"]]
            if set(label["scores"]) != set(DIMENSIONS) or any(
                type(v) is not int or v not in {0, 1, 2} for v in label["scores"].values()
            ):
                raise ValueError("人工评分未完成或格式错误")
            if min(label["scores"].values()) < 2 and not label["comment"].strip():
                raise ValueError("未满分项必须说明失败/不足依据")
        groups[run["strategy"]].append((run, by_id))
    if set(groups) != {"workflow", "single", "multi"} or any(len(v) < 3 for v in groups.values()):
        raise ValueError("workflow/single/multi每种至少3个不同完整轮次")
    output = {
        "quality_source": "human_annotations",
        "strategies": {},
        "warnings": ["样本与重复轮次不代表统计显著性；人工标签也需要核验"],
    }
    if any(not run["sources_frozen"] for runs in groups.values() for run, _ in runs):
        output["warnings"].append(
            "外部来源未冻结，差异不能完全归因于Agent架构；核对每轮保留的证据快照"
        )
    for strategy, runs in groups.items():
        results = [
            (result, labels[result["case_id"]]) for run, labels in runs for result in run["results"]
        ]
        metrics = [result["task"]["metrics"] for result, _ in results]
        runtimes = [m["runtime_ms"] for m in metrics]
        output["strategies"][strategy] = {
            "rounds": len(runs),
            "cases": len(results),
            "contract_pass_rate": mean(result["passed"] for result, _ in results),
            "human_scores_0_to_2": {
                dimension: mean(label["scores"][dimension] for _, label in results)
                for dimension in DIMENSIONS
            },
            "fallback_rate": mean(
                bool(
                    (result["task"].get("validation") or {}).get("fallback_used")
                    or (result["task"].get("validation") or {}).get("draft_fallback_used")
                )
                for result, _ in results
            ),
            "usage_complete_rate": mean(m["usage_complete"] for m in metrics),
            "actual_delegation_rate": mean(
                any(e["kind"] == "delegation" for e in result["events"]) for result, _ in results
            ),
            "runtime_p50_ms": percentile(runtimes, 0.5),
            "runtime_p95_ms": percentile(runtimes, 0.95),
            "average_model_calls": mean(m["model_calls"] for m in metrics),
            "average_tokens": mean(m["input_tokens"] + m["output_tokens"] for m in metrics),
            "average_estimated_model_cost": mean(m["estimated_cost"] for m in metrics)
            if all(m.get("estimated_cost") is not None for m in metrics)
            else None,
        }
    return output


def main():
    parser = argparse.ArgumentParser(description="人工标注和三策略对比；不自动伪造质量评分")
    sub = parser.add_subparsers(dest="command", required=True)
    template = sub.add_parser("template")
    template.add_argument("result", type=Path)
    template.add_argument("--output", type=Path, required=True)
    compare = sub.add_parser("compare")
    compare.add_argument("results", type=Path, nargs="+")
    compare.add_argument("--annotations", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        data = (
            annotation_template(args.result)
            if args.command == "template"
            else compare_runs(args.results, args.annotations)
        )
    except (ValueError, KeyError, FileNotFoundError) as exc:
        raise SystemExit(f"评估未完成：{exc}") from exc
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已保存：{args.output}")


if __name__ == "__main__":
    main()
