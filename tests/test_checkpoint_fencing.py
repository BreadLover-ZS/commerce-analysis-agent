from uuid import uuid4

import pytest
from langgraph.checkpoint.base import empty_checkpoint

from app.storage.repository import StaleLease
from app.tasks.checkpoints import LeaseFencedSaver
from app.tasks.worker import checkpoint_store
from tests.conftest import expire_lease, new_task


async def test_old_worker_cannot_change_checkpoint_or_pending_writes_after_takeover(setup):
    settings, repo, *_ = setup
    task = new_task(setup)
    old = repo.claim()
    config = {"configurable": {"thread_id": task["id"], "checkpoint_ns": ""}}
    async with checkpoint_store(settings) as raw:
        async with LeaseFencedSaver(raw, repo, old) as old_saver:
            initial = empty_checkpoint()
            initial["channel_values"] = {"owner": "initial"}
            saved = await old_saver.aput(config, initial, {"source": "update", "step": 0}, {})
            expire_lease(repo, task["id"])
            current = repo.claim()
            async with LeaseFencedSaver(raw, repo, current) as current_saver:
                checkpoint = empty_checkpoint()
                checkpoint["channel_values"] = {"owner": "current"}
                latest = await current_saver.aput(
                    saved, checkpoint, {"source": "update", "step": 1}, {}
                )
                with pytest.raises(StaleLease):
                    await old_saver.aput(
                        config, empty_checkpoint(), {"source": "update", "step": 2}, {}
                    )
                with pytest.raises(StaleLease):
                    await old_saver.aput_writes(latest, [("owner", "late")], "late-node")
                result = await current_saver.aget_tuple(config)
                assert result.checkpoint["channel_values"]["owner"] == "current"
                assert result.pending_writes == []
                with pytest.raises(ValueError, match="线程不属于"):
                    await current_saver.aget_tuple({"configurable": {"thread_id": str(uuid4())}})
    repo.finish(current, "cancelled", 0)
