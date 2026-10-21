"""Isolated demo HTTP workload; cannot target an existing URL or business database."""

import argparse
import asyncio
import hashlib
import json
import math
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {"succeeded", "degraded", "failed", "cancelled", "rejected"}


def percentile(values, quantile):
    return (
        round(sorted(values)[max(0, math.ceil(len(values) * quantile) - 1)], 2) if values else None
    )


def _postgres(root):
    try:
        import pgserver
    except ImportError:
        raise ValueError("postgres演练需要uv run --with pgserver==0.1.4") from None
    server = pgserver.get_server(root / "pgdata", cleanup_mode="stop")
    try:
        base = make_url(server.get_uri()).set(drivername="postgresql")
        role, password, database = "bench_" + uuid4().hex, secrets.token_hex(20), "commerce_load"
        with psycopg.connect(base.render_as_string(hide_password=False), autocommit=True) as db:
            version = db.execute("SHOW server_version").fetchone()[0]
            db.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}"
                ).format(sql.Identifier(role), sql.Literal(password))
            )
            db.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(database), sql.Identifier(role)
                )
            )
        url = base.set(
            drivername="postgresql+psycopg", username=role, password=password, database=database
        )
        return server, url.render_as_string(hide_password=False), version
    except BaseException:
        server.cleanup()
        raise


def _response(response, expected=200):
    if response.status_code != expected:
        raise RuntimeError(f"HTTP状态异常:{response.status_code}")
    return response.json()


async def run_benchmark(
    requests=20, concurrency=4, workers=2, backend="sqlite", task_timeout=90, profile="research"
):
    if (
        not 1 <= requests <= 200
        or not 1 <= concurrency <= 16
        or not 1 <= workers <= 4
        or not 5 <= task_timeout <= 180
        or backend not in {"sqlite", "postgres"}
        or profile not in {"research", "commerce_review", "commerce_diagnosis"}
    ):
        raise ValueError("参数超出隔离演练范围")
    if os.name != "posix":
        raise ValueError("此隔离演练入口需Linux或macOS")
    processes, server = [], None
    with TemporaryDirectory(prefix="commerce-load-") as directory:
        root = Path(directory)
        # app.cli resolves its migration directory from cwd. Copy only trusted
        # migration source, keeping the benchmark cwd free of the user's .env.
        shutil.copytree(
            ROOT / "migrations", root / "migrations", ignore=shutil.ignore_patterns("__pycache__")
        )
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        try:
            if backend == "postgres":
                server, database_url, database_version = _postgres(root)
            else:
                database_url, database_version = "sqlite:///" + str(root / "app.sqlite"), "SQLite"
            # Child cwd has no .env; credentials, provider keys and proxy config
            # are not inherited. Only generated teaching data is initialized.
            env = {
                key: os.environ[key]
                for key in ("PATH", "LANG", "LC_ALL", "TMPDIR")
                if key in os.environ
            }
            env.update(
                PYTHONPATH=str(ROOT),
                APP_ENV="test",
                AGENT_MODE="demo",
                DATABASE_URL=database_url,
                DATA_DIR=str(root / "runtime"),
                SEED_DEMO="true",
                COOKIE_SECURE="false",
                SESSION_SECRET=secrets.token_hex(32),
                MAX_ACTIVE_TASKS=str(concurrency),
                WORKER_CONCURRENCY="1",
                MAX_RUNTIME_SECONDS=str(task_timeout),
            )

            async def spawn(*arguments, pass_fds=()):
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    *arguments,
                    cwd=root,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    pass_fds=pass_fds,
                )
                processes.append(process)
                return process

            initialize = await spawn("-m", "app.cli", "init")
            if await asyncio.wait_for(initialize.wait(), 40):
                raise RuntimeError("隔离数据库初始化失败")
            api = await spawn(
                "-m",
                "uvicorn",
                "app.api.server:app",
                "--fd",
                str(listener.fileno()),
                "--log-level",
                "error",
                pass_fds=(listener.fileno(),),
            )
            listener.close()
            worker_processes = [await spawn("-m", "app.tasks.worker") for _ in range(workers)]
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}",
                timeout=10,
                trust_env=False,
                limits=httpx.Limits(max_connections=max(8, concurrency * 2)),
            ) as client:
                ready = False
                deadline = time.monotonic() + 40
                while time.monotonic() < deadline:
                    if api.returncode is not None or any(
                        p.returncode is not None for p in worker_processes
                    ):
                        raise RuntimeError("隔离服务提前退出")
                    try:
                        ready = (await client.get("/health/ready")).status_code == 200
                    except httpx.TransportError:
                        pass
                    if ready:
                        break
                    await asyncio.sleep(0.1)
                if not ready:
                    raise RuntimeError("隔离服务启动超时")
                config = _response(await client.get("/api/config"))
                if config.get("mode") != "demo" or not config.get("demo_accounts"):
                    raise RuntimeError("拒绝在非教学环境施加载荷")
                login = _response(
                    await client.post(
                        "/api/auth/login",
                        json={"email": "demo@example.com", "password": "demo-commerce-2026"},
                    )
                )
                client.headers["X-CSRF-Token"] = login["csrf_token"]
                conversation = _response(await client.post("/api/conversations"), 201)["id"]
                dataset_id = bundle = None
                if profile in {"commerce_review", "commerce_diagnosis"}:
                    staged = _response(
                        await client.post("/api/commerce/scenarios", json={"scenario": "normal"})
                    )
                    confirmed = _response(
                        await client.post(f"/api/commerce/datasets/{staged['id']}/commit")
                    )
                    dataset_id = confirmed["id"]
                    if profile == "commerce_diagnosis":
                        bundle = _response(
                            await client.post(
                                "/api/analysis-inputs",
                                json={
                                    "sales_dataset_id": dataset_id,
                                    "period": {"start": "2026-09-01", "end": "2026-09-30"},
                                    "comparison_period": {
                                        "start": "2026-08-02",
                                        "end": "2026-08-31",
                                    },
                                },
                            ),
                            201,
                        )
                semaphore = asyncio.Semaphore(concurrency)
                outcomes = []
                started = time.monotonic()

                async def scenario(index):
                    async with semaphore:
                        begin, phase = time.monotonic(), "submit"
                        try:
                            payload = {
                                "conversation_id": conversation,
                                "query": "负载演练：核对九月库存与经营指标",
                                "sources": ["sales", "inventory"],
                                "public_queries": [],
                            }
                            if profile == "commerce_review":
                                payload.update(
                                    profile=profile,
                                    dataset_id=dataset_id,
                                    sources=["sales"],
                                    strategy="workflow",
                                )
                            elif profile == "commerce_diagnosis":
                                payload.update(
                                    profile=profile,
                                    bundle_id=bundle["bundle_id"],
                                    sources=["sales"],
                                    strategy="diagnostic",
                                    query="核对九月净销售额变化、退款和商品贡献",
                                )
                            headers = {"Idempotency-Key": str(uuid4())}
                            task = _response(
                                await client.post("/api/tasks", json=payload, headers=headers), 202
                            )
                            submit_ms = (time.monotonic() - begin) * 1000
                            phase = "idempotency"
                            repeated = _response(
                                await client.post("/api/tasks", json=payload, headers=headers), 202
                            )
                            if repeated["id"] != task["id"]:
                                raise RuntimeError("同一幂等键产生不同任务")
                            phase = "complete"
                            async with asyncio.timeout(task_timeout):
                                while True:
                                    detail = _response(await client.get(f"/api/tasks/{task['id']}"))
                                    if detail["status"] in TERMINAL:
                                        break
                                    await asyncio.sleep(0.1)
                            if (
                                detail["status"] not in {"degraded", "succeeded"}
                                or not detail["validation"]["passed"]
                            ):
                                raise RuntimeError("任务未通过结构校验")
                            if detail["metrics"]["model_calls"] != 0:
                                raise RuntimeError("教学演练意外产生模型请求")
                            if profile == "commerce_diagnosis":
                                projection = _response(
                                    await client.get(f"/api/tasks/{task['id']}/diagnosis")
                                )
                                if (
                                    len(projection["steps"]) > 8
                                    or detail["metrics"]["tool_calls"] > 10
                                    or detail["report"]["bundle_hash"] != bundle["bundle_hash"]
                                ):
                                    raise RuntimeError("诊断范围或全局预算不一致")
                            phase = "download"
                            artifact = next(
                                a
                                for a in detail["artifacts"]
                                if a["name"]
                                == (
                                    "report-v1.pdf"
                                    if profile == "commerce_diagnosis"
                                    else "report.pdf"
                                )
                            )
                            pdf = await client.get(f"/api/artifacts/{artifact['id']}")
                            if (
                                pdf.status_code != 200
                                or not pdf.content.startswith(b"%PDF")
                                or hashlib.sha256(pdf.content).hexdigest() != artifact["sha256"]
                            ):
                                raise RuntimeError("PDF内容或哈希校验失败")
                            outcomes.append(
                                {
                                    "ok": True,
                                    "submit_ms": submit_ms,
                                    "completion_ms": (time.monotonic() - begin) * 1000,
                                    "task_id": task["id"],
                                    "tool_calls": detail["metrics"]["tool_calls"],
                                }
                            )
                        except Exception as exc:
                            outcomes.append(
                                {
                                    "ok": False,
                                    "index": index,
                                    "phase": phase,
                                    "error_type": type(exc).__name__,
                                }
                            )

                await asyncio.gather(*(scenario(index) for index in range(requests)))
                elapsed = time.monotonic() - started
                success = [r for r in outcomes if r["ok"]]
                failures = [r for r in outcomes if not r["ok"]]
                # The HTTP history endpoint is intentionally limited to 50;
                # audit all rows in our newly generated private database instead.
                engine = create_engine(database_url)
                try:
                    with engine.connect() as db:
                        unique_tasks, model_calls = db.execute(
                            text("SELECT count(*), coalesce(sum(model_calls), 0) FROM tasks")
                        ).one()
                finally:
                    engine.dispose()
                passed = not failures and unique_tasks == requests and model_calls == 0
                return {
                    "run_id": str(uuid4()),
                    "timestamp": datetime.now(UTC).isoformat(),
                    "passed": passed,
                    "backend": backend,
                    "database_version": database_version,
                    "requests": requests,
                    "concurrency": concurrency,
                    "worker_processes": workers,
                    "worker_concurrency": 1,
                    "mode": "demo",
                    "profile": profile,
                    "model_calls": model_calls,
                    "completed": len(success),
                    "failures": failures,
                    "outcomes": outcomes,
                    "unique_tasks": unique_tasks,
                    "elapsed_seconds": round(elapsed, 3),
                    "completed_tasks_per_second": round(len(success) / elapsed, 3),
                    "submit_ms": {
                        "p50": percentile([r["submit_ms"] for r in success], 0.5),
                        "p95": percentile([r["submit_ms"] for r in success], 0.95),
                    },
                    "completion_ms": {
                        "p50": percentile([r["completion_ms"] for r in success], 0.5),
                        "p95": percentile([r["completion_ms"] for r in success], 0.95),
                    },
                    "percentile_method": "nearest-rank; successful scenarios only",
                    "measurement": "HTTP task submission + same-key replay + polling + verified PDF download; startup/login excluded",
                    "limits": "Closed-loop teaching workload on this host, not production capacity or model latency",
                    "platform": platform.system() + "/" + platform.machine(),
                    "benchmark_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                }
        finally:
            listener.close()
            for process in processes:
                if process.returncode is None:
                    process.terminate()
            for process in processes:
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            if server:
                server.cleanup()


def main():
    parser = argparse.ArgumentParser(description="仅启动隔离demo，不能指定外部URL/数据库")
    parser.add_argument("--backend", choices=["sqlite", "postgres"], default="sqlite")
    parser.add_argument(
        "--profile",
        choices=["research", "commerce_review", "commerce_diagnosis"],
        default="research",
    )
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("结果文件已存在；请使用新文件名保留历史结果")
    try:
        result = asyncio.run(
            run_benchmark(
                args.requests, args.concurrency, args.workers, args.backend, profile=args.profile
            )
        )
    except Exception as exc:
        result = {"passed": False, "phase": "startup-or-cleanup", "error_type": type(exc).__name__}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
