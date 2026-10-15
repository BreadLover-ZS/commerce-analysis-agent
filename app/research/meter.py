"""Meter at the model boundary, including SDK-internal summarization calls."""

import asyncio
import time

from langchain_core.callbacks import AsyncCallbackHandler

from app.core.errors import provider_error_data


class RuntimeModelMeter(AsyncCallbackHandler):
    raise_error = True
    run_inline = True

    def __init__(self, repo, ctx):
        self.repo, self.ctx = repo, ctx
        self.starts = {}

    async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        # Callback exceptions must propagate; a normal logging callback cannot enforce admission.
        await asyncio.to_thread(self.repo.consume, self.ctx, "model")
        phase = (kwargs.get("metadata") or {}).get("phase", "研究/上下文处理")
        self.starts[run_id] = (time.monotonic(), phase)
        await asyncio.to_thread(self.repo.emit, self.ctx, "model_start", f"模型调用开始：{phase}")

    async def on_llm_end(self, response, *, run_id, **kwargs):
        first = (
            response.generations[0][0] if response.generations and response.generations[0] else None
        )
        usage = getattr(getattr(first, "message", None), "usage_metadata", None)
        if not usage and response.llm_output:
            raw = response.llm_output.get("token_usage", {})
            if "prompt_tokens" in raw and "completion_tokens" in raw:
                usage = {
                    "input_tokens": raw["prompt_tokens"],
                    "output_tokens": raw["completion_tokens"],
                }
        await asyncio.to_thread(self.repo.record_usage, self.ctx, usage)
        started, phase = self.starts.pop(run_id, (time.monotonic(), "模型"))
        await asyncio.to_thread(
            self.repo.emit,
            self.ctx,
            "model_end",
            f"模型调用结束：{phase}",
            {"elapsed_ms": int((time.monotonic() - started) * 1000)},
        )

    async def on_llm_error(self, error, *, run_id, **kwargs):
        if run_id not in self.starts:
            return
        self.starts.pop(run_id)
        await asyncio.to_thread(self.repo.record_usage, self.ctx, None)
        # During cancellation, usage is recorded but ordinary business events are fenced off.
        from app.storage.repository import TaskCancelled

        try:
            await asyncio.to_thread(
                self.repo.emit,
                self.ctx,
                "model_error",
                "模型请求失败，Token使用量未知",
                provider_error_data(error),
            )
        except TaskCancelled:
            pass


def attach_meter(model, repo, ctx):
    callbacks = model.callbacks or []
    if not isinstance(callbacks, list):
        raise ValueError("本项目需要列表形式的模型 callbacks")
    model.callbacks = [c for c in callbacks if not isinstance(c, RuntimeModelMeter)] + [
        RuntimeModelMeter(repo, ctx)
    ]
    return model
