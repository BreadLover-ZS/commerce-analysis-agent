"""Child-process fault fixture. Always uses demo data / scripted model, never credentials."""

import asyncio
import json
import sys
import time
from pathlib import Path

from app.core.config import Settings
from app.diagnosis.model_calls import DiagnosisModelGateway
from app.diagnosis.store import DiagnosisStore
from app.diagnosis.tools import DiagnosisToolGateway
from app.storage.repository import Repository
from app.tasks.worker import Worker, checkpoint_store
from tests.test_diagnosis_model_calls import ScriptedDecisionModel


def main():
    config = json.loads(Path(sys.argv[1]).read_text())
    root, phase = Path(config["root"]), config["phase"]
    settings = Settings(
        _env_file=None,
        app_env="test",
        agent_mode="demo",
        data_dir=root,
        database_url=config["database_url"],
        lease_seconds=config["lease_seconds"],
    )
    repo = Repository(settings)

    def stop():
        (root / "fault-reached").write_text(phase)
        while True:
            time.sleep(1)

    def wrap(owner, name, *, before=False, predicate=None):
        original = getattr(owner, name)

        def hooked(*args, **kwargs):
            selected = predicate is None or predicate(args, kwargs)
            if before and selected:
                stop()
            value = original(*args, **kwargs)
            if not before and selected:
                stop()
            return value

        setattr(owner, name, hooked)

    model = None
    if phase == "completion_rejection_committed":
        model = ScriptedDecisionModel(
            {"action": "stop", "question": "核查已覆盖", "stop_reason": "target_covered"}
        )
        wrap(
            DiagnosisStore,
            "update_step",
            predicate=lambda args, kwargs: (
                kwargs.get("assessment", {}).get("error_code") == "incomplete_evidence"
            ),
        )
    elif phase.startswith("model_"):
        model = ScriptedDecisionModel(
            {
                "action": "tool",
                "question": "核对商品贡献",
                "hypothesis_code": "sales_concentration",
                "tool_name": "rank_contributors",
                "arguments": {},
            }
        )
        if phase == "model_before_send":
            wrap(DiagnosisModelGateway, "_prepare")
        elif phase == "model_before_commit":
            wrap(DiagnosisModelGateway, "_complete", before=True)
        else:
            wrap(DiagnosisModelGateway, "_complete")
    elif phase == "tool_committed":
        wrap(DiagnosisToolGateway, "_publish")
    elif phase == "extension_tool_committed":
        wrap(
            DiagnosisToolGateway,
            "_publish",
            predicate=lambda args, _: args[2]["tool"] == "inventory_risk",
        )
    elif phase == "input_before_checkpoint":
        wrap(DiagnosisStore, "ensure_input")
    elif phase == "input_after_checkpoint":
        wrap(repo, "finish", before=True, predicate=lambda args, _: args[1] == "waiting_input")
    elif phase == "artifact_committed":
        wrap(DiagnosisStore, "publish_artifacts")
    else:
        raise ValueError("unknown fault phase")

    async def run():
        async with checkpoint_store(settings) as saver:
            await Worker(repo, settings, saver, model=model).run_once()

    asyncio.run(run())


if __name__ == "__main__":
    main()
