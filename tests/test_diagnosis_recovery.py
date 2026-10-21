"""Real SIGKILL windows on new graph, ledgers, typed interrupts and versioned artifacts."""

import asyncio
import json
import subprocess
import sys
import time

import pytest
from sqlalchemy import func, select

from app.diagnosis.schemas import ClarificationAnswer
from app.diagnosis.store import DiagnosisStore
from app.storage.models import DiagnosisStep, ModelInvocation, ToolInvocation
from app.tasks.worker import Worker, checkpoint_store
from tests.conftest import expire_lease
from tests.test_diagnosis_graph import new_diagnosis
from tests.test_diagnosis_model_calls import ScriptedDecisionModel


async def crash_at(settings, phase):
    config = settings.data_dir / "fault-config.json"
    marker = settings.data_dir / "fault-reached"
    config.write_text(
        json.dumps(
            {
                "root": str(settings.data_dir),
                "database_url": settings.database_url,
                "phase": phase,
                "lease_seconds": settings.lease_seconds,
            }
        )
    )
    config.chmod(0o600)
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.diagnosis_fault_worker", str(config)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if marker.is_file():
                break
            assert process.poll() is None, f"child exited before {phase}"
            await asyncio.sleep(0.03)
        else:
            raise AssertionError(f"worker did not reach {phase}")
        process.kill()
        await asyncio.to_thread(process.wait, 5)
        assert process.returncode < 0
    finally:
        if process.poll() is None:
            process.kill()
            await asyncio.to_thread(process.wait, 5)
        config.unlink(missing_ok=True)
        marker.unlink(missing_ok=True)


@pytest.mark.parametrize("phase", ["tool_committed", "artifact_committed"])
async def test_sigkill_committed_tool_or_artifacts_reuses_identity(setup, phase, monkeypatch):
    settings, repo, _, _ = setup
    # SIGKILL + expire_lease below forces takeover independently of wall-clock
    # speed. Give the resumed healthy worker a realistic publication lease.
    settings.lease_seconds = 30
    task, _ = new_diagnosis(setup)
    await crash_at(settings, phase)
    before = repo.artifacts(task["id"])
    expire_lease(repo, task["id"])
    if phase == "artifact_committed":

        def forbidden_pdf(*args):
            raise AssertionError("must reuse already published PDF")

        monkeypatch.setattr("app.diagnosis.graph.convert_md_to_pdf", forbidden_pdf)
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    done = repo.get_task(task["id"])
    assert done["status"] == "degraded", done
    assert done["metrics"]["tool_calls"] == 4
    assert len(repo.evidence(task["id"])) == 4
    with repo.session() as db:
        assert db.scalar(select(func.count()).select_from(ToolInvocation)) == 4
        assert db.scalar(select(func.count()).select_from(DiagnosisStep)) == 3
    if before:
        assert repo.artifacts(task["id"]) == before


@pytest.mark.parametrize(
    "phase", ["model_before_send", "model_before_commit", "model_output_committed"]
)
async def test_sigkill_model_windows_preserve_cost_and_reuse_success(setup, phase):
    settings, repo, _, _ = setup
    task, _ = new_diagnosis(setup, "商品变化贡献")
    await crash_at(settings, phase)
    before = repo.get_task(task["id"])
    assert before["metrics"]["model_calls"] == 1
    expire_lease(repo, task["id"])
    model = ScriptedDecisionModel(
        {"action": "stop", "question": "贡献核查完成", "stop_reason": "target_covered"}
    )
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver, model=model).run_once()
    done = repo.get_task(task["id"])
    assert done["status"] == "degraded", done
    assert done["validation"]["passed"]
    if phase == "model_output_committed":
        assert done["metrics"]["model_calls"] == 2
        assert len(model.requests) == 1
        assert done["metrics"]["input_tokens"] == 20
        assert done["metrics"]["usage_complete"]
    else:
        assert done["metrics"]["model_calls"] == 1 and not done["metrics"]["usage_complete"]
        assert done["report"]["stop_reason"] == "usage_unknown"
        assert model.requests == []
        with repo.session() as db:
            assert db.scalar(select(ModelInvocation.status)) == "UNKNOWN"


@pytest.mark.parametrize("phase", ["input_before_checkpoint", "input_after_checkpoint"])
async def test_sigkill_wait_record_and_checkpoint_reconcile_without_losing_input(setup, phase):
    settings, repo, user, _ = setup
    task, _ = new_diagnosis(setup, "那个商品的净销售额变化")
    await crash_at(settings, phase)
    expire_lease(repo, task["id"])
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    waiting = repo.get_task(task["id"])
    assert waiting["status"] == "waiting_input", waiting
    assert waiting["metrics"]["tool_calls"] == 2
    pending = DiagnosisStore(repo).projection(task["id"], user)["pending_input"]
    DiagnosisStore(repo).answer(
        task["id"],
        user,
        ClarificationAnswer(
            interrupt_id=pending["interrupt_id"],
            expected_version=pending["expected_version"],
            choice="A",
        ),
    )
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    done = repo.get_task(task["id"])
    assert done["status"] == "degraded", done
    assert done["report"]["scope"]["skus"] == ["A"]
