import pytest

from hyperclaw.config import load_settings
from hyperclaw.contracts import Message, ProviderFailure
from hyperclaw.ollama import Ollama
from tests.support.provider import MODEL, ProviderStub, Reply
from tests.support.tool_provider import (
    TOOL_SCHEMAS,
    block_delta,
    block_start,
    block_stop,
    frames,
    message_end,
    message_start,
    tool_block,
)


async def collect(tmp_path, events, *, tools=TOOL_SCHEMAS, split_bytes=0):
    peer = ProviderStub()
    peer.enqueue(Reply(frames=frames(*events), split_bytes=split_bytes))
    settings = load_settings(
        root=tmp_path,
        environ={},
        overrides={"ollama_url": peer.url, "model": MODEL},
    )
    model = Ollama(settings)
    received = []
    error = None
    try:
        try:
            async for event in model.stream(
                [Message(role="user", content="inspect the workspace")],
                system="Use tools carefully.",
                tools=tools,
            ):
                received.append(event)
        except ProviderFailure as exc:
            error = exc
        request = peer.take_request()
        assert peer.requests.empty(), "No implicit retry"
        return received, error, request
    finally:
        await model.aclose()
        peer.close()


async def test_split_tool_json_emits_complete_calls_and_finish_content(tmp_path):
    events = [
        message_start(),
        block_start(0, {"type": "thinking", "thinking": "", "signature": ""}),
        block_delta(0, {"type": "thinking_delta", "thinking": "check "}),
        block_delta(0, {"type": "thinking_delta", "thinking": "first"}),
        block_delta(0, {"type": "signature_delta", "signature": "signed"}),
        block_stop(0),
        block_start(1, {"type": "text", "text": "I will "}),
        block_delta(1, {"type": "text_delta", "text": "inspect."}),
        block_stop(1),
        *tool_block(2, "call-read", "workspace_read", ('{"path":', '"README.md"}')),
        *tool_block(3, "call-list", "workspace_list", ("{", "}")),
        *message_end(),
    ]

    received, error, request = await collect(tmp_path, events, split_bytes=3)

    assert error is None
    assert request["tools"] == TOOL_SCHEMAS
    assert [event.data for event in received if event.kind == "tool_call"] == [
        {"id": "call-read", "name": "workspace_read", "arguments": {"path": "README.md"}},
        {"id": "call-list", "name": "workspace_list", "arguments": {}},
    ]
    assert [event.data["text"] for event in received if event.kind == "thinking"] == ["check ", "first"]
    assert [event.data["text"] for event in received if event.kind == "text"] == ["I will ", "inspect."]
    assert received[-2].kind == "usage"
    assert received[-1].kind == "finish"
    assert received[-1].data == {
        "stop_reason": "tool_use",
        "content": [
            {"type": "thinking", "thinking": "check first", "signature": "signed"},
            {"type": "text", "text": "I will inspect."},
            {
                "type": "tool_use",
                "id": "call-read",
                "name": "workspace_read",
                "input": {"path": "README.md"},
            },
            {"type": "tool_use", "id": "call-list", "name": "workspace_list", "input": {}},
        ],
    }


async def test_tools_are_omitted_when_disabled_and_tool_use_fails_explicitly(tmp_path):
    events = [message_start(), *tool_block(0, "call-read", "workspace_read", ("{}",))]

    received, error, request = await collect(tmp_path, events, tools=None)

    assert "tools" not in request
    assert error is not None and error.code == "tools_disabled"
    assert not any(event.kind in {"tool_call", "finish"} for event in received)


@pytest.mark.parametrize(
    ("events", "tools"),
    [
        (
            [message_start(), *tool_block(0, "call-unknown", "command", ("{}",))],
            TOOL_SCHEMAS,
        ),
        (
            [
                message_start(),
                block_start(
                    0,
                    {"type": "tool_use", "id": "call-bad", "name": "workspace_read", "input": {}},
                ),
                block_delta(0, {"type": "input_json_delta", "partial_json": '{"path":'}),
                block_stop(0),
            ],
            TOOL_SCHEMAS,
        ),
        (
            [
                message_start(),
                block_start(
                    0,
                    {"type": "tool_use", "id": "call-bad", "name": "workspace_read", "input": {}},
                ),
                block_delta(0, {"type": "input_json_delta", "partial_json": "[]"}),
                block_stop(0),
            ],
            TOOL_SCHEMAS,
        ),
        (
            [
                message_start(),
                block_start(
                    0,
                    {"type": "tool_use", "id": "call-bad", "name": "workspace_read", "input": {}},
                ),
                block_delta(0, {"type": "input_json_delta", "partial_json": 42}),
            ],
            TOOL_SCHEMAS,
        ),
    ],
    ids=["unoffered-name", "incomplete-json", "non-object-json", "malformed-delta"],
)
async def test_invalid_tool_blocks_never_emit_calls_or_finish(tmp_path, events, tools):
    received, error, _ = await collect(tmp_path, events, tools=tools)

    assert error is not None and error.code == "invalid_stream"
    assert not any(event.kind in {"tool_call", "finish"} for event in received)


async def test_duplicate_tool_call_ids_are_rejected(tmp_path):
    events = [
        message_start(),
        *tool_block(0, "same-id", "workspace_read", ('{"path":"one"}',)),
        *tool_block(1, "same-id", "workspace_read", ('{"path":"two"}',)),
        *message_end(),
    ]

    received, error, _ = await collect(tmp_path, events)

    assert error is not None and error.code == "invalid_stream"
    assert [event.data["id"] for event in received if event.kind == "tool_call"] == ["same-id"]
    assert not any(event.kind == "finish" for event in received)


async def test_tool_arguments_are_bounded_before_json_completion(tmp_path):
    events = [
        message_start(),
        block_start(
            0,
            {"type": "tool_use", "id": "large", "name": "workspace_read", "input": {}},
        ),
        block_delta(0, {"type": "input_json_delta", "partial_json": '"' + "x" * (64 * 1024) + '"'}),
    ]

    received, error, _ = await collect(tmp_path, events)

    assert error is not None and error.code == "invalid_stream"
    assert not any(event.kind in {"tool_call", "finish"} for event in received)


async def test_more_than_sixteen_tool_calls_is_rejected(tmp_path):
    calls = [
        event
        for index in range(17)
        for event in tool_block(index, f"call-{index}", "workspace_list", ("{}",))
    ]

    received, error, _ = await collect(tmp_path, [message_start(), *calls, *message_end()])

    assert error is not None and error.code == "invalid_stream"
    assert len([event for event in received if event.kind == "tool_call"]) == 16
    assert not any(event.kind == "finish" for event in received)


async def test_tool_call_without_message_stop_never_finishes(tmp_path):
    received, error, _ = await collect(
        tmp_path,
        [message_start(), *tool_block(0, "call-read", "workspace_read", ("{}",)), *message_end()[:-1]],
    )

    assert error is not None and error.code == "incomplete_stream"
    assert [event.data["id"] for event in received if event.kind == "tool_call"] == ["call-read"]
    assert not any(event.kind == "finish" for event in received)


async def test_tool_use_stop_reason_requires_a_complete_call(tmp_path):
    received, error, _ = await collect(tmp_path, [message_start(), *message_end()])

    assert error is not None and error.code == "invalid_stream"
    assert not any(event.kind == "finish" for event in received)


@pytest.mark.parametrize(
    "events",
    [
        [
            message_start(),
            block_start(0, {"type": "thinking", "thinking": "", "signature": ""}),
            block_delta(0, {"type": "signature_delta", "signature": "x" * (32 * 1024)}),
            block_delta(0, {"type": "signature_delta", "signature": "y" * (32 * 1024 + 1)}),
        ],
        [
            message_start(),
            block_start(
                0,
                {"type": "thinking", "thinking": "", "signature": "é" * (32 * 1024 + 1)},
            ),
        ],
        [
            message_start(),
            block_start(
                0,
                {"type": "thinking", "thinking": "", "signature": "x" * (32 * 1024 + 1)},
            ),
            block_stop(0),
            block_start(
                1,
                {"type": "thinking", "thinking": "", "signature": "y" * (32 * 1024 + 1)},
            ),
            block_stop(1),
            *message_end("end_turn"),
        ],
    ],
    ids=["signature-fragments", "initial-signature", "multiple-thinking-blocks"],
)
async def test_thinking_signatures_are_bounded(tmp_path, events):
    received, error, _ = await collect(tmp_path, events)

    assert error is not None and error.code == "response_limit"
    assert not any(event.kind == "finish" for event in received)


async def test_empty_content_blocks_cannot_bypass_response_bounds(tmp_path):
    events = [message_start()]
    for index in range(257):
        events += [block_start(index, {'type':'thinking','thinking':'','signature':''}), block_stop(index)]
    events += message_end('end_turn')
    received, error, _ = await collect(tmp_path, events)
    assert error is not None and error.code == 'invalid_stream'
    assert not any(event.kind == 'finish' for event in received)
