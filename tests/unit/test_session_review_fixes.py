"""Session adapter behavior against real runtime, transport, and temporary storage."""

import asyncio
import ast
import json
from pathlib import Path

import httpx
import pytest

from hyperclaw import agent, terminal
from tests.unit.test_runtime import runtime, wire_message


@pytest.fixture(autouse=True)
def isolated_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HYPERCLAW_ROOT", str(tmp_path))
    monkeypatch.setattr(agent, "HYPERCLAW_ROOT", tmp_path)
    monkeypatch.setattr(agent, "WORKSPACE_PATH", tmp_path / "workspace")


async def bind_runtime(monkeypatch, root, handler):
    app = await runtime(root, handler)

    async def get_runtime():
        return app

    monkeypatch.setattr("hyperclaw.orchestrator.get_orchestrator", get_runtime)
    return app


def reply_handler(prompts):
    def respond(request):
        data = json.loads(request.content)
        prompts.append(data["messages"])
        if data.get("stream"):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=
                'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"reply"}}\n\n'
                'data: {"type":"message_stop"}\n\n')
        return httpx.Response(200, json=wire_message("reply"))
    return respond


async def collect(chat):
    return "".join([chunk async for chunk in chat])


@pytest.mark.asyncio
async def test_default_agent_instances_keep_their_own_history_across_turns(tmp_path, monkeypatch):
    prompts = []
    app = await bind_runtime(monkeypatch, tmp_path, reply_handler(prompts))
    first, second = agent.HyperClawAgent(), agent.HyperClawAgent()
    try:
        await collect(first.chat("first private message"))
        await collect(second.chat("second private message"))
        await collect(first.chat("first follow-up"))
        assert prompts[1] == [{"role": "user", "content": "second private message"}]
        assert [message["content"] for message in prompts[2]] == [
            "first private message", "reply", "first follow-up"
        ]
        assert [message["content"] for message in second.history] == ["second private message", "reply"]
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_explicit_agent_session_resumes_after_runtime_restart(tmp_path, monkeypatch):
    prompts = []
    app = await bind_runtime(monkeypatch, tmp_path, reply_handler(prompts))
    try:
        await collect(agent.HyperClawAgent(session_id="resume-me").chat("prior message"))
    finally:
        await app.shutdown()
    app = await bind_runtime(monkeypatch, tmp_path, reply_handler(prompts))
    try:
        await collect(agent.HyperClawAgent(session_id="resume-me").chat("next message"))
        assert [message["content"] for message in prompts[-1]] == ["prior message", "reply", "next message"]
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_closing_agent_stream_flushes_history_before_releasing_caller(tmp_path, monkeypatch):
    app = await bind_runtime(monkeypatch, tmp_path, reply_handler([]))
    instance = agent.HyperClawAgent()
    stream = instance.chat("partial turn")
    try:
        assert await anext(stream) == "reply"
        session_id = next(iter(app._session_locks))
        await stream.aclose()
        assert not app._session_locks[session_id].locked()
        assert [message["content"] for message in await app._memory.load_conversation(session_id)] == [
            "partial turn", "reply"
        ]
    finally:
        await stream.aclose()
        await app.shutdown()


async def run_terminal(monkeypatch, commands, session_id="terminal"):
    inputs = iter(commands)
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    await terminal.run(session_id=session_id, tools=False)


@pytest.mark.asyncio
async def test_terminal_imports_legacy_history_once_without_changing_original(tmp_path, monkeypatch):
    legacy = tmp_path / "session_history.json"
    original = '[{"role":"user","content":"old question"},{"role":"assistant","content":"old answer"}]'
    legacy.write_text(original)
    prompts = []
    await bind_runtime(monkeypatch, tmp_path, reply_handler(prompts))
    await run_terminal(monkeypatch, ["new question", "/quit"])
    assert [message["content"] for message in prompts[0]] == ["old question", "old answer", "new question"]
    assert legacy.read_text() == original
    await bind_runtime(monkeypatch, tmp_path, reply_handler(prompts))
    await run_terminal(monkeypatch, ["another question", "/quit"])
    assert [message["content"] for message in prompts[1]] == [
        "old question", "old answer", "new question", "reply", "another question"
    ]
    assert legacy.read_text() == original


@pytest.mark.asyncio
async def test_named_terminal_session_does_not_claim_default_legacy_history(tmp_path, monkeypatch):
    (tmp_path / "session_history.json").write_text('[{"role":"user","content":"old default question"}]')
    prompts = []
    app = await bind_runtime(monkeypatch, tmp_path, reply_handler(prompts))
    await run_terminal(monkeypatch, ["named question", "/quit"], session_id="named")
    assert prompts == [[{"role": "user", "content": "named question"}]]
    assert not await app._memory.conversation_exists("terminal")
    await bind_runtime(monkeypatch, tmp_path, reply_handler(prompts))
    await run_terminal(monkeypatch, ["default question", "/quit"])
    assert [message["content"] for message in prompts[-1]] == ["old default question", "default question"]


@pytest.mark.asyncio
async def test_terminal_reset_blocks_legacy_reimport_after_restart(tmp_path, monkeypatch):
    legacy = tmp_path / "session_history.json"
    original = '[{"role":"user","content":"old question"},{"role":"assistant","content":"old answer"}]'
    legacy.write_text(original)
    app = await bind_runtime(monkeypatch, tmp_path, reply_handler([]))
    await run_terminal(monkeypatch, ["/reset", "/quit"])
    assert await app._memory.conversation_exists("terminal")
    assert await app._memory.load_conversation("terminal") == []
    prompts = []
    await bind_runtime(monkeypatch, tmp_path, reply_handler(prompts))
    await run_terminal(monkeypatch, ["fresh question", "/quit"])
    assert prompts == [[{"role": "user", "content": "fresh question"}]]
    assert legacy.read_text() == original


@pytest.mark.asyncio
async def test_existing_canonical_terminal_skips_even_broken_legacy_file(tmp_path, monkeypatch):
    legacy = tmp_path / "session_history.json"
    legacy.write_text("broken legacy JSON")
    prompts = []
    app = await bind_runtime(monkeypatch, tmp_path, reply_handler(prompts))
    app._memory.add_message("terminal", "user", "canonical question")
    app._memory.add_message("terminal", "assistant", "canonical answer")
    await app._memory.save_conversation("terminal")
    await run_terminal(monkeypatch, ["new question", "/quit"])
    assert [message["content"] for message in prompts[0]] == [
        "canonical question", "canonical answer", "new question"
    ]
    assert legacy.read_text() == "broken legacy JSON"


@pytest.mark.parametrize("original", [
    "broken JSON", '{"messages":[]}', '[null]', '[{"role":"user","content":123}]',
    '[{"role":"user","content":["invalid block"]}]',
    '[{"role":"unknown","content":"bad role"}]',
])
@pytest.mark.asyncio
async def test_malformed_legacy_history_can_be_repaired_and_retried(tmp_path, monkeypatch, original):
    legacy = tmp_path / "session_history.json"
    legacy.write_text(original)
    prompts = []
    app = await bind_runtime(monkeypatch, tmp_path, reply_handler(prompts))
    try:
        with pytest.raises(ValueError):
            await run_terminal(monkeypatch, ["must not overwrite migration", "/quit"])
        assert not await app._memory.conversation_exists("terminal")
        assert prompts == []
        assert legacy.read_text() == original
    finally:
        await app.shutdown()
    legacy.write_text('[{"role":"user","content":"repaired question"}]')
    await bind_runtime(monkeypatch, tmp_path, reply_handler(prompts))
    await run_terminal(monkeypatch, ["new question", "/quit"])
    assert [message["content"] for message in prompts[0]] == ["repaired question", "new question"]


@pytest.mark.asyncio
async def test_terminal_import_preserves_completed_text_and_attachments_without_tool_blocks(tmp_path, monkeypatch):
    attachment = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "c3ludGhldGlj"}}
    original = json.dumps([
        {"role": "assistant", "content": [{"type": "tool_use", "id": "orphan", "name": "bash", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "orphan", "content": "discard"}]},
        {"role": "user", "content": [{"type": "text", "text": "old question"}, attachment]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "done", "name": "bash", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "done", "content": "result"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "completed answer"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "unfinished", "name": "bash", "input": {}}]},
    ])
    legacy = tmp_path / "session_history.json"
    legacy.write_text(original)
    app = await bind_runtime(monkeypatch, tmp_path, reply_handler([]))
    await run_terminal(monkeypatch, ["/quit"])
    history = await app._memory.load_conversation("terminal")
    assert [{"role": message["role"], "content": message["content"]} for message in history] == [
        {"role": "user", "content": [{"type": "text", "text": "old question"}, attachment]},
        {"role": "assistant", "content": "completed answer"},
    ]
    assert legacy.read_text() == original


@pytest.mark.asyncio
async def test_tui_compatibility_chat_imports_the_same_legacy_terminal_session(tmp_path, monkeypatch):
    from hyperclaw.inference import Inference
    from hyperclaw.memory_manager import MemoryManager
    from hyperclaw.model_router import ModelRouter
    from hyperclaw.orchestrator import Orchestrator
    from hyperclaw.providers import Provider, ProviderRegistry

    # Execute the production entrypoint without importing the old module's
    # platform integrations and module-level user configuration readers.
    path = Path(__file__).resolve().parents[2] / "hyperclaw" / "tui.py"
    tree = ast.parse(path.read_text())
    method = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "chat")
    namespace = {"__name__": "hyperclaw.tui", "__package__": "hyperclaw",
                 "TOOLS": [], "execute_tool": lambda name, inputs: "unused"}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
    (tmp_path / "session_history.json").write_text('[{"role":"user","content":"old question"}]')
    prompts = []

    def create_runtime():
        provider = Provider("test", "anthropic", frozenset({"chat", "tool_use", "streaming"}), models={"default": "test-qwen"})
        registry = ProviderRegistry({"test": provider}, {"primary": ["test"], "tools": ["test"]})
        inference = Inference(registry, httpx.MockTransport(reply_handler(prompts)))
        return Orchestrator(ModelRouter(inference), MemoryManager(root=tmp_path))

    monkeypatch.setattr("hyperclaw.orchestrator.Orchestrator", create_runtime)
    await asyncio.to_thread(namespace["chat"], "compatibility question")
    assert [message["content"] for message in prompts[0]] == ["old question", "compatibility question"]
