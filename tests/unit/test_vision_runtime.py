"""The computer-vision tool uses the selected transport without a cloud key."""
import ast
import asyncio
import base64
import json
from pathlib import Path

import httpx
import pytest

from hyperclaw.inference import Inference
from hyperclaw.providers import Provider, ProviderRegistry
from hyperclaw.tool_loop import ToolLoop
from tests.unit.test_runtime import wire_message


@pytest.mark.asyncio
async def test_vision_tool_uses_current_inference_in_worker_thread(tmp_path):
    # Import just the real function to avoid platform tool initialization.
    path = Path(__file__).resolve().parents[2] / "hyperclaw" / "tui.py"
    node = next(node for node in ast.parse(path.read_text()).body if isinstance(node, ast.FunctionDef) and node.name == "vision")
    namespace = {"__name__": "hyperclaw.tui", "__package__": "hyperclaw", "Path": Path, "base64": base64}
    from hyperclaw.api_utils import extract_text
    namespace["extract_text"] = extract_text
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    picture = tmp_path / "synthetic.png"
    picture.write_bytes(b"synthetic fixture")
    requests = []
    def respond(request):
        data = json.loads(request.content)
        requests.append(data)
        if len(requests) == 1:
            return httpx.Response(200, json=wire_message("", [{"type": "tool_use", "id": "see", "name": "vision", "input": {"image_path": str(picture)}}], "tool_use"))
        if len(requests) == 2:
            assert data["messages"][0]["content"][0]["type"] == "image"
            return httpx.Response(200, json=wire_message("A synthetic image."))
        assert "A synthetic image." in data["messages"][-1]["content"][0]["content"]
        return httpx.Response(200, json=wire_message("Image analyzed."))
    provider = Provider("test", "anthropic", frozenset({"chat", "images", "tool_use"}), models={"default": "local-qwen"})
    inference = Inference(ProviderRegistry({"test": provider}, {"tools": ["test"], "vision": ["test"]}), httpx.MockTransport(respond))
    loop = ToolLoop(inference, [{"name": "vision", "input_schema": {}}], lambda name, inputs: namespace["vision"](**inputs))
    assert [text async for _, text in loop.run([{"role": "user", "content": "Inspect image"}], "")] == ["Image analyzed."]
    assert [r["model"] for r in requests] == ["local-qwen"] * 3
