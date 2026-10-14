"""Subprocess fault-injection entry point, never imported by the application."""

import asyncio
import sys
import time
from pathlib import Path

import app.research.graph as graph_module
from app.core.config import Settings
from app.storage.repository import Repository
from app.tasks.worker import Worker, checkpoint_store


async def main():
    root = Path(sys.argv[1])
    settings = Settings(
        _env_file=None,
        app_env="test",
        data_dir=root,
        database_url="sqlite:///" + str(root / "app.sqlite"),
        lease_seconds=5,
    )
    repo = Repository(settings)
    original = graph_module.convert_md_to_pdf

    def slow_pdf(*args):
        time.sleep(30)
        return original(*args)

    graph_module.convert_md_to_pdf = slow_pdf
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()


if __name__ == "__main__":
    asyncio.run(main())
