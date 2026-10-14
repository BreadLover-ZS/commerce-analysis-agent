"""Start API + worker together; ephemeral mode is used by browser tests."""

import argparse
import os
import signal
import subprocess
import sys
import time
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--ephemeral", action="store_true")
    args = parser.parse_args()
    env = dict(os.environ)
    directory = (
        TemporaryDirectory(prefix="commerce-browser-") if args.ephemeral else nullcontext(None)
    )
    with directory as root:
        if root:
            env.update(
                DATABASE_URL="sqlite:///" + str(Path(root) / "app.sqlite"),
                DATA_DIR=root,
                APP_ENV="test",
                AGENT_MODE="demo",
                SEED_DEMO="true",
                COOKIE_SECURE="false",
                BOOTSTRAP_EMAIL="demo@example.com",
                BOOTSTRAP_PASSWORD="demo-commerce-2026",
                ALLOWED_ORIGINS='["http://127.0.0.1:5191","http://localhost:5191"]',
            )
        subprocess.run([sys.executable, "-m", "app.cli", "init"], env=env, check=True)
        children = [
            subprocess.Popen([sys.executable, "-m", "app.tasks.worker"], env=env),
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.api.server:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.port),
                ],
                env=env,
            ),
        ]

        def stop(*_):
            for child in children:
                if child.poll() is None:
                    child.terminate()

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        try:
            while all(child.poll() is None for child in children):
                time.sleep(0.2)
        finally:
            stop()
            for child in children:
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


if __name__ == "__main__":
    main()
