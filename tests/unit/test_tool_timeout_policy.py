"""Configured tool deadlines must survive adapter and runtime dispatch."""
import asyncio
import threading

import httpx
import pytest

from hyperclaw.tool_loop import ToolLoop
from tests.unit.test_adapter_runtime import bind_runtime, make_bridge
from tests.unit.test_runtime import wire_message


@pytest.mark.asyncio
async def test_bridge_preserves_long_tool_override_in_shared_loop(tmp_path, monkeypatch):
    # Compress only the wait_for clock so a 200-second synthetic operation
    # takes 0.2 seconds. The actual worker still blocks and is released once.
    real_wait_for = asyncio.wait_for
    async def accelerated_wait_for(work, timeout):
        return await real_wait_for(work, timeout=timeout / 1000)
    monkeypatch.setattr(asyncio, "wait_for", accelerated_wait_for)
    wire_calls = 0
    def respond(request):
        nonlocal wire_calls
        wire_calls += 1
        if wire_calls == 1:
            return httpx.Response(200, json=wire_message("", [{"type": "tool_use", "id": "long", "name": "slow_report", "input": {}}], "tool_use"))
        return httpx.Response(200, json=wire_message("Report ready."))
    app = await bind_runtime(monkeypatch, tmp_path, respond)
    event_loop = asyncio.get_running_loop()
    release = threading.Event()
    executions = []
    def execute(name, inputs):
        executions.append(name)
        event_loop.call_soon_threadsafe(event_loop.call_later, 0.2, release.set)
        release.wait(2)
        return "report content"
    bridge = make_bridge(monkeypatch,
        [{"name": "slow_report", "input_schema": {"type": "object"}}], execute)
    bridge._TOOL_TIMEOUTS = {"slow_report": 300}
    bridge._DEFAULT_TOOL_TIMEOUT = 10
    try:
        async with asyncio.timeout(3):
            result = await bridge.execute("Build the synthetic report", 908)
        assert result["success"] is True, result
        assert result["text"] == "Report ready."
        assert executions == ["slow_report"]
        assert wire_calls == 2
    finally:
        release.set()
        await app.shutdown()


@pytest.mark.asyncio
async def test_environment_default_timeout_ends_turn_without_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("HYPERCLAW_TOOL_TIMEOUT", "0.02")
    wire_calls = []
    cancelled = asyncio.Event()
    def respond(request):
        wire_calls.append(request)
        return httpx.Response(200, json=wire_message("", [{"type": "tool_use", "id": "short", "name": "wait_forever", "input": {}}], "tool_use"))
    app = await bind_runtime(monkeypatch, tmp_path, respond)
    async def execute(name, inputs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    loop = ToolLoop(app._model_router.inference, [{"name": "wait_forever", "input_schema": {}}], execute)
    try:
        with pytest.raises(TimeoutError, match="Tool wait_forever timed out"):
            async with asyncio.timeout(0.5):
                _ = [item async for item in loop.run([{"role": "user", "content": "wait"}], "")]
        assert cancelled.is_set()
        assert len(wire_calls) == 1
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_overall_turn_deadline_caps_even_long_tools(tmp_path):
    from tests.unit.test_runtime import runtime
    def respond(request):
        return httpx.Response(200, json=wire_message("", [{"type": "tool_use", "id": "one", "name": "deep_research", "input": {}}], "tool_use"))
    app = await runtime(tmp_path, respond)
    async def execute(name, inputs):
        await asyncio.Event().wait()
    loop = ToolLoop(app._model_router.inference, [{"name": "deep_research", "input_schema": {}}], execute, timeout=0.02)
    try:
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.5):
                _ = [item async for item in loop.run([{"role": "user", "content": "research"}], "")]
    finally:
        await app.shutdown()
