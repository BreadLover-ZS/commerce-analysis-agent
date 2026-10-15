"""Small filesystem primitives shared by offline backup implementations."""

import hashlib
import tempfile
from pathlib import Path


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stage_directory(target):
    if target.exists() or target.is_symlink():
        raise ValueError("目标必须是不存在的新目录，禁止覆盖")
    target.parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=".commerce-backup-", dir=target.parent))
