"""Retain one reproducible diagnosis run without loading credentials or a business DB.

This is an isolated demo receipt, not the G6 evaluation or a real-model quality claim.
"""

import argparse
import asyncio
import hashlib
import json
import shutil
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from alembic import command
from alembic.config import Config
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
from app.core.files import safe_path
from app.diagnosis.inputs import create_bundle, digest
from app.diagnosis.schemas import InputBundleRequest
from app.diagnosis.store import DiagnosisStore
from app.research.schemas import TaskRequest
from app.storage.models import User
from app.storage.repository import Repository
from app.tasks.worker import Worker, checkpoint_store
from evaluations.commerce_review import fingerprint


def independent_expected(payload, skus=None):
    allowed = set(skus or [p.sku for p in payload.products])
    current = [
        r
        for r in payload.daily_sales
        if r.sku in allowed and date(2026, 9, 1) <= r.day <= date(2026, 9, 30)
    ]
    prior = [
        r
        for r in payload.daily_sales
        if r.sku in allowed and date(2026, 8, 2) <= r.day <= date(2026, 8, 31)
    ]
    expected_current = sum(
        max(0, (date(2026, 9, 30) - max(date(2026, 9, 1), p.active_from)).days + 1)
        for p in payload.products
        if p.sku in allowed
    )
    expected_prior = sum(
        max(0, (date(2026, 8, 31) - max(date(2026, 8, 2), p.active_from)).days + 1)
        for p in payload.products
        if p.sku in allowed
    )
    return {
        "net_cents": sum(r.revenue_cents - r.refund_cents for r in current),
        "comparison_net_cents": sum(r.revenue_cents - r.refund_cents for r in prior),
        "comparable": bool(
            expected_current
            and expected_prior
            and len(current) == expected_current
            and len(prior) == expected_prior
        ),
    }


async def run_demo(scenario, query, output):
    output.mkdir(parents=True, exist_ok=False)
    run_id, started = str(uuid4()), time.monotonic()
    payload = scenario_payload(scenario)
    expected = independent_expected(payload)
    with TemporaryDirectory(prefix="diagnosis-demo-") as directory:
        root = Path(directory)
        settings = Settings(
            _env_file=None,
            app_env="test",
            agent_mode="demo",
            data_dir=root,
            database_url="sqlite:///" + str(root / "app.sqlite"),
        )
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["settings"] = settings
        command.upgrade(config, "head")
        repo = Repository(settings)
        try:
            bootstrap_user(repo, settings)
            with repo.session() as db:
                user = db.scalar(select(User).where(User.email == settings.bootstrap_email))
            data = stage_dataset(
                repo,
                user,
                payload,
                "可复现模拟经营数据",
                True,
                hashlib.sha256(csv_bytes(payload)).hexdigest(),
            )
            commit_dataset(repo, user, data["id"])
            bundle = create_bundle(
                repo,
                user,
                InputBundleRequest(
                    sales_dataset_id=data["id"], period={"start": "2026-09-01", "end": "2026-09-30"}
                ),
            )
            task = repo.create_task(
                user,
                TaskRequest(
                    conversation_id=repo.create_conversation(user.id),
                    query=query,
                    profile="commerce_diagnosis",
                    bundle_id=bundle["bundle_id"],
                ),
                str(uuid4()),
            )
            async with checkpoint_store(settings) as saver:
                await Worker(repo, settings, saver).run_once()
            done, evidence = repo.get_task(task["id"]), repo.evidence(task["id"])
            projection = DiagnosisStore(repo).projection(task["id"], user)
            facts = (done["report"] or {}).get("facts", [])
            expected = independent_expected(
                payload, (done["report"] or {}).get("scope", {}).get("skus")
            )
            comparison = next(
                (e["payload"] for e in evidence if e["payload"]["tool"] == "compare_periods"), {}
            )
            checks = {
                "terminal_report": done["status"] in {"succeeded", "degraded"},
                "program_validation": bool((done["validation"] or {}).get("passed")),
                "independent_net_cents": any(
                    f["metric_key"] == "net_revenue.current"
                    and Decimal(f["value"]) * 100 == expected["net_cents"]
                    for f in facts
                ),
                "independent_comparability": comparison.get("comparable") == expected["comparable"],
                "no_model_requests": done["metrics"]["model_calls"] == 0,
                "bundle_version": (done["report"] or {}).get("bundle_hash")
                == bundle["bundle_hash"],
                "step_and_tool_bounds": len(projection["steps"]) <= 8
                and done["metrics"]["tool_calls"] <= 10,
            }
            receipt = {
                "run_id": run_id,
                "created_at": datetime.now(UTC).isoformat(),
                "passed": all(checks.values()),
                "scope": "isolated_sqlite_demo_policy_not_real_model_or_production",
                "implementation_sha256": fingerprint(),
                "entrypoint_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "input_hash": digest(canonical(payload)),
                "expected_hash": digest(expected),
                "checks": checks,
                "expected": expected,
                "metrics": done["metrics"],
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "real_model_review": "UNTESTED",
                "manual_business_review": "UNTESTED",
                "estimated_model_cost": None,
            }
            values = {
                "receipt.json": receipt,
                "input.json": canonical(payload),
                "bundle.json": bundle,
                "task.json": done,
                "diagnosis.json": projection,
                "events.json": repo.events(task["id"], limit=500),
            }
            for name, value in values.items():
                (output / name).write_text(
                    json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            for artifact in repo.artifacts(task["id"]):
                from app.storage.models import Artifact

                with repo.session() as db:
                    row = db.get(Artifact, artifact["id"])
                    source = safe_path(root / "artifacts", row.storage_key)
                shutil.copyfile(source, safe_path(output, artifact["name"]))
            return receipt
        finally:
            repo.engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="隔离的经营诊断 demo；不读取真实模型凭据")
    parser.add_argument("--scenario", choices=SCENARIOS, default="sales_decline")
    parser.add_argument(
        "--query", default="本期净销售额为什么下降，主要涉及哪些商品，退款变化能解释多少？"
    )
    parser.add_argument("--output", type=Path, required=True, help="必须是尚不存在的目录")
    args = parser.parse_args()
    receipt = asyncio.run(run_demo(args.scenario, args.query, args.output))
    print(
        json.dumps(
            {"run_id": receipt["run_id"], "passed": receipt["passed"], "output": str(args.output)},
            ensure_ascii=False,
        )
    )
    if not receipt["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
