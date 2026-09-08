"""Adapter regressions across canonical sessions, tool selection, and delivery."""

import asyncio
import ast
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from hyperclaw import outbox
from tests.unit.test_runtime import runtime, wire_message

pytestmark = pytest.mark.asyncio


def make_bridge(monkeypatch, definitions=None, execute=None):
    from hyperclaw.tui_bridge import TUIBridge

    monkeypatch.setattr(TUIBridge, "_load_system_prompt", lambda self: None)
    monkeypatch.setattr(TUIBridge, "_load_tools", lambda self: None)
    bridge = TUIBridge()
    bridge.tools = definitions or [{"name": "screenshot", "input_schema": {"type": "object", "properties": {}}}]
    bridge._raw_execute_tool = execute or (lambda name, inputs: "synthetic screenshot")
    return bridge


async def bind_runtime(monkeypatch, root, handler):
    app = await runtime(root, handler)

    async def get_runtime():
        return app

    monkeypatch.setattr("hyperclaw.orchestrator.get_orchestrator", get_runtime)
    return app


async def test_bridge_offers_its_tools_and_returns_files_from_worker_threads(tmp_path, monkeypatch):
    artifact = tmp_path / "synthetic.txt"
    artifact.write_text("synthetic result")
    offered = []
    requests = 0

    def respond(request):
        import json
        nonlocal requests
        requests += 1
        offered.append([tool["name"] for tool in json.loads(request.content)["tools"]])
        if requests == 1:
            blocks = [{"type": "tool_use", "id": "delivery", "name": "send_file", "input": {}}]
            return httpx.Response(200, json=wire_message("", blocks, "tool_use"))
        return httpx.Response(200, json=wire_message("Delivered."))

    app = await bind_runtime(monkeypatch, tmp_path, respond)
    bridge = make_bridge(monkeypatch,
        [{"name": "send_file", "input_schema": {"type": "object", "properties": {}}}],
        lambda name, inputs: outbox.queue_file(str(artifact)),
    )
    try:
        result = await bridge.execute("deliver synthetic result", 171)
        assert result["success"] is True
        assert offered == [["send_file"], ["send_file"]]
        assert [item["path"] for item in result["files"]] == [str(artifact)]
    finally:
        await app.shutdown()
        outbox.drain(171)


@pytest.mark.parametrize("result_format", ["path", "dict", "message"])
async def test_bridge_returns_screenshot_paths_without_reexecuting_tools(tmp_path, monkeypatch, result_format):
    artifact = tmp_path / "synthetic.png"
    artifact.write_bytes(b"synthetic screenshot fixture")
    requests = 0
    executions = []

    def respond(request):
        nonlocal requests
        requests += 1
        if requests == 1:
            blocks = [{"type": "tool_use", "id": "capture", "name": "screenshot", "input": {}}]
            return httpx.Response(200, json=wire_message("", blocks, "tool_use"))
        return httpx.Response(200, json=wire_message("Captured."))

    def capture(name, inputs):
        executions.append(name)
        return {"path": str(artifact)} if result_format == "dict" else f"Screenshot saved: {artifact}" if result_format == "message" else str(artifact)

    app = await bind_runtime(monkeypatch, tmp_path, respond)
    bridge = make_bridge(monkeypatch, execute=capture)
    try:
        result = await bridge.execute("capture a synthetic screen", 178)
        assert result["screenshots"] == [str(artifact)]
        assert executions == ["screenshot"]
    finally:
        await app.shutdown()


async def test_bridge_clear_removes_durable_history_and_staged_attachment_context(tmp_path, monkeypatch):
    prompts = []

    def respond(request):
        import json
        prompts.append(json.loads(request.content)["messages"])
        return httpx.Response(200, json=wire_message("synthetic answer"))

    app = await bind_runtime(monkeypatch, tmp_path, respond)
    bridge = make_bridge(monkeypatch)
    try:
        await bridge.execute("private prior turn", 172)
        bridge.add_to_history(172, "user", "private attachment")
        await bridge.clear_session_async(172)
        assert bridge.get_session_history(172) == []
    finally:
        await app.shutdown()
    app = await bind_runtime(monkeypatch, tmp_path, respond)
    try:
        await bridge.execute("after reset", 172)
        assert prompts[-1] == [{"role": "user", "content": "after reset"}]
    finally:
        await app.shutdown()


async def test_bridge_attachment_history_reaches_the_next_turn_and_getter(tmp_path, monkeypatch):
    prompts = []

    def respond(request):
        import json
        prompts.append(json.loads(request.content)["messages"])
        return httpx.Response(200, json=wire_message("synthetic answer"))

    app = await bind_runtime(monkeypatch, tmp_path, respond)
    bridge = make_bridge(monkeypatch)
    try:
        bridge.add_to_history(173, "user", "image context")
        bridge.add_to_history(173, "assistant", "image description")
        await bridge.execute("describe it again", 173)
        assert [entry["content"] for entry in prompts[-1]] == ["image context", "image description", "describe it again"]
        assert bridge.get_session_history(173)[-1]["content"] == "synthetic answer"
    finally:
        await app.shutdown()


async def test_async_outbox_sessions_survive_thread_dispatch_without_cross_delivery(tmp_path):
    first_ready = asyncio.Event()
    second_ready = asyncio.Event()
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first")
    second.write_text("second")

    async def deliver(chat_id, path, own_ready, other_ready):
        outbox.set_current_session(chat_id)
        own_ready.set()
        await other_ready.wait()
        await asyncio.to_thread(outbox.queue_file, str(path))
        outbox.set_current_session(None)

    await asyncio.gather(deliver(174, first, first_ready, second_ready), deliver(175, second, second_ready, first_ready))
    assert [item["path"] for item in outbox.drain(174)] == [str(first)]
    assert [item["path"] for item in outbox.drain(175)] == [str(second)]


async def test_terminal_keeps_the_tui_tool_catalog(tmp_path, monkeypatch, capsys):
    import hyperclaw
    from hyperclaw import terminal
    offered = []

    def respond(request):
        import json
        offered.append([tool["name"] for tool in json.loads(request.content)["tools"]])
        return httpx.Response(200, json=wire_message("terminal answer"))

    app = await bind_runtime(monkeypatch, tmp_path, respond)
    tools = [{"name": "screenshot", "input_schema": {"type": "object", "properties": {}}}]
    monkeypatch.setattr(hyperclaw, "tui", SimpleNamespace(TOOLS=tools, execute_tool=lambda name, inputs: "synthetic"), raising=False)
    commands = iter(["inspect synthetic screen", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(commands))
    monkeypatch.setattr("hyperclaw.local.load_profile", lambda: None)
    await terminal.run()
    assert offered == [["screenshot"]]
    assert "terminal answer" in capsys.readouterr().out
    assert app._initialized is False


async def test_agent_adapter_keeps_its_tool_catalog(tmp_path, monkeypatch):
    from hyperclaw import agent
    offered = []

    def respond(request):
        import json
        offered.append([tool["name"] for tool in json.loads(request.content).get("tools", [])])
        return httpx.Response(200, json=wire_message("agent answer"))

    app = await bind_runtime(monkeypatch, tmp_path, respond)
    monkeypatch.setattr(agent, "TOOLS", [{"name": "gmail_inbox", "input_schema": {"type": "object", "properties": {}}}])
    monkeypatch.setattr(agent.HyperClawAgent, "_load_system_prompt", lambda self: "synthetic")
    try:
        response = "".join([text async for text in agent.HyperClawAgent().chat("inspect synthetic inbox")])
        assert response == "agent answer"
        assert offered == [["gmail_inbox"]]
    finally:
        await app.shutdown()


async def test_tui_compatibility_chat_keeps_its_tool_catalog(tmp_path, monkeypatch):
    from hyperclaw.inference import Inference
    from hyperclaw.memory_manager import MemoryManager
    from hyperclaw.model_router import ModelRouter
    from hyperclaw.orchestrator import Orchestrator
    from hyperclaw.providers import Provider, ProviderRegistry
    offered = []

    def respond(request):
        import json
        offered.append([tool["name"] for tool in json.loads(request.content)["tools"]])
        return httpx.Response(200, json=wire_message("tui answer"))

    def create_runtime():
        provider = Provider("test", "anthropic", frozenset({"chat", "tool_use", "streaming"}), models={"default": "test-qwen"})
        registry = ProviderRegistry({"test": provider}, {"primary": ["test"], "tools": ["test"]})
        return Orchestrator(ModelRouter(Inference(registry, httpx.MockTransport(respond))), MemoryManager(root=tmp_path))

    monkeypatch.setattr("hyperclaw.orchestrator.Orchestrator", create_runtime)
    path = Path(__file__).resolve().parents[2] / "hyperclaw" / "tui.py"
    tree = ast.parse(path.read_text())
    method = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "chat")
    namespace = {"__name__": "hyperclaw.tui", "__package__": "hyperclaw",
                 "TOOLS": [{"name": "screenshot", "input_schema": {"type": "object", "properties": {}}}],
                 "execute_tool": lambda name, inputs: "synthetic"}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
    await asyncio.to_thread(namespace["chat"], "inspect synthetic screen")
    assert offered == [["screenshot"]]


@pytest.mark.parametrize("chat_id", [None, 177])
async def test_telegram_clear_waits_for_canonical_reset(chat_id):
    # Execute the real command method without importing the legacy polling
    # module, whose module-level setup opens logs and reads channel settings.
    path = Path(__file__).resolve().parents[2] / "scripts" / "telegram_direct.py"
    tree = ast.parse(path.read_text())
    method = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "handle_command")
    namespace = {"ALLOWED_CHAT_ID": 176}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
    reset_started = asyncio.Event()
    release_reset = asyncio.Event()

    async def reset(chat_id):
        assert chat_id == expected_chat_id
        reset_started.set()
        await release_reset.wait()

    bridge = SimpleNamespace(clear_session=lambda chat_id: None, clear_session_async=reset)
    bot = SimpleNamespace(_load_tui_bridge=lambda: bridge)
    expected_chat_id = 176 if chat_id is None else chat_id
    arguments = {} if chat_id is None else {"chat_id": chat_id}
    command = asyncio.create_task(namespace["handle_command"](bot, "/clear", **arguments))
    await asyncio.sleep(0)
    assert not command.done()
    assert reset_started.is_set()
    release_reset.set()
    assert await command == "Session cleared. Fresh context."
