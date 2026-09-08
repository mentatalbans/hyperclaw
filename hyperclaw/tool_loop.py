"""Bounded tool execution using the same inference transport as plain chat."""
from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections import Counter


class ToolLoop:
    def __init__(self, inference, tools, execute, *, max_rounds=12, timeout=600):
        self.inference = inference
        self.tools = tools
        self.execute = execute
        self.max_rounds = max_rounds
        self.timeout = timeout
        self.tools_used = []
        self.model_used = ""

    async def run(self, messages, system, *, model_override=None, max_tokens=4096):
        messages = list(messages)
        allowed = {tool["name"] for tool in self.tools}
        repeated = Counter()
        deadline = time.monotonic() + self.timeout
        for _ in range(self.max_rounds):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Tool turn exceeded its time limit")
            async with asyncio.timeout(remaining):
                response, metadata = await self.inference.complete(
                    messages, system, slot="tools", tools=self.tools,
                    model_override=model_override, max_tokens=max_tokens)
            self.model_used = metadata["model"]
            blocks = [block.model_dump(exclude_none=True) for block in response.content]
            calls = [block for block in blocks if block["type"] == "tool_use"]
            for block in blocks:
                if block["type"] == "text":
                    yield "text", block["text"]
            if not calls:
                if response.stop_reason == "max_tokens":
                    yield "text", "\n[Response reached the output limit.]"
                return
            if response.stop_reason == "max_tokens":
                raise RuntimeError("Model output was truncated before its tool call completed; no tools were executed")
            messages.append({"role": "assistant", "content": blocks})
            results = []
            for call in calls:
                name, inputs = call["name"], call["input"]
                if name not in allowed:
                    result = f"Error: tool {name!r} was not offered for this turn"
                else:
                    signature = (name, json.dumps(inputs, sort_keys=True))
                    repeated[signature] += 1
                    if repeated[signature] > 3:
                        raise RuntimeError(f"Stopped repeated identical calls to {name}")
                    self.tools_used.append({"name": name, "input": inputs})
                    try:
                        from .inference import inference_context
                        remaining = max(0, deadline - time.monotonic())
                        with inference_context(self.inference):
                            if inspect.iscoroutinefunction(self.execute):
                                work = self.execute(name, inputs)
                            else:
                                work = asyncio.to_thread(self.execute, name, inputs)
                            result = await asyncio.wait_for(work, timeout=min(120, remaining))
                    except asyncio.TimeoutError:
                        # A timed-out thread can still have side effects. Ending
                        # the turn prevents the model from blindly retrying it.
                        raise TimeoutError(f"Tool {name} timed out and may still be running; do not retry blindly")
                    except Exception as exc:
                        result = f"Tool error: {exc}"
                results.append({"type": "tool_result", "tool_use_id": call["id"], "content": str(result)[:12000]})
            messages.append({"role": "user", "content": results})
        raise RuntimeError(f"Stopped after {self.max_rounds} tool rounds")


def local_tools():
    """The existing core computer tools, without unrelated integration schemas."""
    from .agent import TOOLS, execute_tool
    names = {"bash", "read_file", "write_file", "edit_file", "glob", "grep", "list_dir"}
    return [tool for tool in TOOLS if tool["name"] in names], execute_tool
