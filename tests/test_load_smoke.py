import asyncio
from pathlib import Path

import pytest

from scripts import load_smoke


@pytest.mark.parametrize(
    "options",
    [{"requests": 0}, {"concurrency": 17}, {"workers": 5}, {"backend": "external"}],
)
async def test_rejects_unbounded_workload_before_startup(options, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid workload must not create a listener")

    monkeypatch.setattr(load_smoke.socket, "socket", forbidden)
    with pytest.raises(ValueError, match="范围"):
        await load_smoke.run_benchmark(**options)


@pytest.mark.parametrize("inject_failure", [False, True])
async def test_real_http_isolation_and_process_cleanup(monkeypatch, inject_failure):
    # These deliberately unusable parent settings must never reach the children.
    monkeypatch.setenv("AGENT_MODE", "live")
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/forbidden")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-isolation-sentinel")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    spawn, response = asyncio.create_subprocess_exec, load_smoke._response
    children, directories = [], []

    async def capture(*args, **kwargs):
        assert kwargs["env"]["AGENT_MODE"] == "demo"
        assert "OPENAI_API_KEY" not in kwargs["env"]
        assert "HTTPS_PROXY" not in kwargs["env"]
        assert "forbidden" not in kwargs["env"]["DATABASE_URL"]
        directories.append(Path(kwargs["cwd"]))
        child = await spawn(*args, **kwargs)
        children.append(child)
        return child

    def fail_submission(result, expected=200):
        if inject_failure and expected == 202:
            raise RuntimeError("injected after HTTP submission")
        return response(result, expected)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    monkeypatch.setattr(load_smoke, "_response", fail_submission)
    result = await load_smoke.run_benchmark(requests=2, concurrency=2, workers=2)
    assert result["passed"] is not inject_failure
    assert result["unique_tasks"] == 2
    assert result["model_calls"] == 0
    if inject_failure:
        assert result["completed"] == 0
        assert {f["phase"] for f in result["failures"]} == {"submit"}
    else:
        assert result["completed"] == 2
        assert result["failures"] == []
        assert result["completion_ms"]["p95"] >= result["completion_ms"]["p50"] > 0
    assert len(children) == 4
    assert all(child.returncode is not None for child in children)
    assert all(not directory.exists() for directory in directories)


async def test_diagnosis_real_http_two_workers_preserve_bundle_and_versioned_pdf():
    result = await load_smoke.run_benchmark(
        requests=2, concurrency=2, workers=2, profile="commerce_diagnosis"
    )
    assert result["passed"] and result["completed"] == result["unique_tasks"] == 2
    assert result["model_calls"] == 0 and result["worker_processes"] == 2
    assert all(2 <= row["tool_calls"] <= 10 for row in result["outcomes"])
