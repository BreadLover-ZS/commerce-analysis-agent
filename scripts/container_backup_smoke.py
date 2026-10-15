"""CI-only round trip against a disposable Compose database and temporary files."""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core.config import Settings
from scripts.backup_postgres import _connect, _url, create_backup, restore_backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-isolated-compose", action="store_true", required=True)
    parser.parse_args()
    settings = Settings()
    url = _url(settings)
    if settings.agent_mode != "demo" or url.host != "postgres" or url.database != "commerce":
        raise SystemExit("仅允许隔离的默认Compose教学环境")
    target = settings.model_copy(
        update={"database_url": url.set(database="commerce_restore").render_as_string(False)}
    )
    with TemporaryDirectory(prefix="container-restore-") as directory:
        root = Path(directory)
        backup, restored = root / "backup", root / "restored"
        create_backup(settings, backup, services_stopped=True)
        restore_backup(target, backup, restored, services_stopped=True)
        assert (restored / "artifacts").is_dir()
        with _connect(settings) as source, _connect(target) as destination:
            for table in ("users", "tasks", "artifacts", "checkpoints"):
                # Table names are fixed in source, never operator/model input.
                query = f"SELECT count(*) AS n FROM {table}"
                expected = source.execute(query).fetchone()["n"]
                actual = destination.execute(query).fetchone()["n"]
                assert actual == expected and actual > 0, table
    print("PASS: container PostgreSQL dump/restore, checkpoint and artifact integrity")


if __name__ == "__main__":
    main()
