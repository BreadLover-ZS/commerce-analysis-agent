import asyncio
import logging
import signal
import time
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from app.core.config import get_settings
from app.research.graph import build_workflow
from app.storage.repository import BudgetExceeded, Repository, StaleLease, TaskCancelled
from app.tasks.checkpoints import LeaseFencedSaver

logger = logging.getLogger(__name__)


@asynccontextmanager
async def checkpoint_store(settings):
    if settings.database_url.startswith("postgresql"):
        async with AsyncPostgresSaver.from_conn_string(settings.checkpoint_url) as saver:
            await saver.setup()
            yield saver
    else:
        async with AsyncSqliteSaver.from_conn_string(
            str(settings.data_dir / "checkpoints.sqlite")
        ) as saver:
            await saver.setup()
            yield saver


class Worker:
    def __init__(self, repo, settings, saver, model=None):
        self.repo, self.settings, self.saver, self.model = repo, settings, saver, model
        self.stopping = asyncio.Event()

    async def process(self, ctx):
        started = time.monotonic()
        try:
            task = await asyncio.to_thread(self.repo.get_task, ctx.task_id)
            remaining = await asyncio.to_thread(self.repo.remaining_runtime, ctx)
            await asyncio.to_thread(self.repo.account_unreported_usage, ctx)
        except StaleLease:
            return
        execution = None

        async def heartbeats():
            while True:
                await asyncio.sleep(self.settings.lease_seconds / 3)
                try:
                    status = await asyncio.to_thread(self.repo.heartbeat, ctx)
                    if status == "cancelling" and execution:
                        execution.cancel()
                except (StaleLease, TaskCancelled):
                    if execution:
                        execution.cancel()
                    return

        async def execute():
            async with LeaseFencedSaver(self.saver, self.repo, ctx) as saver:
                graph = build_workflow(self.repo, self.settings, ctx, saver, self.model)
                config = {
                    "configurable": {"thread_id": ctx.task_id},
                    "recursion_limit": 20,
                    "run_name": "commerce_analysis_workflow",
                    "metadata": {"task_id": ctx.task_id, "mode": task["mode"]},
                }
                snapshot = await graph.aget_state(config)
                if snapshot.interrupts:
                    if (
                        task["status"] == "running"
                        and task["parameters"]["require_review"]
                        and task.get("report")
                    ):
                        with self.repo.session() as db:
                            from app.storage.models import Task

                            decision = db.get(Task, ctx.task_id).review_decision
                        if decision is None:
                            return "waiting_review"
                        result = await graph.ainvoke(
                            Command(resume={"approved": decision}), config=config, durability="sync"
                        )
                    else:
                        return "waiting_review"
                elif snapshot.values:
                    # An empty `next` can mean the last node's writes are durable
                    # but its follow-up checkpoint is not. Let LangGraph apply
                    # those writes/edges; a genuinely finished graph is a no-op.
                    result = await graph.ainvoke(None, config=config, durability="sync")
                else:
                    # A completed step must be durable before the next step can
                    # make business/model side effects. Async persistence can
                    # otherwise lose earlier progress during a later SIGKILL.
                    result = await graph.ainvoke({}, config=config, durability="sync")
                if result.get("__interrupt__"):
                    return "waiting_review"
                return result.get("status", "failed")

        execution = asyncio.create_task(execute())
        heartbeat = asyncio.create_task(heartbeats())
        status, error_code = "failed", None
        try:
            if remaining <= 0:
                raise TimeoutError()
            async with asyncio.timeout(remaining):
                status = await execution
        except asyncio.CancelledError:
            if self.stopping.is_set():
                await asyncio.to_thread(self.repo.release, ctx)
                return
            status = "cancelled"
        except TaskCancelled:
            status = "cancelled"
        except StaleLease:
            return
        except TimeoutError:
            error_code = "runtime_budget"
        except BudgetExceeded:
            error_code = "model_or_tool_budget"
        except Exception as exc:
            # Exception type is logged; request bodies/keys/provider errors are not.
            error_code = type(exc).__name__
            logger.error("task=%s failed type=%s", ctx.task_id, error_code)
        finally:
            heartbeat.cancel()
            execution.cancel()
            await asyncio.gather(heartbeat, execution, return_exceptions=True)
        try:
            await asyncio.to_thread(
                self.repo.finish, ctx, status, int((time.monotonic() - started) * 1000), error_code
            )
        except StaleLease:
            pass

    async def run_once(self):
        ctx = await asyncio.to_thread(self.repo.claim)
        if not ctx:
            return False
        await self.process(ctx)
        return True

    async def run(self):
        jobs = set()
        try:
            while not self.stopping.is_set():
                jobs = {job for job in jobs if not job.done()}
                if len(jobs) < self.settings.worker_concurrency:
                    ctx = await asyncio.to_thread(self.repo.claim)
                    if ctx:
                        jobs.add(asyncio.create_task(self.process(ctx)))
                        continue
                try:
                    await asyncio.wait_for(self.stopping.wait(), timeout=0.5)
                except TimeoutError:
                    pass
        finally:
            self.stopping.set()
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)


async def main():
    settings = get_settings()
    repo = Repository(settings)
    async with checkpoint_store(settings) as saver:
        worker = Worker(repo, settings, saver)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, worker.stopping.set)
        await worker.run()
    repo.engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
