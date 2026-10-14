"""Compatibility helper with a strict session boundary."""

from pathlib import Path

from app.core.files import safe_path


def resolve_path(filename: str, session_dir: str | None = None) -> str:
    if not session_dir:
        raise ValueError("文件操作必须指定会话目录")
    return str(safe_path(Path(session_dir), filename))
