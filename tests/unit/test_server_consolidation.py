"""HTTP compatibility, startup ownership, and channel authorization contracts."""

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("HYPERCLAW_ROOT", str(tmp_path))
    monkeypatch.setenv("SECRETS_MOUNT", str(tmp_path / "secrets"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("HYPERCLAW_ENABLE_TELEGRAM", "false")
    monkeypatch.setenv("HYPERCLAW_ENABLE_SCHEDULER", "false")
    module = importlib.import_module("hyperclaw.server")
    monkeypatch.setattr(module, "_orchestrator", None)
    return module


@pytest.fixture
def runtime(server, monkeypatch):
    from hyperclaw.agent_coordinator import AgentCoordinator

    coordinator = AgentCoordinator(model_router=SimpleNamespace(get_stats=lambda: {}))

    async def chat(**kwargs):
        return json.dumps(kwargs)

    async def dispatch_task(**kwargs):
        return await coordinator.submit_task(**kwargs)

    async def stream_events(**kwargs):
        yield "thinking", "Checking"
        yield "text", "Hello\nworld"

    orchestrator = SimpleNamespace(
        chat=chat,
        stream_events=stream_events,
        reset_session=AsyncMock(),
        dispatch_task=dispatch_task,
        _coordinator=coordinator,
        _initialized=True,
        _memory=object(),
        shutdown=AsyncMock(),
        send_telegram=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(server, "_orchestrator", orchestrator)
    return orchestrator


def test_root_and_package_serve_the_same_app(server):
    assert importlib.import_module("server").app is server.app


def test_chat_preserves_session_attachments_and_tool_choice(server, runtime):
    response = TestClient(server.app).post("/chat", json={
        "message": "Describe this",
        "session_id": "separate-session",
        "attachments": [{"type": "image", "data": "aW1hZ2U="}],
        "tools": False,
        "force_model": "ollama/qwen3.8:27b-mlx",
    })
    assert response.status_code == 200
    assert json.loads(response.json()["response"]) == {
        "message": "Describe this", "session_id": "separate-session",
        "channel": "api", "stream": False, "tools": False,
        "attachments": [{"type": "image", "data": "aW1hZ2U="}],
        "force_model": "ollama/qwen3.8:27b-mlx",
    }


def test_chat_provider_failure_returns_http_error(server, runtime):
    runtime.chat = AsyncMock(side_effect=RuntimeError("provider unavailable"))
    response = TestClient(server.app, raise_server_exceptions=False).post(
        "/chat", json={"message": "hello"})
    assert response.status_code == 502
    assert "response" not in response.json()


def test_dashboard_null_session_creates_an_isolated_session(server, runtime):
    client = TestClient(server.app)
    first = client.post("/chat", json={"message": "hello", "session_id": None})
    second = client.post("/chat", json={"message": "hello", "session_id": None})
    assert first.status_code == second.status_code == 200
    assert first.json()["session_id"] != second.json()["session_id"]
    assert json.loads(first.json()["response"])["session_id"] == first.json()["session_id"]


def test_stream_preserves_multiline_text_and_thinking(server, runtime):
    response = TestClient(server.app).post("/chat/stream", json={"message": "hello"})
    assert response.status_code == 200
    assert 'event: thinking\ndata: "Checking"\n\n' in response.text
    assert 'data: "Hello\\nworld"\n\n' in response.text
    assert response.text.endswith("data: [DONE]\n\n")


def test_stream_failure_before_first_event_returns_http_error(server, runtime):
    async def broken(**kwargs):
        raise RuntimeError("provider unavailable")
        yield

    runtime.stream_events = broken
    response = TestClient(server.app, raise_server_exceptions=False).post(
        "/chat/stream", json={"message": "hello"})
    assert response.status_code == 502


def test_reset_uses_durable_public_operation(server, runtime):
    response = TestClient(server.app).post("/reset?session_id=isolated")
    assert response.status_code == 200
    assert response.json()["session_id"] == "isolated"
    runtime.reset_session.assert_awaited_once_with("isolated")


@pytest.mark.parametrize("path", ["/reset", "/chat"])
def test_uninitialized_requests_fail_instead_of_reporting_success(server, path):
    response = TestClient(server.app).post(path, json={"message": "hello"})
    assert response.status_code == 503


@pytest.mark.parametrize("agent_id", ["FORGE", "code_specialist", "FORGE — Code Specialist"])
def test_swarm_alias_uses_canonical_coordinator(server, runtime, agent_id):
    client = TestClient(server.app)
    response = client.post("/api/swarm/dispatch", json={"task": "Review code", "agent_id": agent_id})
    assert response.status_code == 200
    task = response.json()
    assert task["assigned_to"] == "code_specialist"
    assert task["task_id"] in runtime._coordinator.tasks
    canonical = client.get("/api/tasks/" + task["task_id"]).json()
    legacy = client.get("/api/swarm/task/" + task["task_id"]).json()
    assert canonical == legacy


def test_unknown_agent_does_not_silently_route_elsewhere(server, runtime):
    response = TestClient(server.app).post("/api/swarm/dispatch", json={
        "task": "Review code", "agent_id": "missing-agent"})
    assert response.status_code == 404
    assert not runtime._coordinator.tasks


@pytest.mark.parametrize("secret,header,allowed,want", [
    (None, None, "123", 503),
    ("synthetic-secret", None, "123", 403),
    ("synthetic-secret", "incorrect", "123", 403),
    ("synthetic-secret", "synthetic-secret", "", 403),
    ("synthetic-secret", "synthetic-secret", "456", 403),
    ("synthetic-secret", "synthetic-secret", "123", 200),
])
def test_webhook_authenticates_and_checks_allowlist_before_processing(
    server, runtime, monkeypatch, secret, header, allowed, want,
):
    if secret:
        monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", allowed)
    runtime.chat = AsyncMock(return_value="synthetic reply")
    headers = {"X-Telegram-Bot-Api-Secret-Token": header} if header else {}
    response = TestClient(server.app).post("/webhook/telegram", headers=headers, json={
        "message": {"chat": {"id": 123}, "text": "hello"},
    })
    assert response.status_code == want
    assert runtime.chat.await_count == (1 if want == 200 else 0)
    assert runtime.send_telegram.await_count == (1 if want == 200 else 0)


def test_webhook_authentication_happens_before_json_parsing(server, runtime, monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "synthetic-secret")
    response = TestClient(server.app).post("/webhook/telegram", content="invalid json")
    assert response.status_code == 403


def test_lifespan_starts_without_channels_or_database(server, runtime, monkeypatch):
    factory = AsyncMock(return_value=runtime)
    monkeypatch.setattr(server, "get_orchestrator", factory)
    with TestClient(server.app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["components"]["orchestrator"] is True
    factory.assert_awaited_once_with(None)
    runtime.shutdown.assert_awaited_once()
    assert server._orchestrator is None


def test_dashboard_auxiliary_routes_are_retained(server):
    paths = TestClient(server.app).get("/openapi.json").json()["paths"]
    assert {
        "/api/trading/status", "/api/trading/signal", "/api/trading/close",
        "/api/trading/halt", "/api/trading/resume", "/api/memory/stats",
        "/api/memory/reload", "/api/memory/consolidate", "/api/prometheus/status",
        "/api/markets", "/api/intel", "/api/summits", "/api/polymarket",
        "/api/tts", "/api/tts/voices", "/api/swarm/all-hands", "/api/swarm/status",
        "/api/swarm/agent/{agent_id}",
    } <= paths.keys()


def test_entrypoint_passes_mounted_secrets_without_shell_evaluation(tmp_path):
    mount = tmp_path / "secrets"
    mount.mkdir()
    marker = tmp_path / "should-not-exist"
    value = f'quote \' newline\n$(touch {marker}) `touch {marker}`'
    (mount / "credentials").write_text(json.dumps({"SYNTHETIC_VALUE": value, "EXISTING_VALUE": "mount"}))
    env = dict(os.environ, SECRETS_MOUNT=str(mount), EXISTING_VALUE="environment")
    env.pop("SYNTHETIC_VALUE", None)
    result = subprocess.run([
        "sh", str(Path(__file__).resolve().parents[2] / "entrypoint.sh"),
        sys.executable, "-c",
        "import json, os; print(json.dumps([os.environ.get('SYNTHETIC_VALUE'), os.environ['EXISTING_VALUE']]))",
    ], env=env, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [value, "environment"]
    assert not marker.exists()


def test_http_sessions_memory_and_reset_survive_new_runtimes(server, monkeypatch, tmp_path):
    """The HTTP path must retain the real memory/runtime persistence boundary."""
    import httpx
    from hyperclaw.inference import Inference
    from hyperclaw.memory_manager import MemoryManager
    from hyperclaw.model_router import ModelRouter
    from hyperclaw.orchestrator import Orchestrator
    from hyperclaw.providers import Provider, ProviderRegistry

    monkeypatch.delenv("HYPERCLAW_PROVIDER", raising=False)
    monkeypatch.setenv("HYPERCLAW_ENABLE_TOOLS", "false")
    requests = []
    runtimes = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "msg_test", "type": "message", "role": "assistant", "model": "test-qwen",
            "content": [{"type": "text", "text": "Synthetic reply"}], "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 3},
        })

    async def factory(db_pool=None):
        provider = Provider("test", "anthropic", frozenset({"chat"}), models={"default": "test-qwen"})
        registry = ProviderRegistry({"test": provider}, {"primary": ["test"], "fast": ["test"]})
        router = ModelRouter(Inference(registry, httpx.MockTransport(respond)))
        runtime = Orchestrator(model_router=router, memory=MemoryManager(root=tmp_path))
        await runtime.initialize(db_pool)
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr(server, "get_orchestrator", factory)
    with TestClient(server.app) as client:
        models = client.get("/api/models").json()["models"]
        assert [model["id"] for model in models] == ["test-qwen"]
        assert client.get("/api/config").json()["model"] == "test-qwen"
        assert client.post("/chat", json={"message": "session A marker", "session_id": "a"}).status_code == 200
        assert client.post("/chat", json={"message": "session B marker", "session_id": "b"}).status_code == 200
        assert requests[-1]["messages"] == [{"role": "user", "content": "session B marker"}]
        stored = client.post("/api/memory/remember", json={"content": "synthetic cobalt note", "domain": "testing"})
        assert stored.status_code == 200
        memory_id = stored.json()["memory_id"]
        recalled = client.post("/api/memory/recall", json={"query": "cobalt"}).json()["memories"]
        assert any(item["id"] == memory_id for item in recalled)

    with TestClient(server.app) as client:
        assert client.post("/chat", json={"message": "continue A", "session_id": "a"}).status_code == 200
        assert [message["content"] for message in requests[-1]["messages"]] == [
            "session A marker", "Synthetic reply", "continue A",
        ]
        recalled = client.post("/api/memory/recall", json={"query": "cobalt"}).json()["memories"]
        assert any(item["id"] == memory_id and item["content"] == "synthetic cobalt note" for item in recalled)
        assert client.post("/reset?session_id=a").status_code == 200

    with TestClient(server.app) as client:
        assert client.post("/chat", json={"message": "fresh A", "session_id": "a"}).status_code == 200
        assert requests[-1]["messages"] == [{"role": "user", "content": "fresh A"}]
    assert all(not runtime._coordinator._running for runtime in runtimes)


def test_enabled_background_services_start_and_stop_once(server, runtime, monkeypatch):
    events = []

    def sync_event(name):
        def emit(*args, **kwargs):
            events.append(name)
        return emit

    def async_event(name):
        async def emit(*args, **kwargs):
            events.append(name)
        return emit

    telegram = SimpleNamespace(
        initialize=async_event("initialize"), start=async_event("start"),
        stop=async_event("stop"), shutdown=async_event("shutdown"),
        updater=SimpleNamespace(start_polling=async_event("poll"), stop=async_event("stop_poll")),
    )
    scheduler = SimpleNamespace(start=sync_event("schedule"), stop=sync_event("stop_schedule"))
    monkeypatch.setitem(sys.modules, "hyperclaw.telegram_bot", SimpleNamespace(
        get_telegram_bot=lambda: SimpleNamespace(build=lambda token: telegram),
        close_legacy_history=async_event("close_history")))
    monkeypatch.setitem(sys.modules, "hyperclaw.scheduler", SimpleNamespace(get_scheduler=lambda send: scheduler))
    monkeypatch.setattr(server, "get_orchestrator", AsyncMock(return_value=runtime))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "synthetic-token")
    monkeypatch.setenv("HYPERCLAW_ENABLE_TELEGRAM", "true")
    monkeypatch.setenv("HYPERCLAW_ENABLE_SCHEDULER", "true")
    with TestClient(server.app):
        assert events == ["initialize", "start", "poll", "schedule"]
    assert events == ["initialize", "start", "poll", "schedule", "stop_schedule", "stop_poll", "stop", "shutdown", "close_history"]
    runtime.shutdown.assert_awaited_once()


def test_telegram_enabled_without_token_does_not_block_http(server, runtime, monkeypatch):
    monkeypatch.setenv("HYPERCLAW_ENABLE_TELEGRAM", "true")
    monkeypatch.setattr(server, "get_orchestrator", AsyncMock(return_value=runtime))
    with TestClient(server.app) as client:
        assert client.get("/health").status_code == 200


@pytest.mark.asyncio
async def test_database_can_be_explicitly_disabled_with_legacy_url(server, monkeypatch):
    import asyncpg
    monkeypatch.setenv("DATABASE_URL", "postgresql://synthetic.invalid/test")
    monkeypatch.setenv("HYPERCLAW_ENABLE_DATABASE", "false")
    create = AsyncMock(return_value=object())
    monkeypatch.setattr(asyncpg, "create_pool", create)
    assert await server.create_db_pool() is None
    create.assert_not_awaited()
