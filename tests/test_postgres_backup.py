import json
import os
import secrets
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import select
from sqlalchemy.engine import URL, make_url

from app.api.server import create_app
from app.commerce.service import seed_commerce
from app.core.auth import bootstrap_user
from app.core.config import Settings
from app.research.schemas import TaskRequest
from app.storage.models import Artifact, User
from app.storage.repository import Repository
from app.tasks.worker import Worker, checkpoint_store
from scripts import backup_postgres as backup
from scripts.backup_common import file_hash
from tests.conftest import authenticate


@pytest.fixture
def pg_backup_setup(tmp_path):
    if not os.getenv("TEST_POSTGRES_URL"):
        pytest.skip("未提供隔离PostgreSQL")
    base = make_url(os.environ["TEST_POSTGRES_URL"]).set(drivername="postgresql")
    role, password = "backup_" + uuid4().hex, secrets.token_hex(16)
    databases = ["backup_" + uuid4().hex for _ in range(2)]
    with psycopg.connect(base.render_as_string(hide_password=False), autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
        for database in databases:
            admin.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(database), sql.Identifier(role)
                )
            )
    settings = [
        Settings(
            _env_file=None,
            app_env="test",
            database_url=base.set(
                drivername="postgresql+psycopg", username=role, password=password, database=database
            ).render_as_string(hide_password=False),
            data_dir=tmp_path / f"runtime-{index}",
        )
        for index, database in enumerate(databases)
    ]
    config = Config("alembic.ini")
    config.attributes["settings"] = settings[0]
    command.upgrade(config, "head")
    repo = Repository(settings[0])
    bootstrap_user(repo, settings[0])
    seed_commerce(repo)
    try:
        yield settings[0], settings[1], repo
    finally:
        repo.engine.dispose()
        with psycopg.connect(base.render_as_string(hide_password=False), autocommit=True) as admin:
            for database in databases:
                admin.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
                )
            admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def _pg_bin():
    configured = os.getenv("TEST_PG_BIN")
    return Path(configured) if configured else None


async def test_postgres_backup_restores_review_checkpoint_files_and_isolation(
    pg_backup_setup, tmp_path
):
    source, target, repo = pg_backup_setup
    with repo.session() as db:
        user = db.scalar(select(User).where(User.email == "demo@example.com"))
        other = db.scalar(select(User).where(User.email == "other@example.com"))

    def task(review):
        return repo.create_task(
            user,
            TaskRequest(
                conversation_id=repo.create_conversation(user.id),
                query="备份恢复演练",
                sources=["sales", "inventory"],
                require_review=review,
            ),
            str(uuid4()),
        )

    finished = task(False)
    async with checkpoint_store(source) as saver:
        await Worker(repo, source, saver).run_once()
        waiting = task(True)
        await Worker(repo, source, saver).run_once()
    before = repo.get_task(waiting["id"])
    assert before["status"] == "waiting_review"
    (source.data_dir / ".env").write_text("private-configuration-not-backed-up")
    directory = tmp_path / "private-backup"
    with pytest.raises(backup.BackupError, match="停止"):
        backup.create_backup(source, directory, pg_bin=_pg_bin())
    manifest = backup.create_backup(source, directory, True, _pg_bin())
    assert manifest["format"] == 2 and manifest["server_major"] == 16
    assert "runtime/.env" not in {item["path"] for item in manifest["files"]}
    assert source.database_url not in json.dumps(manifest)
    assert directory.stat().st_mode & 0o777 == 0o700
    assert (directory / "database.dump").stat().st_mode & 0o777 == 0o600
    with pytest.raises(backup.BackupError, match="非空"):
        backup.restore_backup(source, directory, tmp_path / "overwrite", True, _pg_bin())
    assert not (tmp_path / "overwrite").exists()

    with backup._connect(target) as db:
        db.execute("CREATE SCHEMA pgcustomer")
    with pytest.raises(backup.BackupError, match="非空"):
        backup.restore_backup(target, directory, target.data_dir, True, _pg_bin())
    with backup._connect(target) as db:
        db.execute("DROP SCHEMA pgcustomer")
    result = backup.restore_backup(target, directory, target.data_dir, True, _pg_bin())
    assert result["database_restored"]
    restored = Repository(target)
    try:
        with TestClient(create_app(target, restored)) as client:
            authenticate(client)
            artifact = next(
                a for a in restored.artifacts(finished["id"]) if a["name"] == "report.pdf"
            )
            response = client.get(f"/api/artifacts/{artifact['id']}")
            assert response.status_code == 200 and response.content.startswith(b"%PDF")
            authenticate(client, other.email, "other-commerce-2026")
            assert client.get(f"/api/tasks/{finished['id']}").status_code == 404
            assert client.get(f"/api/artifacts/{artifact['id']}").status_code == 404
        restored.review(waiting["id"], user.id, True)
        async with checkpoint_store(target) as saver:
            await Worker(restored, target, saver).run_once()
        after = restored.get_task(waiting["id"])
        assert after["status"] == "degraded" and after["metrics"]["model_calls"] == 0
        assert after["metrics"]["tool_calls"] == before["metrics"]["tool_calls"]
        assert len(restored.artifacts(waiting["id"])) == 5
        assert repo.get_task(waiting["id"])["status"] == "waiting_review"
        with restored.session() as db:
            artifacts = list(db.scalars(select(Artifact).where(Artifact.task_id == waiting["id"])))
        for artifact in artifacts:
            assert (
                file_hash(target.data_dir / "artifacts" / artifact.storage_key) == artifact.sha256
            )
    finally:
        restored.engine.dispose()
    # A real pg_restore completed; the same target cannot be restored over again.
    with pytest.raises(backup.BackupError, match="非空"):
        backup.restore_backup(target, directory, tmp_path / "second-runtime", True, _pg_bin())


def test_postgres_backup_refuses_running_tasks(pg_backup_setup, tmp_path):
    source, _, repo = pg_backup_setup
    with repo.session() as db:
        user = db.scalar(select(User).where(User.email == "demo@example.com"))
    repo.create_task(
        user,
        TaskRequest(conversation_id=repo.create_conversation(user.id), query="运行中不可备份"),
        str(uuid4()),
    )
    assert repo.claim()
    with pytest.raises(backup.BackupError, match="运行中的任务"):
        backup.create_backup(source, tmp_path / "backup", True, _pg_bin())
    assert not (tmp_path / "backup").exists()


def test_failed_restore_preserves_staged_files_and_does_not_publish(pg_backup_setup, tmp_path):
    _, target, _ = pg_backup_setup
    directory, _ = _bundle(tmp_path)
    with pytest.raises(backup.BackupError, match="数据库结果需核对"):
        backup.restore_backup(target, directory, target.data_dir, True, _pg_bin())
    assert not target.data_dir.exists()
    assert list(target.data_dir.parent.glob(".commerce-backup-*"))
    with backup._connect(target) as db:
        backup._require_empty(db)


def test_publish_failure_after_database_commit_keeps_runtime(
    pg_backup_setup, tmp_path, monkeypatch
):
    source, target, _ = pg_backup_setup
    (source.data_dir / "kept.txt").write_text("runtime retained after DB restore")
    directory = tmp_path / "backup"
    backup.create_backup(source, directory, True, _pg_bin())
    original = Path.rename

    def fail_publish(path, destination):
        if Path(destination) == target.data_dir:
            raise OSError("simulated publication failure")
        return original(path, destination)

    monkeypatch.setattr(Path, "rename", fail_publish)
    with pytest.raises(backup.BackupError, match="数据库已恢复"):
        backup.restore_backup(target, directory, target.data_dir, True, _pg_bin())
    assert not target.data_dir.exists()
    stages = list(target.data_dir.parent.glob(".commerce-backup-*"))
    assert len(stages) == 1 and (stages[0] / "kept.txt").read_text().startswith("runtime retained")
    with backup._connect(target) as db:
        assert db.execute("SELECT count(*) AS n FROM users").fetchone()["n"] == 2


def _bundle(tmp_path):
    directory = tmp_path / "bundle"
    directory.mkdir()
    dump = directory / "database.dump"
    dump.write_bytes(b"not-a-real-dump")
    manifest = {
        "format": 2,
        "backend": "postgresql",
        "files": [
            {"path": "database.dump", "size": dump.stat().st_size, "sha256": file_hash(dump)}
        ],
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return directory, manifest


@pytest.mark.parametrize(
    "mutation", ["traversal", "duplicate", "corrupt", "symlink", "missing", "size"]
)
def test_invalid_backup_is_rejected_before_any_database_write(tmp_path, monkeypatch, mutation):
    directory, manifest = _bundle(tmp_path)
    if mutation == "traversal":
        manifest["files"].append({**manifest["files"][0], "path": "runtime/../../outside"})
    elif mutation == "duplicate":
        manifest["files"] *= 2
    elif mutation == "corrupt":
        (directory / "database.dump").write_bytes(b"changed")
    elif mutation == "symlink":
        (directory / "extra").symlink_to(tmp_path)
    elif mutation == "missing":
        manifest["files"] = []
    else:
        manifest["files"][0]["size"] = True
    (directory / "manifest.json").write_text(json.dumps(manifest))

    def no_connection(*args):
        raise AssertionError("invalid backup reached the database")

    monkeypatch.setattr(backup, "_connect", no_connection)
    with pytest.raises(backup.BackupError):
        backup.restore_backup(Settings(_env_file=None), directory, tmp_path / "new-runtime", True)
    assert not (tmp_path / "new-runtime").exists()


def test_client_password_file_is_private_ephemeral_and_errors_do_not_leak(tmp_path, monkeypatch):
    password = "private:database\\password"
    settings = Settings(
        _env_file=None,
        database_url=URL.create(
            "postgresql+psycopg",
            username="operator",
            password=password,
            host="localhost",
            database="test",
        ).render_as_string(hide_password=False),
    )
    binary = tmp_path / "pg_dump"
    binary.touch()
    seen = []

    def fail(args, *, env, **kwargs):
        assert password not in str(args) and "PGPASSWORD" not in env
        path = Path(env["PGPASSFILE"])
        assert path.stat().st_mode & 0o777 == 0o600
        assert "private\\:database\\\\password" in path.read_text()
        seen.append(path)
        return subprocess.CompletedProcess(args, 2, stderr=password.encode())

    monkeypatch.setenv("PGPASSWORD", "inherited-private-secret")
    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(backup.BackupError, match="exit=2") as caught:
        backup._run_client(settings, "pg_dump", [], tmp_path)
    assert password not in str(caught.value)
    assert seen and not seen[0].exists()
