"""Anthropic Messages SSE fixtures for tool transport tests."""

from __future__ import annotations

import json

from tests.support.provider import MODEL


TOOL_SCHEMAS = [
    {
        "name": "workspace_read",
        "description": "Read a workspace file.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "workspace_list",
        "description": "List a workspace directory.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "additionalProperties": False,
        },
    },
]


def frame(value: dict) -> bytes:
    return (f"event: {value['type']}\r\ndata: {json.dumps(value)}\r\n\r\n").encode()


def message_start() -> dict:
    return {
        "type": "message_start",
        "message": {
            "type": "message",
            "role": "assistant",
            "model": MODEL,
            "content": [],
            "stop_reason": None,
            "usage": {"input_tokens": 12},
        },
    }


def block_start(index: int, block: dict) -> dict:
    return {"type": "content_block_start", "index": index, "content_block": block}


def block_delta(index: int, delta: dict) -> dict:
    return {"type": "content_block_delta", "index": index, "delta": delta}


def block_stop(index: int) -> dict:
    return {"type": "content_block_stop", "index": index}


def message_end(reason: str = "tool_use") -> list[dict]:
    return [
        {
            "type": "message_delta",
            "delta": {"stop_reason": reason, "stop_sequence": None},
            "usage": {"output_tokens": 9},
        },
        {"type": "message_stop"},
    ]


def tool_block(index: int, call_id: str, name: str, partials: tuple[str, ...]) -> list[dict]:
    return [
        block_start(index, {"type": "tool_use", "id": call_id, "name": name, "input": {}}),
        *(block_delta(index, {"type": "input_json_delta", "partial_json": part}) for part in partials),
        block_stop(index),
    ]


def frames(*events: dict) -> tuple[bytes, ...]:
    return tuple(frame(event) for event in events)
