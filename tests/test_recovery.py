import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, inspect, select

from app.storage.models import Artifact
from app.tasks.worker import Worker, checkpoint_store
from tests.conftest import expire_lease, new_task
from tests.fake_model import ScriptedCommerceModel


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


@pytest.mark.parametrize(
    "phase", ["evidence-committed", "report-committed", "artifact-unregistered"]
)
async def test_sigkill_commit_windows_resume_with_dedup_and_valid_artifacts(setup, phase):
    settings, repo, *_ = setup
    task = new_task(setup, sources=["sales", "inventory", "documents"])
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.fault_worker", str(settings.data_dir), phase],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    marker = settings.data_dir / ("fault-" + phase + ".json")
    try:
        deadline = time.monotonic() + 15
        while not marker.exists() and time.monotonic() < deadline:
            assert process.poll() is None, "fault worker exited before selected window"
            await asyncio.sleep(0.05)
        assert marker.exists(), "fault worker did not reach selected window"
        before = repo.get_task(task["id"])
        fault = json.loads(marker.read_text())
        if phase == "artifact-unregistered":
            assert (settings.data_dir / "artifacts" / fault["storage_key"]).is_file()
            assert not any(a["name"] == "report.md" for a in repo.artifacts(task["id"]))
        process.kill()
        await asyncio.to_thread(process.wait, 5)
        expire_lease(repo, task["id"])
        async with checkpoint_store(settings) as saver:
            await Worker(repo, settings, saver).run_once()
        done = repo.get_task(task["id"])
        assert done["status"] == "degraded" and done["attempts"] == 2
        evidence = repo.evidence(task["id"])
        assert len({(e["kind"], e["locator"], e["content_hash"]) for e in evidence}) == len(
            evidence
        )
        if phase == "evidence-committed":
            # An incomplete collect node replays its read calls; only storage is deduplicated.
            assert done["metrics"]["tool_calls"] > before["metrics"]["tool_calls"]
            assert sum(e["kind"] == "collecting" for e in repo.events(task["id"])) == 2
        else:
            assert done["metrics"]["tool_calls"] == before["metrics"]["tool_calls"]
            assert sum(e["kind"] == "collecting" for e in repo.events(task["id"])) == 1
        with repo.session() as db:
            artifacts = list(db.scalars(select(Artifact).where(Artifact.task_id == task["id"])))
        assert len(artifacts) == 5
        for artifact in artifacts:
            content = (settings.data_dir / "artifacts" / artifact.storage_key).read_bytes()
            assert hashlib.sha256(content).hexdigest() == artifact.sha256
            assert len(content) == artifact.size
        if phase == "artifact-unregistered":
            assert all(a.storage_key != fault["storage_key"] for a in artifacts)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()


async def test_recovery_after_unreported_model_request_blocks_new_generation(setup):
    settings, repo, *_ = setup
    settings.agent_mode, settings.llm_model = "live", "fake-commerce"
    settings.openai_api_key = SecretStr("offline-recovery-test")
    task = new_task(setup, sources=["sales"])
    old = repo.claim()
    # Persisted admission with no usage receipt models a crash during a request.
    repo.consume(old, "model")
    expire_lease(repo, task["id"])
    current = repo.claim()
    model = ScriptedCommerceModel()
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver, model).process(current)
    done = repo.get_task(task["id"])
    assert done["status"] == "degraded"
    assert done["attempts"] == 2 and done["metrics"]["model_calls"] == 1
    assert not done["metrics"]["usage_complete"]
    assert model._tool_sets == []
    assert done["validation"]["draft_fallback_used"]


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
