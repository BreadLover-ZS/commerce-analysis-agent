"""Plan-only SDK/validation feasibility checks. MockTransport only, no credentials."""

import argparse
import asyncio
import json
import socket
from contextvars import ContextVar
from pathlib import Path

import httpx
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Choice(BaseModel):
    action: str


class LocalDenied(RuntimeError):
    pass


ATTEMPT = ContextVar("plan_attempt", default=None)


class Meter(AsyncCallbackHandler):
    run_inline = True
    raise_error = True

    def __init__(self):
        self.start_context = None
        self.error_chain = []

    async def on_chat_model_start(self, *args, **kwargs):
        self.start_context = ATTEMPT.get()

    async def on_llm_error(self, error, **kwargs):
        self.error_chain = chain(error)


def chain(exc):
    seen = set()
    result = []
    while exc is not None and id(exc) not in seen and len(result) < 8:
        seen.add(id(exc))
        result.append(type(exc).__name__)
        exc = exc.__cause__
    return result


async def scenario(mode):
    count = 0
    hook_context = None
    meter = Meter()

    async def hook(request):
        nonlocal hook_context
        hook_context = ATTEMPT.get()
        if mode == "typed":
            raise LocalDenied("local_guard_denied")
        if mode == "string":
            raise RuntimeError("evaluation_token_budget_capacity")

    def transport(request):
        nonlocal count
        count += 1
        if mode == "timeout":
            raise httpx.ReadTimeout("simulated after transport entry", request=request)
        return httpx.Response(
            200,
            json={
                "id": "local",
                "model": "stub",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": '{"action":"stop"}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), event_hooks={"request": [hook]}, trust_env=False
    ) as client:
        model = ChatOpenAI(
            api_key="local-placeholder-not-a-key",
            model="stub",
            base_url="https://example.invalid/v1",
            max_retries=0,
            http_async_client=client,
            callbacks=[meter],
        )
        token = ATTEMPT.set("attempt-" + mode)
        result = []
        try:
            await model.with_structured_output(
                Choice, method="json_mode", include_raw=True
            ).ainvoke("local protocol shape only")
        except Exception as exc:
            result = chain(exc)
        finally:
            ATTEMPT.reset(token)
    return {
        "mode": mode,
        "transport_entries": count,
        "request_hook_context": hook_context,
        "callback_start_context": meter.start_context,
        "outer_error_chain": result,
        "callback_error_chain": meter.error_chain,
    }


async def main(path):
    network = []

    def forbidden(*args, **kwargs):
        network.append(True)
        raise AssertionError("network forbidden")

    socket.socket.connect = forbidden
    cases = [await scenario(mode) for mode in ["typed", "string", "timeout", "ok"]]

    class Box(BaseModel):
        model_config = ConfigDict(validate_assignment=True)
        values: list[int] = Field(max_length=1)

    box = Box(values=[1])
    box.values.append(2)
    accepted_instance = Box.model_validate(box) is box
    dict_rejected = False
    try:
        Box.model_validate(box.model_dump())
    except ValidationError:
        dict_rejected = True
    checks = {
        "typed_denial_stays_in_sdk_cause_chain": cases[0]["transport_entries"] == 0
        and cases[0]["outer_error_chain"] == ["APIConnectionError", "LocalDenied"],
        "callback_sees_same_typed_denial": cases[0]["callback_error_chain"]
        == cases[0]["outer_error_chain"],
        "outer_context_reaches_callback_and_hook": all(
            c["request_hook_context"] == c["callback_start_context"] == "attempt-" + c["mode"]
            for c in cases
        ),
        "same_error_text_is_not_typed_proof": cases[1]["outer_error_chain"]
        == ["APIConnectionError", "RuntimeError"],
        "transport_timeout_is_distinct_and_not_unsent": cases[2]["transport_entries"] == 1
        and "LocalDenied" not in cases[2]["outer_error_chain"],
        "success_has_single_transport_entry": cases[3]["transport_entries"] == 1
        and not cases[3]["outer_error_chain"],
        "assignment_validation_does_not_protect_list_append": len(box.values) == 2,
        "model_instance_can_skip_revalidation": accepted_instance,
        "plain_dict_revalidation_rejects_oversize": dict_rejected,
        "real_network_attempts_zero": not network,
    }
    value = {
        "status": "PLAN_FEASIBILITY_ONLY_NOT_A_PRODUCTION_FIX",
        "checks": checks,
        "passed": sum(checks.values()),
        "sdk_scenarios": cases,
        "provider_requests": 0,
        "credentials_read": False,
        "network_attempts": len(network),
    }
    with path.open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    assert all(checks.values()), value
    print(
        json.dumps(
            {
                "passed": sum(checks.values()),
                "provider_requests": 0,
                "network_attempts": len(network),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main(parser.parse_args().output))
