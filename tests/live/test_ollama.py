"""Real-model smoke contracts; run explicitly with --run-ollama -m ollama.

All prompts, images, and disk state are synthetic. The selected daemon/model
must already exist. Each scenario has a 120-second wall-clock bound.
"""

import asyncio
import base64
import json
import struct
from uuid import uuid4
import zlib

import pytest


pytestmark = [pytest.mark.ollama, pytest.mark.asyncio]


def assert_accounted(app, model, calls):
    stats = app._model_router.get_stats()
    assert stats["total_requests"] == calls
    assert stats["requests_by_model"] == {model: calls}
    assert app._model_router.stats.total_input_tokens > 0
    assert app._model_router.stats.total_output_tokens > 0
    assert stats["cost_complete"] is True
    assert stats["total_cost_usd"] == 0


async def test_plain_response_has_local_provenance_and_usage(ollama_runtime_factory, ollama_target):
    # Catches empty/error replies, wrong-provider routing, and lost usage counts.
    async with asyncio.timeout(120), ollama_runtime_factory() as app:
        text = await app.chat("Describe a paper kite in one short sentence.", session_id="plain", tools=False)
        assert text.strip()
        assert "[Error:" not in text
        assert app._last_turns["plain"]["served_by"] == f"ollama/{ollama_target.model}"
        assert app._last_turns["plain"]["tools_used"] == []
        assert_accounted(app, ollama_target.model, 1)
        history = await app._memory.load_conversation("plain")
        assert [message["role"] for message in history] == ["user", "assistant"]
        assert history[-1]["content"] == text


async def test_stream_returns_text_and_accounts_once(ollama_runtime_factory, ollama_target, request):
    # Catches an ignored SSE format/completion marker or missing stream accounting.
    async with asyncio.timeout(120), ollama_runtime_factory() as app:
        events = [event async for event in app.stream_events(
            "Describe a sailing boat in two short sentences.", session_id="stream", tools=False,
        )]
        chunks = [text for kind, text in events if kind == "text"]
        answer = "".join(chunks)
        assert answer.strip()
        assert all(kind in {"text", "thinking"} for kind, _ in events)
        assert "stream interrupted" not in answer
        assert "[Error:" not in answer
        assert app._last_turns["stream"]["served_by"] == f"ollama/{ollama_target.model}"
        assert_accounted(app, ollama_target.model, 1)
        assert (await app._memory.load_conversation("stream"))[-1]["content"] == answer
        request.node.user_properties.append(("ollama_text_chunks", len(chunks)))


async def test_conversation_resumes_without_leaking_to_other_or_reset_sessions(ollama_runtime_factory):
    # The opaque value cannot come from model knowledge; dropped history fails
    # the positive check, and reused session state fails either negative check.
    codeword = f"otter-{uuid4().hex[:16]}"
    question = "What is this conversation's synthetic exhibit label? Reply with it only, or UNKNOWN if absent."
    async with asyncio.timeout(120):
        async with ollama_runtime_factory() as first:
            answer = await first.chat(
                f"This conversation's synthetic exhibit label is {codeword}. Reply with it only.",
                session_id="alice", tools=False,
            )
            assert codeword in answer, f"Synthetic model reply: {answer!r}"
            # This test must exercise saved conversation history, not memory recall.
            assert await first._memory.list_memories() == []
        async with ollama_runtime_factory() as resumed:
            answer = await resumed.chat(question, session_id="alice", tools=False)
            assert codeword in answer, f"Synthetic model reply: {answer!r}"
            other = await resumed.chat(question, session_id="bob", tools=False)
            assert other.strip() and codeword not in other
            assert len(await resumed._memory.load_conversation("bob")) == 2
            await resumed.reset_session("alice")
        async with ollama_runtime_factory() as reset:
            assert await reset._memory.load_conversation("alice") == []
            answer = await reset.chat(question, session_id="alice", tools=False)
            assert answer.strip() and codeword not in answer
            assert len(await reset._memory.load_conversation("alice")) == 2


async def test_memory_tools_persist_and_return_fresh_manager_search_results(
    ollama_runtime_factory, ollama_target, canonical_memory_schemas,
):
    # A plausible acknowledgement is insufficient: require committed tool input
    # and a second real-model turn whose only source for the opaque label is the tool.
    ollama_target.require("tools")
    from hyperclaw.memory_manager import MemoryManager
    from hyperclaw.memory_tools import bind_memory_tools
    from hyperclaw.tool_loop import ToolLoop

    codeword = f"amber-{uuid4().hex[:16]}"
    # An exhibit label avoids testing a model's willingness to retain passwords.
    fact = f"The synthetic Juniper observatory exhibit label is {codeword}."

    def reject_other_tools(name, inputs):
        pytest.fail(f"Unexpected non-memory tool: {name}")

    async with asyncio.timeout(120), ollama_runtime_factory() as app:
        reply = await app.chat(
            'Call memory_store exactly once with domain "smoke", source "synthetic", '
            f'and this exact content: {json.dumps(fact)}. Then give a short acknowledgement.',
            session_id="store", tools=True,
            tool_set=([canonical_memory_schemas["memory_store"]], reject_other_tools),
        )
        assert reply.strip()
        calls = app._last_turns["store"]["tools_used"]
        assert [call["name"] for call in calls] == ["memory_store"], f"Synthetic model reply: {reply!r}"
        fresh = MemoryManager(root=app._memory.root)
        await fresh.initialize()
        stored = await fresh.recall("Juniper observatory", domain="smoke")
        assert len(stored) == 1
        assert stored[0].content == fact
        assert stored[0].source == "synthetic"

        # No conversation history or orchestrator pre-retrieval can reveal the
        # codeword here. The real memory_search result must reach the model.
        loop = ToolLoop(
            app._model_router.inference, [canonical_memory_schemas["memory_search"]],
            bind_memory_tools(fresh, reject_other_tools), max_rounds=3, timeout=110,
        )
        output = "".join([text async for kind, text in loop.run(
            [{"role": "user", "content": 'Call memory_search exactly once with query "Juniper observatory" '
              'and domain "smoke". Return its exhibit label, preserving it exactly.'}],
            "Use the provided tool to answer. Do not invent a label.", max_tokens=256,
        ) if kind == "text"])
        assert [call["name"] for call in loop.tools_used] == ["memory_search"], f"Synthetic model reply: {output!r}"
        assert codeword in output, f"Synthetic model reply: {output!r}"
        assert_accounted(app, ollama_target.model, 4)


def red_png():
    """Small synthetic RGB fixture, generated without an optional image library."""
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))

    header = struct.pack("!2I5B", 64, 64, 8, 2, 0, 0, 0)
    scanlines = (b"\0" + b"\xff\0\0" * 64) * 64
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b"")


async def test_synthetic_image_reaches_selected_vision_model(ollama_runtime_factory, ollama_target):
    # Dropping or mistranslating the image attachment loses the only color cue.
    ollama_target.require("vision")
    async with asyncio.timeout(120), ollama_runtime_factory() as app:
        response = await app.chat(
            "What is the single dominant color of this image? Reply with just the color name.",
            session_id="vision", tools=False, attachments=[{
                "type": "image", "source": {
                    "type": "base64", "media_type": "image/png",
                    "data": base64.b64encode(red_png()).decode("ascii"),
                },
            }],
        )
        assert response.strip(" \t\r\n.*`'\"!").casefold() == "red", f"Synthetic vision reply: {response!r}"
        assert app._last_turns["vision"]["served_by"] == f"ollama/{ollama_target.model}"
        assert_accounted(app, ollama_target.model, 1)
