from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.server import create_app
from app.storage.repository import Repository
from app.tasks.worker import Worker, checkpoint_store
from scripts.backup_local import create_backup, restore_backup
from tests.conftest import authenticate, new_task


async def test_offline_backup_restores_database_checkpoints_and_downloads(setup, tmp_path):
    settings, repo, user, _ = setup
    settings = settings.model_copy(update={"data_dir": tmp_path / "runtime"})
    settings.data_dir.mkdir()
    task = new_task(setup)
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    backup = tmp_path / "private-backup"
    with pytest.raises(ValueError, match="停止"):
        create_backup(settings, backup)
    manifest = create_backup(settings, backup, services_stopped=True)
    assert "runtime/checkpoints.sqlite" in {f["path"] for f in manifest["files"]}
    restored = restore_backup(backup, tmp_path / "restored")
    restored_settings = settings.model_copy(
        update={
            "database_url": "sqlite:///" + restored["database"],
            "data_dir": Path(restored["data_dir"]),
        }
    )
    restored_repo = Repository(restored_settings)
    try:
        with TestClient(create_app(restored_settings, restored_repo)) as client:
            authenticate(client)
            assert restored_repo.get_task(task["id"], user.id)["report"]
            pdf = next(a for a in restored_repo.artifacts(task["id"]) if a["name"] == "report.pdf")
            response = client.get(f"/api/artifacts/{pdf['id']}")
            assert response.status_code == 200 and response.content.startswith(b"%PDF")
        # Corruption is refused before writing a partial restore.
        bad_file = backup / "runtime" / "checkpoints.sqlite"
        bad_file.write_bytes(bad_file.read_bytes() + b"corrupt")
        with pytest.raises(ValueError, match="校验失败"):
            restore_backup(backup, tmp_path / "invalid")
        assert not (tmp_path / "invalid").exists()
    finally:
        restored_repo.engine.dispose()


def test_backup_refuses_running_tasks_and_path_overlap(setup, tmp_path):
    settings, repo, *_ = setup
    new_task(setup)
    repo.claim()
    settings = settings.model_copy(update={"data_dir": tmp_path / "runtime"})
    settings.data_dir.mkdir()
    with pytest.raises(ValueError, match="运行中的任务"):
        create_backup(settings, tmp_path / "backup", services_stopped=True)
    with pytest.raises(ValueError, match="互相包含"):
        create_backup(settings, settings.data_dir / "backup", services_stopped=True)


async def test_restored_review_checkpoint_resumes_without_research_repeat(setup, tmp_path):
    settings, repo, user, _ = setup
    settings = settings.model_copy(update={"data_dir": tmp_path / "runtime"})
    settings.data_dir.mkdir()
    task = new_task(setup, require_review=True)
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()
    before = repo.get_task(task["id"])
    assert before["status"] == "waiting_review"
    backup = tmp_path / "backup"
    create_backup(settings, backup, services_stopped=True)
    restored = restore_backup(backup, tmp_path / "restored")
    settings = settings.model_copy(
        update={
            "database_url": "sqlite:///" + restored["database"],
            "data_dir": Path(restored["data_dir"]),
        }
    )
    restored_repo = Repository(settings)
    try:
        restored_repo.review(task["id"], user.id, True)
        async with checkpoint_store(settings) as saver:
            await Worker(restored_repo, settings, saver).run_once()
        after = restored_repo.get_task(task["id"])
        assert after["status"] in {"succeeded", "degraded"}
        assert after["metrics"]["tool_calls"] == before["metrics"]["tool_calls"]
        assert any(a["name"] == "research.json" for a in restored_repo.artifacts(task["id"]))
    finally:
        restored_repo.engine.dispose()
