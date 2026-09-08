"""Conversation behavior across real routing and persistence boundaries."""
import asyncio
import json

import httpx
import pytest

from hyperclaw.inference import Inference
from hyperclaw.memory_manager import MemoryManager
from hyperclaw.model_router import ModelRouter
from hyperclaw.orchestrator import Orchestrator
from hyperclaw.providers import Provider, ProviderRegistry


def wire_message(text, content=None, stop="end_turn"):
    return {"id": "msg_test", "type": "message", "role": "assistant", "model": "test-qwen",
            "content": content or [{"type": "text", "text": text}], "stop_reason": stop,
            "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 3}}


async def runtime(root, handler):
    p = Provider("test", "anthropic", frozenset({"chat", "streaming", "tool_use"}), models={"default": "test-qwen"})
    registry = ProviderRegistry({"test": p}, {"primary": ["test"], "tools": ["test"], "fast": ["test"]})
    router = ModelRouter(Inference(registry, httpx.MockTransport(handler)))
    memory = MemoryManager(root=root)
    await memory.initialize()
    app = Orchestrator(model_router=router, memory=memory)
    await app.initialize()
    return app


@pytest.mark.asyncio
async def test_remember_turn_survives_restart_and_other_session_is_isolated(tmp_path):
    prompts = []
    def respond(request):
        data = json.loads(request.content)
        prompts.append(data)
        return httpx.Response(200, json=wire_message("I will remember teal."))
    app = await runtime(tmp_path, respond)
    try:
        assert await app.chat("Remember my favorite color is teal", session_id="alice") == "I will remember teal."
        assert len(prompts[0]["messages"]) == 1
        await app.chat("Hello", session_id="bob")
        assert prompts[1]["messages"] == [{"role": "user", "content": "Hello"}]
    finally:
        await app.shutdown()
    app = await runtime(tmp_path, respond)
    try:
        await app.chat("What did I say?", session_id="alice")
        assert prompts[-1]["messages"][0]["content"] == "Remember my favorite color is teal"
        assert len(prompts[-1]["messages"]) == 3
        await app.reset_session("alice")
    finally:
        await app.shutdown()
    app = await runtime(tmp_path, respond)
    try:
        await app.chat("Fresh session", session_id="alice")
        assert prompts[-1]["messages"] == [{"role": "user", "content": "Fresh session"}]
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_same_session_turns_are_serialized(tmp_path):
    entered = asyncio.Event()
    release = asyncio.Event()
    prompts = []
    async def respond(request):
        prompts.append(json.loads(request.content)["messages"])
        if len(prompts) == 1:
            entered.set()
            await release.wait()
        return httpx.Response(200, json=wire_message("reply"))
    app = await runtime(tmp_path, respond)
    try:
        first = asyncio.create_task(app.chat("first", session_id="same"))
        await entered.wait()
        second = asyncio.create_task(app.chat("second", session_id="same"))
        release.set()
        await asyncio.gather(first, second)
        assert [m["content"] for m in prompts[1]] == ["first", "reply", "second"]
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_tool_result_is_returned_to_model_once(tmp_path):
    from hyperclaw.tool_loop import ToolLoop
    calls = []
    def respond(request):
        data = json.loads(request.content)
        if len(data["messages"]) == 1:
            return httpx.Response(200, json=wire_message("", [{"type": "tool_use", "id": "call_1", "name": "lookup", "input": {"key": "color"}}], "tool_use"))
        assert data["messages"][-1]["content"] == [{"type": "tool_result", "tool_use_id": "call_1", "content": "teal"}]
        return httpx.Response(200, json=wire_message("The color is teal."))
    app = await runtime(tmp_path, respond)
    def lookup(name, inputs):
        calls.append((name, inputs))
        return "teal"
    loop = ToolLoop(app._model_router.inference,
        [{"name": "lookup", "description": "Look up a key", "input_schema": {"type": "object", "properties": {"key": {"type": "string"}}}}], lookup)
    try:
        output = [text async for kind, text in loop.run([{"role": "user", "content": "Find the color"}], "Use lookup.") if kind == "text"]
        assert "".join(output) == "The color is teal."
        assert calls == [("lookup", {"key": "color"})]
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_closing_stream_flushes_history_and_releases_session(tmp_path):
    def respond(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"partial"}}\n\n'
            'data: {"type":"message_stop"}\n\n')
    app = await runtime(tmp_path, respond)
    events = app.stream_events("hello", session_id="cancelled", tools=False)
    try:
        assert await anext(events) == ("text", "partial")
        await events.aclose()
        assert not app._session_locks["cancelled"].locked()
        fresh = MemoryManager(root=tmp_path)
        await fresh.initialize()
        assert [m["content"] for m in await fresh.load_conversation("cancelled")] == ["hello", "partial"]
    finally:
        await app.shutdown()
