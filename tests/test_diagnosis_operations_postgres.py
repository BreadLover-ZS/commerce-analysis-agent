"""New profiles through real pg_dump/pg_restore and the same recovery audit."""

from sqlalchemy import select

from app.storage.models import User
from app.storage.repository import Repository
from scripts import backup_postgres as backup
from scripts.diagnosis_state_audit import audit
from tests.test_diagnosis_operations import check_restored, prepare_recovery_cases
from tests.test_postgres import postgres_setup as postgres_setup
from tests.test_postgres_backup import _pg_bin
from tests.test_postgres_backup import pg_backup_setup as pg_backup_setup


async def test_postgres_diagnosis_consistent_backup_and_typed_wait_recovery(
    pg_backup_setup, tmp_path
):
    source, target, repo = pg_backup_setup
    with repo.session() as db:
        user = db.scalar(select(User).where(User.email == "demo@example.com"))
        other = db.scalar(select(User).where(User.email == "other@example.com"))
    ids = await prepare_recovery_cases((source, repo, user, other))
    initial = audit(repo, require_stopped=True)
    assert initial["passed"], initial
    before = {name: repo.get_task(task_id) for name, task_id in ids.items()}
    directory = tmp_path / "diagnosis-private-backup"
    manifest = backup.create_backup(source, directory, True, _pg_bin())
    assert manifest["diagnosis_recovery"]["state_sha256"] == initial["state_sha256"]
    backup.restore_backup(target, directory, target.data_dir, True, _pg_bin())
    restored = Repository(target)
    try:
        assert audit(restored, require_stopped=True)["state_sha256"] == initial["state_sha256"]
        await check_restored(restored, target, user, ids, before)
        assert repo.get_task(ids["clarifying"])["status"] == "waiting_input"
    finally:
        restored.engine.dispose()


async def test_postgres_maintenance_schema_upgrade_keeps_v1_wait_and_accepts_v2(postgres_setup):
    from alembic import command
    from alembic.config import Config

    from tests.test_diagnosis_extensions import extended_task
    from tests.test_diagnosis_graph import new_diagnosis
    from tests.test_diagnosis_operations import approve, run

    settings, repo, user = postgres_setup
    with repo.session() as db:
        other = db.scalar(select(User).where(User.email == "other@example.com"))
    setup = (settings, repo, user, other)
    task, _ = new_diagnosis(setup, require_review=True)
    await run(repo, settings)
    before = repo.get_task(task["id"])
    assert before["status"] == "waiting_review" and before["graph_version"] == "diagnosis-v1"
    config = Config("alembic.ini")
    config.attributes["settings"] = settings
    command.downgrade(config, "d31019000003")
    command.upgrade(config, "head")
    assert repo.get_task(task["id"])["parameters"] == before["parameters"]
    assert audit(repo, require_stopped=True)["passed"]
    approve(repo, user, task["id"])
    await run(repo, settings)
    assert repo.get_task(task["id"])["metrics"]["tool_calls"] == before["metrics"]["tool_calls"]
    extended, _, _ = extended_task(setup)
    await run(repo, settings)
    assert repo.get_task(extended["id"])["graph_version"] == "diagnosis-v2"
    assert repo.get_task(extended["id"])["status"] == "degraded"
