"""Isolated review evaluation. Demo checks contracts; live additionally checks provider execution.

Never connects to the configured business DB. Stops on the first failed case and retains
receipts. Human semantic review is recorded separately from automatic checks.
"""

import argparse
import asyncio
import hashlib
import json
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from sqlalchemy import select

from app.commerce.datasets import (
    SCENARIOS,
    canonical,
    commit_dataset,
    csv_bytes,
    scenario_payload,
    stage_dataset,
)
from app.core.auth import bootstrap_user
from app.core.config import Settings
from app.research.evidence import REVIEW_SECTIONS, deterministic_report
from app.research.schemas import TaskRequest
from app.storage.models import Base, User
from app.storage.repository import Repository
from app.tasks.worker import Worker, checkpoint_store


def fingerprint():
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(
        [
            *root.glob("app/**/*.py"),
            *root.glob("evaluations/*.py"),
            *root.glob("migrations/**/*.py"),
            root / "uv.lock",
        ]
    ):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


async def evaluate(mode, cases, repeats, output):
    output.mkdir(parents=True, exist_ok=False)
    base = Settings()
    if mode == "live" and not (base.openai_api_key.get_secret_value() and base.llm_model):
        raise ValueError("live评估需要现有模型配置")
    results = {
        "run_id": str(uuid4()),
        "timestamp": datetime.now(UTC).isoformat(),
        "mode": mode,
        "implementation_sha256": fingerprint(),
        "passed": True,
        "scope": "模拟数据经营复盘；非真实业务效果或生产容量验收",
        "manual_review": "pending" if mode == "live" else "not_applicable",
        "cases": [],
    }
    with TemporaryDirectory(prefix="commerce-review-eval-") as directory:
        root = Path(directory)
        settings = Settings(
            _env_file=None,
            app_env="test",
            agent_mode=mode,
            data_dir=root,
            database_url="sqlite:///" + str(root / "app.sqlite"),
            openai_api_key=base.openai_api_key,
            openai_base_url=base.openai_base_url,
            llm_model=base.llm_model,
            semantic_review=True,
            max_model_calls=6,
            max_tokens=20000,
            max_runtime_seconds=180,
            model_read_timeout_seconds=base.model_read_timeout_seconds,
            model_connect_timeout_seconds=base.model_connect_timeout_seconds,
        )
        repo = Repository(settings)
        try:
            Base.metadata.create_all(repo.engine)
            bootstrap_user(repo, settings)
            with repo.session() as db:
                user = db.scalar(select(User).where(User.email == "demo@example.com"))
            async with checkpoint_store(settings) as saver:
                for case in cases:
                    for repeat in range(repeats):
                        payload = scenario_payload(case)
                        info = stage_dataset(
                            repo,
                            user,
                            payload,
                            "模拟：" + SCENARIOS[case],
                            True,
                            hashlib.sha256(csv_bytes(payload)).hexdigest(),
                        )
                        commit_dataset(repo, user, info["id"])
                        task = repo.create_task(
                            user,
                            TaskRequest(
                                conversation_id=repo.create_conversation(user.id),
                                query="复盘本期经营表现、商品变化贡献和退款，区分观察和待验证原因。",
                                profile="commerce_review",
                                dataset_id=info["id"],
                                sources=["sales"],
                                strategy="workflow",
                                start_date=date(2026, 9, 1),
                                end_date=date(2026, 9, 30),
                            ),
                            str(uuid4()),
                        )
                        started = time.monotonic()
                        await Worker(repo, settings, saver).run_once()
                        done = repo.get_task(task["id"])
                        evidence = repo.evidence(task["id"])
                        report, validation = done["report"], done["validation"] or {}
                        template = deterministic_report(evidence, []) if evidence else None
                        expected_net = sum(
                            s.revenue_cents - s.refund_cents
                            for s in payload.daily_sales
                            if date(2026, 9, 1) <= s.day <= date(2026, 9, 30)
                        )
                        source = evidence[0]["payload"] if evidence else {}
                        checks = {
                            "terminal_report": done["status"] in {"succeeded", "degraded"}
                            and report is not None,
                            "structure": validation.get("passed") is True,
                            "sections": bool(report)
                            and [s["name"] for s in report["sections"]] == REVIEW_SECTIONS,
                            "independent_net_cents": source.get("metrics", {}).get("net_revenue")
                            == f"{Decimal(expected_net) / 100:.2f}",
                            "version": source.get("dataset_hash") == info["content_hash"],
                            "locked_facts": bool(report and template)
                            and report["sections"][:3]
                            == [s.model_dump() for s in template.sections[:3]],
                            "execution": done["metrics"]["model_calls"] == 0
                            if mode == "demo"
                            else (
                                done["metrics"]["model_calls"] > 0
                                and done["metrics"]["usage_complete"]
                                and validation.get("semantic_status") == "model_reviewed"
                                and not validation.get("fallback_used")
                                and not validation.get("draft_fallback_used")
                            ),
                        }
                        case_dir = output / f"{case}-{repeat + 1}"
                        case_dir.mkdir()
                        (case_dir / "fixture.json").write_text(
                            json.dumps(canonical(payload), ensure_ascii=False, indent=2)
                        )
                        for artifact in repo.artifacts(task["id"]):
                            # Copy only known report artifacts from this disposable run.
                            with repo.session() as db:
                                from app.storage.models import Artifact

                                item = db.get(Artifact, artifact["id"])
                            (case_dir / item.name).write_bytes(
                                (root / "artifacts" / item.storage_key).read_bytes()
                            )
                        receipt = {
                            "case": case,
                            "repeat": repeat + 1,
                            "checks": checks,
                            "passed": all(checks.values()),
                            "elapsed_seconds": round(time.monotonic() - started, 3),
                            "task": done,
                            "events": repo.events(task["id"]),
                        }
                        (case_dir / "receipt.json").write_text(
                            json.dumps(receipt, ensure_ascii=False, indent=2)
                        )
                        results["cases"].append(
                            {
                                k: receipt[k]
                                for k in ("case", "repeat", "checks", "passed", "elapsed_seconds")
                            }
                        )
                        results["passed"] = all(r["passed"] for r in results["cases"])
                        (output / "summary.json").write_text(
                            json.dumps(results, ensure_ascii=False, indent=2)
                        )
                        print(json.dumps(results["cases"][-1], ensure_ascii=False), flush=True)
                        if not results["passed"]:
                            return results
        finally:
            repo.engine.dispose()
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["demo", "live"], default="demo")
    parser.add_argument("--cases", nargs="+", choices=list(SCENARIOS), default=list(SCENARIOS))
    parser.add_argument("--repeats", type=int, choices=[1, 2, 3], default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(evaluate(args.mode, args.cases, args.repeats, args.output))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
