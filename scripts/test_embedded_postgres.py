"""Optional local verification; pgserver is not a production dependency."""

import inspect
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pgserver
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def main():
    with TemporaryDirectory(prefix="commerce-postgres-") as directory:
        server = pgserver.get_server(Path(directory) / "pgdata", cleanup_mode="stop")
        try:
            url = (
                make_url(server.get_uri())
                .set(drivername="postgresql+psycopg")
                .render_as_string(hide_password=False)
            )
            engine = create_engine(url)
            with engine.connect() as db:
                print(
                    "Isolated PostgreSQL version:",
                    db.scalar(text("SHOW server_version")),
                    flush=True,
                )
            engine.dispose()
            env = {
                **os.environ,
                "TEST_POSTGRES_URL": url,
                "TEST_PG_BIN": str(Path(inspect.getfile(pgserver)).parent / "pginstall" / "bin"),
            }
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    *(sys.argv[1:] or ["tests/test_postgres.py"]),
                ],
                env=env,
            )
            if result.returncode:
                raise SystemExit(result.returncode)
        finally:
            server.cleanup()


if __name__ == "__main__":
    main()
