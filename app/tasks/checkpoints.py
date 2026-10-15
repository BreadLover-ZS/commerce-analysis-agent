"""Lease admission serialized with checkpoint writes, across worker processes."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

import aiosqlite
from langgraph.checkpoint.base import BaseCheckpointSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.storage.repository import validate_task_lease


class LeaseFencedSaver(BaseCheckpointSaver):
    def __init__(self, inner, repo, ctx):
        super().__init__(serde=inner.serde)
        self.inner, self.repo, self.ctx = inner, repo, ctx
        self.postgres = repo.engine.dialect.name == "postgresql"
        self.connection = None
        self.write_lock = asyncio.Lock()

    async def __aenter__(self):
        if self.postgres:
            self.connection = await AsyncConnection.connect(
                self.repo.settings.checkpoint_url,
                autocommit=True,
                row_factory=dict_row,
                connect_timeout=5,
            )
        else:
            self.connection = await aiosqlite.connect(self.repo.engine.url.database, timeout=20)
            self.connection.row_factory = aiosqlite.Row
        return self

    async def __aexit__(self, *args):
        await self.connection.close()

    @property
    def config_specs(self):
        return self.inner.config_specs

    def _scope(self, config):
        thread = config.get("configurable", {}).get("thread_id") if config else None
        if thread not in {self.ctx.task_id, self.ctx.task_id + ":research"}:
            raise ValueError("检查点线程不属于当前任务")

    @asynccontextmanager
    async def _write_transaction(self, config):
        self._scope(config)
        if self.connection is None:
            raise RuntimeError("检查点租约连接未打开")
        # One connection is shared by concurrent checkpoint/pending-write calls
        # of this task. Different tasks retain separate connections and row locks.
        async with self.write_lock:
            if self.postgres:
                async with self.connection.transaction():
                    async with await self.connection.execute(
                        "SELECT lease_token, lease_until, status FROM tasks WHERE id=%s FOR UPDATE",
                        (self.ctx.task_id,),
                    ) as cursor:
                        row = await cursor.fetchone()
                    validate_task_lease(
                        SimpleNamespace(**row) if row else None, self.ctx, allow_cancel=True
                    )
                    yield
            else:
                try:
                    await self.connection.execute("BEGIN IMMEDIATE")
                    async with self.connection.execute(
                        "SELECT lease_token, lease_until, status FROM tasks WHERE id=?",
                        (self.ctx.task_id,),
                    ) as cursor:
                        row = await cursor.fetchone()
                    task = SimpleNamespace(**dict(row)) if row else None
                    if task and task.lease_until:
                        task.lease_until = datetime.fromisoformat(task.lease_until)
                    validate_task_lease(task, self.ctx, allow_cancel=True)
                    yield
                    await self.connection.commit()
                except BaseException:
                    await self.connection.rollback()
                    raise

    async def aget_tuple(self, config):
        self._scope(config)
        return await self.inner.aget_tuple(config)

    async def alist(self, config, *, filter=None, before=None, limit=None):
        self._scope(config)
        async for item in self.inner.alist(config, filter=filter, before=before, limit=limit):
            yield item

    async def aput(self, config, checkpoint, metadata, new_versions):
        async with self._write_transaction(config):
            return await self.inner.aput(config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        async with self._write_transaction(config):
            await self.inner.aput_writes(config, writes, task_id, task_path)

    def get_next_version(self, current, channel):
        return self.inner.get_next_version(current, channel)

    async def aget_delta_channel_history(self, *, config, channels):
        self._scope(config)
        return await self.inner.aget_delta_channel_history(config=config, channels=channels)
