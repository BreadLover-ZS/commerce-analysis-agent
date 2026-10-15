"""Subprocess fault-injection entry point, never imported by the application."""

import asyncio
import json
import sys
import time
from pathlib import Path

import app.research.graph as graph_module
from app.core.config import Settings
from app.storage.repository import Repository
from app.tasks.worker import Worker, checkpoint_store


async def main():
    root = Path(sys.argv[1])
    phase = sys.argv[2] if len(sys.argv) > 2 else "export-pdf"
    settings = Settings(
        _env_file=None,
        app_env="test",
        agent_mode="demo",
        data_dir=root,
        database_url="sqlite:///" + str(root / "app.sqlite"),
        lease_seconds=5,
    )
    repo = Repository(settings)
    original = graph_module.convert_md_to_pdf

    def pause(key=None):
        marker = root / ("fault-" + phase + ".json")
        if not marker.exists():
            marker.write_text(json.dumps({"phase": phase, "storage_key": key}))
            time.sleep(30)

    if phase == "evidence-committed":
        original_evidence = repo.add_evidence

        def add_evidence(*args, **kwargs):
            result = original_evidence(*args, **kwargs)
            pause()
            return result

        repo.add_evidence = add_evidence
    elif phase == "report-committed":
        original_report = repo.save_report

        def save_report(*args, **kwargs):
            result = original_report(*args, **kwargs)
            pause()
            return result

        repo.save_report = save_report
    elif phase == "artifact-unregistered":
        original_artifact = repo.add_artifact

        def add_artifact(ctx, name, storage_key, content):
            if name == "report.md":
                pause(storage_key)
            return original_artifact(ctx, name, storage_key, content)

        repo.add_artifact = add_artifact

    def slow_pdf(*args):
        time.sleep(30)
        return original(*args)

    if phase == "export-pdf":
        graph_module.convert_md_to_pdf = slow_pdf
    async with checkpoint_store(settings) as saver:
        await Worker(repo, settings, saver).run_once()


if __name__ == "__main__":
    asyncio.run(main())
