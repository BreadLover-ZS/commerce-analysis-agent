import asyncio
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, inspect, select

from app.research.graph import build_workflow
from app.storage.models import Artifact
from app.tasks.checkpoints import LeaseFencedSaver
from app.tasks.worker import Worker, checkpoint_store
from tests.conftest import expire_lease, new_task
from tests.fake_model import ScriptedCommerceModel


async def test_worker_waits_for_collect_checkpoint_before_drafting(setup, monkeypatch):
    settings, repo, *_ = setup
    task = new_task(setup, sources=["sales", "inventory", "documents"])
    checkpoint_started, release_checkpoint = asyncio.Event(), asyncio.Event()
    draft_started = threading.Event()
    original_put, original_emit = LeaseFencedSaver.aput, repo.emit

    async def delayed_put(self, config, checkpoint, metadata, new_versions):
        values = checkpoint["channel_values"]
        if "evidence_context" in values and "report" not in values:
            # Hold the completed collect checkpoint before lease admission, as
            # a delayed background writer can be scheduled after later nodes.
            checkpoint_started.set()
            await release_checkpoint.wait()
        return await original_put(self, config, checkpoint, metadata, new_versions)

    def record_draft(ctx, kind, *args, **kwargs):
        if kind == "drafting":
            draft_started.set()
        return original_emit(ctx, kind, *args, **kwargs)

    monkeypatch.setattr(LeaseFencedSaver, "aput", delayed_put)
    monkeypatch.setattr(repo, "emit", record_draft)
    async with checkpoint_store(settings) as saver:
        execution = asyncio.create_task(Worker(repo, settings, saver).run_once())
        try:
            await asyncio.wait_for(checkpoint_started.wait(), timeout=5)
            await asyncio.sleep(0.2)
            advanced_while_checkpoint_blocked = draft_started.is_set()
        finally:
            release_checkpoint.set()
            await asyncio.wait_for(execution, timeout=10)
    assert not advanced_while_checkpoint_blocked, "draft ran before collect checkpoint was durable"
    done = repo.get_task(task["id"])
    assert done["status"] == "degraded" and done["metrics"]["tool_calls"] == 3


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
            checkpoint = await saver.aget_tuple(
                {"configurable": {"thread_id": task["id"], "checkpoint_ns": ""}}
            )
        done = repo.get_task(task["id"])
        diagnosis = {
            "status": done["status"],
            "error_code": done["error_code"],
            "attempts": done["attempts"],
            "checkpoint_step": checkpoint.metadata.get("step") if checkpoint else None,
            "checkpoint_keys": sorted(checkpoint.checkpoint["channel_values"])
            if checkpoint
            else [],
            "checkpoint_status": checkpoint.checkpoint["channel_values"].get("status")
            if checkpoint
            else None,
            "pending_channels": [write[1] for write in checkpoint.pending_writes]
            if checkpoint
            else [],
            "events": [event["kind"] for event in repo.events(task["id"])],
        }
        assert done["status"] == "degraded" and done["attempts"] == 2, diagnosis
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


@pytest.mark.parametrize("boundary", ["review", "export", "completed"])
async def test_empty_next_with_pending_writes_must_be_finalized_not_failed(setup, boundary):
    settings, repo, *_ = setup
    task = new_task(setup, sources=["sales", "inventory", "documents"])
    old = repo.claim()
    config = {"configurable": {"thread_id": task["id"], "checkpoint_ns": ""}}
    async with checkpoint_store(settings) as saver:
        async with LeaseFencedSaver(saver, repo, old) as guarded:
            graph = build_workflow(repo, settings, old, guarded)
            result = await graph.ainvoke({}, config)
            assert result["status"] == "degraded"
            if boundary != "completed":
                # Reproduce the durable crash window: node writes committed but
                # the checkpoint applying them did not. Business side effects can
                # already be committed, just as before the failed CI SIGKILL.
                checkpoints = [checkpoint async for checkpoint in saver.alist(config)]
                target = next(
                    checkpoint
                    for checkpoint in checkpoints
                    if f"branch:to:{boundary}" in checkpoint.checkpoint["channel_values"]
                )
                assert target.pending_writes
                async with saver.lock:
                    await saver.conn.execute(
                        "DELETE FROM checkpoints WHERE thread_id=? AND checkpoint_ns='' AND checkpoint_id>?",
                        (task["id"], target.checkpoint["id"]),
                    )
                    await saver.conn.commit()
            snapshot = await graph.aget_state(config)
            assert not snapshot.next
            if boundary == "review":
                assert snapshot.values.get("status") is None
        before = repo.get_task(task["id"])
        exports = sum(event["kind"] == "exporting" for event in repo.events(task["id"]))
        expire_lease(repo, task["id"])
        await Worker(repo, settings, saver).run_once()
        done = repo.get_task(task["id"])
        assert done["status"] == "degraded" and done["attempts"] == 2
        assert done["metrics"]["tool_calls"] == before["metrics"]["tool_calls"]
        assert len(repo.artifacts(task["id"])) == 5
        assert sum(e["kind"] == "planning" for e in repo.events(task["id"])) == 1
        assert sum(e["kind"] == "exporting" for e in repo.events(task["id"])) == exports + (
            boundary == "review"
        )


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
