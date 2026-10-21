"""Operator-only offline PostgreSQL + runtime backup, never exposed as an Agent tool."""

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.core.files import safe_path
from scripts.backup_common import diagnosis_audit, file_hash, stage_directory

RESTORE_LOCK = 742309165081
QUERY_ENV = {
    "host": "PGHOST",
    "port": "PGPORT",
    "sslmode": "PGSSLMODE",
    "sslrootcert": "PGSSLROOTCERT",
    "sslcert": "PGSSLCERT",
    "sslkey": "PGSSLKEY",
    "options": "PGOPTIONS",
    "connect_timeout": "PGCONNECT_TIMEOUT",
    "application_name": "PGAPPNAME",
    "target_session_attrs": "PGTARGETSESSIONATTRS",
    "channel_binding": "PGCHANNELBINDING",
}


class BackupError(ValueError):
    """Only messages constructed here, never provider/SQL output, reach the CLI."""


def _url(settings):
    url = make_url(settings.database_url)
    if url.get_backend_name() != "postgresql":
        raise BackupError("该工具仅支持PostgreSQL；SQLite请使用backup_local")
    if set(url.query) - QUERY_ENV.keys() or any(isinstance(v, tuple) for v in url.query.values()):
        raise BackupError("连接参数不受支持，拒绝静默丢弃参数")
    return url


def _connect(settings):
    _url(settings)
    return psycopg.connect(
        settings.checkpoint_url, autocommit=True, row_factory=dict_row, connect_timeout=5
    )


@contextmanager
def _client_environment(settings):
    url = _url(settings)
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    env.update(PGCONNECT_TIMEOUT="5", PGAPPNAME="commerce-offline-backup")
    for name, value in (
        ("PGHOST", url.host),
        ("PGPORT", url.port),
        ("PGUSER", url.username),
        ("PGDATABASE", url.database),
    ):
        if value is not None:
            env[name] = str(value)
    env.update({QUERY_ENV[key]: value for key, value in url.query.items()})
    # Passwords are not command arguments, manifest fields or inherited PGPASSWORD.
    with tempfile.TemporaryDirectory(prefix="commerce-pgpass-") as directory:
        password = url.password or ""
        user = url.username or "*"
        if any(char in password + user for char in "\r\n"):
            raise BackupError("数据库凭据不支持换行符")

        def escape(value):
            return value.replace("\\", "\\\\").replace(":", "\\:")

        path = Path(directory) / "pgpass"
        path.write_text(f"*:*:*:{escape(user)}:{escape(password)}\n", encoding="utf-8")
        path.chmod(0o600)
        env["PGPASSFILE"] = str(path)
        yield env


def _run_client(settings, name, args, pg_bin=None, timeout=300):
    program = str(Path(pg_bin) / name) if pg_bin else shutil.which(name)
    if not program or not Path(program).is_file():
        raise BackupError(f"缺少{name}；配置PostgreSQL16客户端或--pg-bin")
    try:
        with _client_environment(settings) as env:
            result = subprocess.run(
                [program, "--no-password", *args],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired):
        raise BackupError(f"{name}未完成，未发布恢复目录；请检查客户端及连接配置") from None
    if result.returncode:
        # pg_restore errors can contain row contents; never echo raw stderr.
        raise BackupError(f"{name}失败（exit={result.returncode}），未发布目标目录")


def _private_tree(root):
    root.chmod(0o700)
    for path in root.rglob("*"):
        path.chmod(0o700 if path.is_dir() else 0o600)


def _check_artifacts(db, runtime):
    for item in db.execute("SELECT storage_key, size, sha256 FROM artifacts").fetchall():
        path = safe_path(runtime, item["storage_key"])
        if (
            not path.is_file()
            or path.stat().st_size != item["size"]
            or file_hash(path) != item["sha256"]
        ):
            raise BackupError("登记产物缺失或哈希不一致，拒绝发布")


def _check_stopped(db, services_stopped):
    if not services_stopped:
        raise BackupError("必须先停止API与Worker，并提供--services-stopped")
    if db.execute(
        "SELECT count(*) AS n FROM tasks WHERE status IN ('running','cancelling')"
    ).fetchone()["n"]:
        raise BackupError("存在运行中的任务；等待完成或取消后再备份")


def create_backup(settings, target, services_stopped=False, pg_bin=None):
    if not services_stopped:
        raise BackupError("必须先停止API与Worker，并提供--services-stopped")
    if target.is_symlink():
        raise BackupError("备份目标不能是符号链接")
    root, target = settings.data_dir.resolve(), target.resolve()
    if not root.is_dir() or target.is_relative_to(root) or root.is_relative_to(target):
        raise BackupError("备份目标与运行目录不能互相包含，且运行目录必须存在")
    if any(path.is_symlink() for path in root.rglob("*")):
        raise BackupError("运行目录包含符号链接，拒绝跨目录复制")
    with _connect(settings) as db:
        _check_stopped(db, services_stopped)
        _check_artifacts(db, root / "artifacts")
        version = db.execute("SHOW server_version_num").fetchone()["server_version_num"]
    recovery_state = diagnosis_audit(settings)
    stage = stage_directory(target)
    try:
        _run_client(
            settings,
            "pg_dump",
            [
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                "--file",
                str(stage / "database.dump"),
            ],
            pg_bin,
        )
        runtime = stage / "runtime"
        runtime.mkdir(mode=0o700)
        for path in root.rglob("*"):
            if path.is_symlink():
                raise BackupError("运行目录包含符号链接")
            if not path.is_file() or path.name == ".env" or path.name.startswith(".env."):
                continue
            destination = runtime / path.relative_to(root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
        with _connect(settings) as db:
            _check_stopped(db, services_stopped)
            _check_artifacts(db, runtime / "artifacts")
        if diagnosis_audit(settings).get("state_sha256") != recovery_state.get("state_sha256"):
            raise BackupError("诊断状态在备份窗口内变化；保持停服并重新备份")
        manifest = {
            "format": 2,
            "backup_id": str(uuid4()),
            "diagnosis_recovery": recovery_state,
            "backend": "postgresql",
            "created_at": datetime.now(UTC).isoformat(),
            "server_major": int(version) // 10000,
            "sensitive": True,
            "precondition": "operator-stopped-api-and-worker",
            "files": [
                {
                    "path": p.relative_to(stage).as_posix(),
                    "size": p.stat().st_size,
                    "sha256": file_hash(p),
                }
                for p in sorted(stage.rglob("*"))
                if p.is_file()
            ],
        }
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        _private_tree(stage)
        if target.exists():
            raise BackupError("备份目标已存在，禁止覆盖")
        stage.rename(target)
        return manifest
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def _validate_bundle(source):
    if any(path.is_symlink() for path in source.rglob("*")):
        raise BackupError("备份包含符号链接")
    manifest = json.loads((source / "manifest.json").read_text())
    if manifest.get("format") != 2 or manifest.get("backend") != "postgresql":
        raise BackupError("不支持的PostgreSQL备份格式")
    seen = set()
    for item in manifest["files"]:
        name = item["path"]
        path = PurePosixPath(name)
        if (
            not name
            or name != path.as_posix()
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in name
            or name in seen
            or not (name == "database.dump" or name.startswith("runtime/"))
        ):
            raise BackupError("备份清单包含重复或越界路径")
        seen.add(name)
        file = safe_path(source, name)
        if (
            type(item["size"]) is not int
            or item["size"] < 0
            or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
            or not file.is_file()
            or file.stat().st_size != item["size"]
            or file_hash(file) != item["sha256"]
        ):
            raise BackupError("备份文件校验失败")
    if "database.dump" not in seen:
        raise BackupError("备份缺少数据库")
    return manifest


def _require_empty(db):
    if db.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname <> 'public' "
        "AND nspname <> 'information_schema' AND nspname !~ '^pg_') OR "
        "EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON c.relnamespace=n.oid "
        "WHERE n.nspname='public') OR "
        "EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON p.pronamespace=n.oid "
        "WHERE n.nspname='public') OR "
        "EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n ON t.typnamespace=n.oid "
        "WHERE n.nspname='public') AS occupied"
    ).fetchone()["occupied"]:
        raise BackupError("恢复目标数据库非空，禁止覆盖；请配置新的空数据库")


def restore_backup(settings, source, target, services_stopped=False, pg_bin=None):
    if not services_stopped:
        raise BackupError("恢复前必须停止目标服务，并提供--services-stopped")
    if source.is_symlink() or target.is_symlink():
        raise BackupError("备份或恢复目录不能是符号链接")
    source, target = source.resolve(), target.resolve()
    if source.is_relative_to(target) or target.is_relative_to(source):
        raise BackupError("恢复目录不能与备份目录互相包含")
    manifest = _validate_bundle(source)
    stage = stage_directory(target)
    database_restored = False
    restore_started = False
    try:
        for item in manifest["files"]:
            if item["path"].startswith("runtime/"):
                relative = item["path"].removeprefix("runtime/")
                destination = safe_path(stage, relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(safe_path(source, item["path"]), destination)
        _private_tree(stage)
        with _connect(settings) as db:
            locked = db.execute(
                "SELECT pg_try_advisory_lock(%s) AS locked", (RESTORE_LOCK,)
            ).fetchone()
            if not locked["locked"]:
                raise BackupError("已有恢复任务占用目标数据库")
            _require_empty(db)
            # No --clean / --create: never drop an existing database or its data.
            restore_started = True
            _run_client(
                settings,
                "pg_restore",
                [
                    "--single-transaction",
                    "--exit-on-error",
                    "--no-owner",
                    "--no-privileges",
                    "--dbname",
                    make_conninfo(dbname=db.info.dbname),
                    str(source / "database.dump"),
                ],
                pg_bin,
            )
            database_restored = True
            _check_artifacts(db, stage / "artifacts")
            if manifest.get("diagnosis_recovery"):
                recovered = diagnosis_audit(settings.model_copy(update={"data_dir": stage}))
                if recovered.get("state_sha256") != manifest["diagnosis_recovery"].get(
                    "state_sha256"
                ):
                    raise BackupError("恢复诊断状态与同批备份不一致")
            if target.exists():
                raise BackupError("恢复目录已存在，禁止覆盖")
            stage.rename(target)
        return {"data_dir": str(target), "database_restored": True}
    except Exception:
        if restore_started and stage.exists():
            # The DB commit and file publication are not one transaction. Keep
            # the already verified runtime for recovery, never silently delete it.
            state = "数据库已恢复" if database_restored else "恢复命令已发出，数据库结果需核对"
            raise BackupError(f"{state}，运行目录未发布；保持停服，保留目录：{stage}") from None
        raise
    finally:
        if stage.exists() and not restore_started:
            shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description="可信PostgreSQL备份；停服后恢复到空库和新目录")
    parser.add_argument("--settings-file", type=Path, default=Path(".env"))
    parser.add_argument("--pg-bin", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("target", type=Path)
    create.add_argument("--services-stopped", action="store_true", required=True)
    restore = sub.add_parser("restore")
    restore.add_argument("source", type=Path)
    restore.add_argument("target", type=Path)
    restore.add_argument("--services-stopped", action="store_true", required=True)
    args = parser.parse_args()
    try:
        settings = Settings(_env_file=args.settings_file)
        result = (
            {
                "files": len(create_backup(settings, args.target, True, args.pg_bin)["files"]),
                "sensitive": True,
            }
            if args.command == "create"
            else restore_backup(settings, args.source, args.target, True, args.pg_bin)
        )
        print(json.dumps(result, ensure_ascii=False))
    except BackupError as exc:
        raise SystemExit(str(exc)) from None
    except Exception as exc:
        raise SystemExit(
            f"备份/恢复未完成（{type(exc).__name__}）；请检查私有配置和目标环境"
        ) from None


if __name__ == "__main__":
    main()
