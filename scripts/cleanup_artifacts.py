"""Operator-only orphan artifact cleanup. Never part of the Agent tool registry."""

import argparse
import json
import time

from sqlalchemy import select

from app.core.config import Settings
from app.core.files import safe_path
from app.storage.models import Artifact, Task
from app.storage.repository import Repository
from scripts.backup_common import file_hash


def cleanup(repo, *, apply=False, services_stopped=False, older_than_hours=24):
    if type(older_than_hours) is not int or not 24 <= older_than_hours <= 87600:
        raise ValueError("孤立产物保留期必须为24小时至10年")
    if apply and not services_stopped:
        raise ValueError("实际删除前须停止API与全部Worker，并提供--services-stopped")
    root = repo.settings.data_dir / "artifacts"
    if repo.settings.data_dir.is_symlink() or root.is_symlink():
        raise ValueError("运行或产物目录不能是符号链接")
    if not root.exists():
        return {
            "mode": "apply" if apply else "dry_run",
            "candidates": [],
            "deleted": 0,
            "protected": 0,
        }
    files = list(root.rglob("*"))
    if any(path.is_symlink() for path in files):
        raise ValueError("产物目录包含符号链接，未删除任何文件")
    cutoff = time.time() - older_than_hours * 3600
    candidates, protected = [], 0
    # Holding task row locks during apply prevents an existing stopped task from
    # concurrently resuming while its old unpublished files are being removed.
    with repo.session() as db:
        tasks = list(db.scalars(select(Task).with_for_update() if apply else select(Task)))
        active = {t.id for t in tasks if t.status in {"running", "cancelling"}}
        if apply and active:
            raise ValueError("存在运行或取消中的任务，拒绝删除")
        registered = {a.storage_key for a in db.scalars(select(Artifact))}
        for path in sorted(files):
            if not path.is_file():
                continue
            key = path.relative_to(root).as_posix()
            if key in registered or key.split("/", 1)[0] in active or path.stat().st_mtime > cutoff:
                protected += 1
                continue
            stat = path.stat()
            candidates.append(
                {
                    "storage_key": key,
                    "size": stat.st_size,
                    "sha256": file_hash(path),
                    "mtime_ns": stat.st_mtime_ns,
                    "inode": stat.st_ino,
                }
            )
        deleted = 0
        for item in candidates if apply else []:
            path = safe_path(root, item["storage_key"])
            current = path.stat()
            if (
                path.is_symlink()
                or (current.st_size, current.st_mtime_ns, current.st_ino)
                != (item["size"], item["mtime_ns"], item["inode"])
                or file_hash(path) != item["sha256"]
            ):
                raise ValueError("清理候选在删除前变化；停止并保留其余文件")
            path.unlink()
            deleted += 1
        # Empty folders are harmless; keep them so no unrelated directory is removed.
    return {
        "mode": "apply" if apply else "dry_run",
        "retention_hours": older_than_hours,
        "candidates": candidates,
        "deleted": deleted,
        "protected": protected,
        "scope": "Unregistered files only; registered versions and all database evidence retained.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--services-stopped", action="store_true")
    parser.add_argument("--older-than-hours", type=int, default=24)
    args = parser.parse_args()
    repo = Repository(Settings())
    try:
        result = cleanup(
            repo,
            apply=args.apply,
            services_stopped=args.services_stopped,
            older_than_hours=args.older_than_hours,
        )
        print(json.dumps(result, ensure_ascii=False))
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    finally:
        repo.engine.dispose()


if __name__ == "__main__":
    main()
