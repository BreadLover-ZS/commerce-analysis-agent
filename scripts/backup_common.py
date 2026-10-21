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


def diagnosis_audit(settings):
    """Legacy backups remain possible before the diagnosis migrations exist."""
    from sqlalchemy import create_engine, inspect

    engine = create_engine(settings.database_url)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    if (
        not {"analysis_input_bundles", "model_invocations", "tool_invocations", "input_requests"}
        <= tables
    ):
        return {
            "passed": True,
            "status": "legacy_schema_diagnosis_not_audited",
            "state_sha256": None,
        }
    from scripts.diagnosis_state_audit import audit_settings

    result = audit_settings(settings)
    if not result["passed"]:
        raise ValueError("诊断状态/账本/版本/产物核对失败；保持停服并运行diagnosis_state_audit查看")
    return result
