import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

from sqlalchemy import create_engine, inspect

from app.tasks.worker import Worker, checkpoint_store
from tests.conftest import expire_lease, new_task


async def test_sigkill_during_export_resumes_without_research_replay(setup):
    settings, repo, _, _ = setup
    task = new_task(setup)
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.fault_worker", str(settings.data_dir)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if any(e["kind"] == "exporting" for e in repo.events(task["id"])):
                break
            assert process.poll() is None, "fault worker exited before export"
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("worker did not reach export")
        before = repo.get_task(task["id"])
        process.kill()
        await asyncio.to_thread(process.wait, 5)
        expire_lease(repo, task["id"])
        async with checkpoint_store(settings) as saver:
            await Worker(repo, settings, saver).run_once()
        done = repo.get_task(task["id"])
        assert done["status"] == "degraded" and done["attempts"] == 2
        assert done["metrics"]["tool_calls"] == before["metrics"]["tool_calls"]
        assert len(repo.artifacts(task["id"])) == 5
        assert sum(e["kind"] == "planning" for e in repo.events(task["id"])) == 1
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()


def test_migrations_upgrade_downgrade_and_reinitialize(tmp_path):
    path = tmp_path / "migrations.sqlite"
    env = {
        **os.environ,
        "DATABASE_URL": "sqlite:///" + str(path),
        "DATA_DIR": str(tmp_path),
        "APP_ENV": "test",
        "AGENT_MODE": "demo",
    }
    command = [str(Path(sys.executable).parent / "alembic")]
    for target in ("head", "base", "head"):
        action = "downgrade" if target == "base" else "upgrade"
        subprocess.run(command + [action, target], env=env, check=True, capture_output=True)
        engine = create_engine(env["DATABASE_URL"])
        tables = inspect(engine).get_table_names()
        assert ("tasks" in tables) == (target == "head")
        engine.dispose()
