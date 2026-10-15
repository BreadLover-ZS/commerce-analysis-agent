import argparse
import asyncio
import hashlib
import json
import statistics
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import select

from app.commerce.service import seed_commerce
from app.core.auth import bootstrap_user
from app.core.config import Settings
from app.research.evidence import numbers
from app.research.schemas import TaskRequest
from app.storage.models import Artifact, Base, User
from app.storage.repository import Repository
from app.tasks.worker import Worker, checkpoint_store
from evaluations.fixtures import prepare_case


def load_cases(path, case_ids=None):
    content = path.read_bytes()
    rows = [(line, json.loads(line)) for line in content.splitlines(keepends=True) if line.strip()]
    ids = [case["id"] for _, case in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("评估集用例ID重复")
    if case_ids is not None:
        if not case_ids or len(case_ids) != len(set(case_ids)):
            raise ValueError("选取的用例ID为空或重复")
        if set(case_ids) - set(ids):
            raise ValueError("选取了评估集中不存在的用例")
        rows = [(line, case) for line, case in rows if case["id"] in case_ids]
        content = b"".join(line for line, _ in rows)
    if not rows:
        raise ValueError("评估集为空")
    return [case for _, case in rows], hashlib.sha256(content).hexdigest()


def research_execution_ok(mode, evidence, research):
    if mode == "demo":
        return True
    if not research:
        return False
    return research.get("status") == "completed" or (
        research.get("status") == "not_required"
        and not any(e["kind"] != "metrics" for e in evidence)
    )


async def evaluate(args):
    original = Settings()
    updates = {"agent_mode": args.mode}
    if getattr(args, "max_model_calls", None) is not None:
        updates["max_model_calls"] = args.max_model_calls
    original = original.model_copy(update=updates)
    # Re-validate live credentials after an explicit mode override; never silently use demo.
    original = Settings(_env_file=None, **original.model_dump())
    cases, cases_hash = load_cases(args.cases, getattr(args, "case_ids", None))
    results = []
    with TemporaryDirectory(prefix="commerce-eval-") as directory:
        root = Path(directory)
        settings = original.model_copy(
            update={
                "database_url": "sqlite:///" + str(root / "app.sqlite"),
                "data_dir": root,
                "app_env": "test",
                "seed_demo": True,
            }
        )
        repo = Repository(settings)
        Base.metadata.create_all(repo.engine)
        bootstrap_user(repo, settings)
        seed_commerce(repo)
        with repo.session() as db:
            user = db.scalar(select(User).where(User.email == settings.bootstrap_email.lower()))
        async with checkpoint_store(settings) as saver:
            for case in cases:
                prepare_case(repo, case)
                request = TaskRequest(
                    conversation_id=repo.create_conversation(user.id),
                    strategy=args.strategy,
                    **case["request"],
                )
                task = repo.create_task(user, request, str(uuid4()))
                await Worker(repo, settings, saver).run_once()
                result = repo.get_task(task["id"])
                evidence = repo.evidence(task["id"])
                with repo.session() as db:
                    research_file = db.scalar(
                        select(Artifact).where(
                            Artifact.task_id == task["id"], Artifact.name == "research.json"
                        )
                    )
                research = (
                    json.loads((root / "artifacts" / research_file.storage_key).read_text())
                    if research_file
                    else None
                )
                claims = [
                    c
                    for section in (result.get("report") or {}).get("sections", [])
                    for c in section["claims"]
                ]
                cited_ids = {eid for c in claims for eid in c["evidence_ids"]}
                report_text = "\n".join(
                    [c["text"] for c in claims]
                    + (result.get("report") or {}).get("limitations", [])
                )
                checks = {
                    "research_execution": research_execution_ok(args.mode, evidence, research),
                    "agent_execution": args.mode == "demo"
                    or bool(
                        result["validation"]
                        and not result["validation"].get("fallback_used")
                        and not result["validation"].get("draft_fallback_used")
                        and result["metrics"]["usage_complete"]
                        and result["metrics"]["model_calls"] > 0
                        and (
                            not settings.semantic_review
                            or result["validation"]["semantic_status"] == "model_reviewed"
                        )
                    ),
                    "completed": result["status"] in {"succeeded", "degraded"},
                    "structure": bool(result["validation"] and result["validation"]["passed"]),
                    "section_coverage": bool(
                        result["report"]
                        and [s["name"] for s in result["report"]["sections"]]
                        == ["经营表现", "库存风险", "竞品观察", "行动建议"]
                    ),
                    "evidence_present": len(evidence) >= case.get("min_evidence", 0),
                    "expected_numbers": all(
                        any(
                            m["key"] == key
                            and m["value"] == value
                            and m["evidence_id"] in c["evidence_ids"]
                            and Decimal(value) in numbers(c["text"])
                            for c in claims
                            for m in c.get("metrics", [])
                        )
                        for key, value in case.get("expected_metrics", {}).items()
                    ),
                    "source_used_in_answer": all(
                        any(
                            e["id"] in cited_ids
                            for e in evidence
                            if (e["kind"], e["payload"].get("tool")) == group
                        )
                        for group in {(e["kind"], e["payload"].get("tool")) for e in evidence}
                    ),
                    "explicit_gap": not case.get("expect_gap")
                    or bool(result["report"] and result["report"]["limitations"]),
                    "required_content": all(
                        term in report_text for term in case.get("required_text", [])
                    ),
                    "forbidden_content": all(
                        term not in report_text for term in case.get("forbidden_text", [])
                    ),
                    "forbidden_metrics": all(
                        key not in e["payload"].get("metrics", {})
                        for key in case.get("forbidden_metrics", [])
                        for e in evidence
                    ),
                }
                # Retain requests, outputs and citations, not just a green summary.
                results.append(
                    {
                        "case_id": case["id"],
                        "request": request.model_dump(mode="json"),
                        "passed": all(checks.values()),
                        "checks": checks,
                        "task": result,
                        "evidence": evidence,
                        "events": repo.events(task["id"], limit=1000),
                        "research": research,
                        "rubric": case.get(
                            "rubric",
                            "逐项核对商品、单位、周期、依据与信息缺口；引用存在不等于结论正确",
                        ),
                    }
                )
                if not all(checks.values()):
                    break
        repo.engine.dispose()
    runtimes = [r["task"]["metrics"]["runtime_ms"] for r in results]
    provider = urlsplit(settings.openai_base_url)
    output = {
        "run_id": str(uuid4()),
        "timestamp": datetime.now(UTC).isoformat(),
        "mode": args.mode,
        "strategy": args.strategy,
        "evaluation_type": "deterministic_contracts"
        if args.mode == "demo"
        else "live_application_evaluation",
        "quality_limit": "规则校验不证明真实模型研究质量；需要人工逐项核对证据与业务内容",
        "dataset_size": len(cases),
        "executed": len(results),
        "passed": sum(r["passed"] for r in results),
        "median_runtime_ms": statistics.median(runtimes) if runtimes else None,
        "results": results,
        "cases_sha256": cases_hash,
        "fixture_sha256": hashlib.sha256(
            b"".join(p.read_bytes() for p in sorted(Path("data").rglob("*")) if p.is_file())
        ).hexdigest(),
        "implementation_sha256": hashlib.sha256(
            b"".join(
                p.as_posix().encode() + b"\0" + p.read_bytes()
                for p in sorted(
                    [
                        *Path("app").rglob("*.py"),
                        *Path("evaluations").rglob("*.py"),
                        Path("uv.lock"),
                    ]
                )
            )
        ).hexdigest(),
        "configuration": {
            "model": settings.llm_model,
            "provider_base_url": f"{provider.scheme}://{provider.hostname}"
            + (f":{provider.port}" if provider.port else "")
            + provider.path,
            "semantic_review": settings.semantic_review,
            "knowledge_provider": settings.knowledge_provider,
            "max_model_calls": settings.max_model_calls,
            "max_tool_calls": settings.max_tool_calls,
            "max_tokens": settings.max_tokens,
            "max_runtime_seconds": settings.max_runtime_seconds,
            "input_price_per_million": settings.input_price_per_million,
            "output_price_per_million": settings.output_price_per_million,
        },
        "sources_frozen": args.mode == "demo",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in output.items() if k != "results"}, ensure_ascii=False))
    if output["passed"] != len(cases):
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["demo", "live"], default="demo")
    parser.add_argument("--strategy", choices=["workflow", "single", "multi"], default="single")
    parser.add_argument("--cases", type=Path, default=Path("evaluations/cases.jsonl"))
    parser.add_argument(
        "--case", dest="case_ids", action="append", help="仅运行指定用例，可重复提供"
    )
    parser.add_argument("--max-model-calls", type=int, help="单用例模型调用上限，计入修正和SDK调用")
    parser.add_argument("--output", type=Path, default=Path("evaluations/results/latest.json"))
    asyncio.run(evaluate(parser.parse_args()))


if __name__ == "__main__":
    main()
