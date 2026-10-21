"""Offline recovery of new typed waits, versions, usage ledgers and published files."""

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.diagnosis.schemas import ClarificationAnswer
from app.diagnosis.store import DiagnosisStore
from app.storage.models import Task
from app.storage.repository import Repository
from app.tasks.worker import Worker, checkpoint_store
from scripts.backup_local import create_backup, restore_backup
from scripts.cleanup_artifacts import cleanup
from scripts.diagnosis_state_audit import audit
from tests.test_diagnosis_extensions import extended_task
from tests.test_diagnosis_graph import new_diagnosis
from tests.test_diagnosis_model_calls import ScriptedDecisionModel


async def run(repo, settings, model=None):
    async with checkpoint_store(settings) as saver:
        assert await Worker(repo, settings, saver, model=model).run_once()


def approve(repo, user, task_id):
    pending = DiagnosisStore(repo).projection(task_id, user)["pending_input"]
    DiagnosisStore(repo).review(
        task_id,
        user,
        SimpleNamespace(
            approved=True,
            interrupt_id=pending["interrupt_id"],
            expected_version=pending["expected_version"],
            report_hash=repo.get_task(task_id)["report_hash"],
        ),
    )


async def prepare_recovery_cases(setup):
    settings, repo, user, _ = setup
    settings.lease_seconds = 30
    finished, _, _ = extended_task(setup)
    await run(repo, settings)
    clarifying, _, _ = extended_task(setup, "那个商品的库存与规则需要核对", review=True)
    await run(repo, settings)
    reviewing, _ = new_diagnosis(setup, require_review=True)
    await run(repo, settings)
    cancelled, _ = new_diagnosis(setup)
    repo.cancel(cancelled["id"], user.id)
    unknown, _ = new_diagnosis(setup)
    await run(repo, settings, ScriptedDecisionModel(TimeoutError("local unknown usage injection")))
    ids = {
        "finished": finished["id"],
        "clarifying": clarifying["id"],
        "reviewing": reviewing["id"],
        "cancelled": cancelled["id"],
        "unknown": unknown["id"],
    }
    assert repo.get_task(ids["clarifying"])["status"] == "waiting_input"
    assert repo.get_task(ids["reviewing"])["status"] == "waiting_review"
    assert not repo.get_task(ids["unknown"])["metrics"]["usage_complete"]
    return ids


async def check_restored(repo, settings, user, ids, before):
    state = audit(repo, require_stopped=True)
    assert state["passed"], state
    assert state["typed_waits_with_checkpoint"] == 2 and state["unknown_usage_retained"] == 1
    store = DiagnosisStore(repo)
    pending = store.projection(ids["clarifying"], user)["pending_input"]
    store.answer(
        ids["clarifying"],
        user,
        ClarificationAnswer(
            interrupt_id=pending["interrupt_id"],
            expected_version=pending["expected_version"],
            choice="A",
        ),
    )
    await run(repo, settings)
    assert repo.get_task(ids["clarifying"])["status"] == "waiting_review"
    assert repo.get_task(ids["clarifying"])["report"]["scope"]["skus"] == ["A"]
    approve(repo, user, ids["clarifying"])
    await run(repo, settings)
    approve(repo, user, ids["reviewing"])
    await run(repo, settings)
    assert (
        repo.get_task(ids["reviewing"])["metrics"]["tool_calls"]
        == before["reviewing"]["metrics"]["tool_calls"]
    )
    for name in ("finished", "clarifying", "reviewing"):
        assert repo.get_task(ids[name])["status"] == "degraded"
        assert len(repo.artifacts(ids[name])) == 5
    assert repo.get_task(ids["finished"])["report_hash"] == before["finished"]["report_hash"]
    assert repo.get_task(ids["unknown"])["metrics"] == before["unknown"]["metrics"]
    assert repo.get_task(ids["cancelled"])["status"] == "cancelled"
    assert audit(repo, require_stopped=True)["passed"]


async def test_new_graph_backup_restores_typed_waits_usage_and_versions(setup, tmp_path):
    settings, repo, user, _ = setup
    ids = await prepare_recovery_cases(setup)
    before = {name: repo.get_task(task_id) for name, task_id in ids.items()}
    initial = audit(repo, require_stopped=True)
    assert initial["passed"], initial
    backup = tmp_path.with_name(tmp_path.name + "-coordinated-backup")
    manifest = create_backup(settings, backup, services_stopped=True)
    assert (
        manifest["backup_id"]
        and manifest["diagnosis_recovery"]["state_sha256"] == initial["state_sha256"]
    )
    restored = restore_backup(backup, tmp_path.with_name(tmp_path.name + "-restored"))
    restored_settings = settings.model_copy(
        update={
            "database_url": "sqlite:///" + restored["database"],
            "data_dir": Path(restored["data_dir"]),
        }
    )
    target = Repository(restored_settings)
    try:
        assert audit(target, require_stopped=True)["state_sha256"] == initial["state_sha256"]
        await check_restored(target, restored_settings, user, ids, before)
        assert repo.get_task(ids["clarifying"])["status"] == "waiting_input"
    finally:
        target.engine.dispose()


@pytest.mark.parametrize("fault", ["graph", "usage", "checkpoint", "artifact", "source"])
async def test_recovery_audit_and_backup_fail_closed_on_inconsistent_state(setup, tmp_path, fault):
    settings, repo, _, _ = setup
    task, _ = new_diagnosis(setup, "那个商品的销售额变化" if fault == "checkpoint" else "商品贡献")
    await run(repo, settings)
    if fault == "checkpoint":
        (settings.data_dir / "checkpoints.sqlite").unlink()
    elif fault == "artifact":
        from app.storage.models import Artifact

        with repo.session() as db:
            row = db.query(Artifact).filter(Artifact.task_id == task["id"]).first()
            path = settings.data_dir / "artifacts" / row.storage_key
        path.write_bytes(b"tampered")
    elif fault == "source":
        from app.storage.models import AnalysisInputBundle, CommerceDataset

        with repo.session() as db:
            bundle = db.get(AnalysisInputBundle, task["bundle_id"])
            db.get(CommerceDataset, bundle.sales_dataset_id).content_hash = "0" * 64
            db.commit()
    else:
        with repo.session() as db:
            row = db.get(Task, task["id"])
            if fault == "graph":
                row.graph_version = "unsupported-graph"
            else:
                row.input_tokens += 1
            db.commit()
    state = audit(repo, require_stopped=True)
    assert not state["passed"] and state["issues"]
    target = tmp_path.with_name(tmp_path.name + "-invalid-backup")
    with pytest.raises(ValueError, match="核对失败"):
        create_backup(settings, target, services_stopped=True)
    assert not target.exists()


def old(path):
    stamp = time.time() - 48 * 3600
    os.utime(path, (stamp, stamp))


async def test_orphan_cleanup_preserves_all_registered_versions_and_recent_files(setup):
    settings, repo, _, _ = setup
    task, _ = new_diagnosis(setup)
    await run(repo, settings)
    registered = list((settings.data_dir / "artifacts").rglob("*"))
    for path in registered:
        if path.is_file():
            old(path)
    orphan = settings.data_dir / "artifacts" / task["id"] / "abandoned-lease.pdf"
    orphan.write_bytes(b"not registered, never downloadable")
    old(orphan)
    recent = orphan.with_name("recent-unpublished.pdf")
    recent.write_bytes(b"retain grace period")
    plan = cleanup(repo)
    assert plan["deleted"] == 0 and len(plan["candidates"]) == 1 and orphan.exists()
    with pytest.raises(ValueError, match="停止"):
        cleanup(repo, apply=True)
    result = cleanup(repo, apply=True, services_stopped=True)
    assert result["deleted"] == 1 and not orphan.exists() and recent.exists()
    assert len(repo.artifacts(task["id"])) == 5
    assert audit(repo, require_stopped=True)["passed"]


def test_cleanup_rejects_active_tasks_short_grace_and_symlinks(setup, tmp_path):
    settings, repo, _, _ = setup
    task, _ = new_diagnosis(setup)
    root = settings.data_dir / "artifacts" / task["id"]
    root.mkdir(parents=True)
    orphan = root / "old.pdf"
    orphan.write_bytes(b"old")
    old(orphan)
    repo.claim()
    assert cleanup(repo)["candidates"] == []
    with pytest.raises(ValueError, match="运行"):
        cleanup(repo, apply=True, services_stopped=True)
    with pytest.raises(ValueError, match="24小时"):
        cleanup(repo, older_than_hours=1)
    external = tmp_path / "external-secret"
    external.write_bytes(b"preserved")
    (root / "link").symlink_to(external)
    with pytest.raises(ValueError, match="符号链接"):
        cleanup(repo)
    assert external.read_bytes() == b"preserved" and orphan.exists()
