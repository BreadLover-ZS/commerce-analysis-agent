"""Offline SQLite backup; run with python -m scripts.backup_local, from repo root."""

import argparse
import hashlib
import json
import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import Settings
from app.core.files import safe_path


def _sqlite_copy(source, target):
    with sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True) as db:
        with sqlite3.connect(target) as destination:
            db.backup(destination)


def _hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _stage(target):
    if target.exists():
        raise ValueError("目标必须是不存在的新目录，禁止覆盖")
    target.parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=".commerce-backup-", dir=target.parent))


def create_backup(settings, target, services_stopped=False):
    if not services_stopped:
        raise ValueError("必须先停止API与Worker；备份不提供跨数据库/文件的在线一致性")
    if not settings.database_url.startswith("sqlite:///"):
        raise ValueError("该工具仅支持本地SQLite；PostgreSQL使用独立备份流程")
    root, target = settings.data_dir.resolve(), target.resolve()
    database = Path(settings.database_url.removeprefix("sqlite:///")).resolve()
    if target.is_relative_to(root) or root.is_relative_to(target):
        raise ValueError("备份目标与运行目录不能互相包含")
    if not database.is_file():
        raise ValueError("业务数据库不存在")
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
        if db.execute(
            "SELECT count(*) FROM tasks WHERE status IN ('running','cancelling')"
        ).fetchone()[0]:
            raise ValueError("存在运行中的任务；等待完成或取消后再备份")
    stage = _stage(target)
    try:
        (stage / "runtime").mkdir()
        _sqlite_copy(database, stage / "database.sqlite")
        for source in root.rglob("*"):
            if source.is_symlink():
                raise ValueError("运行目录包含符号链接，拒绝跨目录复制")
            if (
                not source.is_file()
                or source.resolve() == database
                or source.name.endswith(("-wal", "-shm"))
            ):
                continue
            destination = stage / "runtime" / source.relative_to(root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source == root / "checkpoints.sqlite":
                _sqlite_copy(source, destination)
            else:
                shutil.copyfile(source, destination)
        files = [
            {"path": p.relative_to(stage).as_posix(), "size": p.stat().st_size, "sha256": _hash(p)}
            for p in sorted(stage.rglob("*"))
            if p.is_file()
        ]
        manifest = {
            "format": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "files": files,
            "sensitive": True,
            "precondition": "operator-stopped-api-and-worker",
        }
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        for p in stage.rglob("*"):
            p.chmod(0o700 if p.is_dir() else 0o600)
        stage.rename(target)
        return manifest
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def restore_backup(source, target):
    source, target = source.resolve(), target.resolve()
    if target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("恢复目录不能与备份目录互相包含")
    manifest = json.loads((source / "manifest.json").read_text())
    if any(p.is_symlink() for p in source.rglob("*")):
        raise ValueError("备份目录包含符号链接")
    if manifest["format"] != 1:
        raise ValueError("不支持的备份格式")
    paths = [item["path"] for item in manifest["files"]]
    if any(path != "database.sqlite" and not path.startswith("runtime/") for path in paths):
        raise ValueError("备份清单包含非运行数据")
    if len(set(paths)) != len(paths) or "database.sqlite" not in paths:
        raise ValueError("备份清单重复或缺少数据库")
    for item in manifest["files"]:
        path = safe_path(source, item["path"])
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != item["size"]
            or _hash(path) != item["sha256"]
        ):
            raise ValueError("备份文件校验失败")
    stage = _stage(target)
    try:
        for item in manifest["files"]:
            destination = safe_path(stage, item["path"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(safe_path(source, item["path"]), destination)
            destination.chmod(0o600)
        with sqlite3.connect(stage / "database.sqlite") as db:
            if (
                db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or db.execute("PRAGMA foreign_key_check").fetchall()
            ):
                raise ValueError("恢复数据库结构校验失败")
        stage.rename(target)
        return {"database": str(target / "database.sqlite"), "data_dir": str(target / "runtime")}
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description="停止服务后备份；在新目录恢复；不修改原始数据")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("target", type=Path)
    create.add_argument("--services-stopped", action="store_true", required=True)
    restore = sub.add_parser("restore")
    restore.add_argument("source", type=Path)
    restore.add_argument("target", type=Path)
    args = parser.parse_args()
    result = (
        create_backup(Settings(), args.target, args.services_stopped)
        if args.command == "create"
        else restore_backup(args.source, args.target)
    )
    print(
        json.dumps(
            result
            if args.command == "restore"
            else {"files": len(result["files"]), "sensitive": True},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
