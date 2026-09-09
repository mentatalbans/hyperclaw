"""Public API contracts through a real CLI/uvicorn child and loopback provider.

No in-process application monkeypatches: only the external model's HTTP service
is scripted. Each scenario owns its ports, environment, storage and processes.
"""

import threading

import pytest

from tests.support.http_process import MODEL, HyperClawProcess, ProviderStub, Reply, sse_events


pytestmark = pytest.mark.integration


@pytest.fixture
def runtime(tmp_path, request):
    provider = ProviderStub()
    app = HyperClawProcess(tmp_path / "runtime", provider.url)
    try:
        app.start()
        yield app, provider
    finally:
        # Captured output appears with failures, including assertion failures after
        # a successful launch. Release streaming gates before waiting for shutdown.
        for gate in provider.gates:
            gate.set()
        try:
            app.stop()
        finally:
            provider.close()
            print(f"\nHyperClaw child logs ({request.node.nodeid}):\n{app.diagnostics()}")
            print(f"Provider errors: {provider.errors}")
        assert not provider.errors


def chat(app, message, session, **kwargs):
    response = app.client.post("/chat", json={"message": message, "session_id": session, **kwargs})
    assert response.status_code == 200, response.text
    return response.json()


def test_cli_profile_drives_health_model_metadata_and_real_chat(runtime):
    """Catch startup/profile divergence between CLI, API metadata and inference."""
    app, provider = runtime
    health = app.client.get("/health").json()
    assert health["status"] == "healthy"
    assert health["components"] == {"api": True, "orchestrator": True, "database": False, "memory": True}
    config = app.client.get("/api/config").json()
    assert config["hyperclaw_root"] == str(app.root)
    assert (config["provider"], config["model"]) == ("ollama", MODEL)
    assert config["database_configured"] is False
    assert config["integrations_configured"] == []
    models = app.client.get("/api/models").json()["models"]
    assert [(model["id"], model["provider"]) for model in models] == [(MODEL, "ollama")]
    assert {"chat", "streaming"} <= set(models[0]["capabilities"])

    provider.enqueue(Reply(text="The API reached the selected provider."))
    result = chat(app, "Hello from a child process", "metadata", force_model=MODEL)
    assert result["response"] == "The API reached the selected provider."
    assert result["session_id"] == "metadata"
    assert result["timestamp"]
    sent = provider.take_request()
    assert sent["model"] == MODEL
    assert sent["messages"] == [{"role": "user", "content": "Hello from a child process"}]


@pytest.mark.parametrize("path,extra", [("/chat/stream", {}), ("/chat", {"stream": True})])
def test_sse_arrives_before_provider_finishes_and_persists_only_answer(runtime, path, extra):
    """Catch buffering, missing DONE, thinking leakage and lost streamed history."""
    app, provider = runtime
    gate = threading.Event()
    provider.enqueue(Reply(thinking="Checking the answer.", gate=gate))
    with app.client.stream("POST", path, json={"message": "Stream this", "session_id": "stream", **extra}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = sse_events(response)
        assert next(events) == ("thinking", "Checking the answer.")
        assert next(events) == ("message", "Hello ")
        assert not gate.is_set()
        gate.set()
        assert list(events) == [("message", "world."), ("message", "[DONE]")]
    assert provider.take_request()["stream"] is True

    app.restart()
    provider.enqueue(Reply(text="Follow-up answer"))
    assert chat(app, "Continue", "stream")["response"] == "Follow-up answer"
    assert provider.take_request()["messages"] == [
        {"role": "user", "content": "Stream this"},
        {"role": "assistant", "content": "Hello world."},
        {"role": "user", "content": "Continue"},
    ]


def test_conversations_memory_and_isolated_reset_survive_process_restarts(runtime):
    """Catch dropped disk writes, colliding session filenames and non-durable reset."""
    app, provider = runtime
    first, second = "family/a", "family_a"
    provider.enqueue(Reply(text="First answer"), Reply(text="Second answer"))
    chat(app, "First private marker", first)
    provider.take_request()
    chat(app, "Second private marker", second)
    assert provider.take_request()["messages"] == [{"role": "user", "content": "Second private marker"}]
    stored = app.client.post("/api/memory/remember", json={"content": "Cobalt observatory note", "domain": "testing"})
    assert stored.status_code == 200, stored.text
    memory_id = stored.json()["memory_id"]
    assert memory_id

    app.restart()
    provider.enqueue(Reply(text="First continuation"))
    chat(app, "Continue first", first)
    assert provider.take_request()["messages"] == [
        {"role": "user", "content": "First private marker"},
        {"role": "assistant", "content": "First answer"},
        {"role": "user", "content": "Continue first"},
    ]
    recalled = app.client.post("/api/memory/recall", json={"query": "Cobalt observatory"})
    assert recalled.status_code == 200, recalled.text
    assert any(memory["id"] == memory_id and memory["content"] == "Cobalt observatory note"
               and memory["domain"] == "testing" for memory in recalled.json()["memories"])
    reset = app.client.post("/reset", params={"session_id": first})
    assert reset.status_code == 200, reset.text
    assert reset.json()["session_id"] == first

    app.restart()
    provider.enqueue(Reply(text="Fresh first answer"), Reply(text="Second continuation"))
    chat(app, "First after reset", first)
    assert provider.take_request()["messages"] == [{"role": "user", "content": "First after reset"}]
    chat(app, "Continue second", second)
    assert provider.take_request()["messages"] == [
        {"role": "user", "content": "Second private marker"},
        {"role": "assistant", "content": "Second answer"},
        {"role": "user", "content": "Continue second"},
    ]
    recalled = app.client.post("/api/memory/recall", json={"query": "Cobalt observatory"}).json()
    assert memory_id in {memory["id"] for memory in recalled["memories"]}


@pytest.mark.parametrize("path,extra", [("/chat", {}), ("/chat/stream", {}), ("/chat", {"stream": True})])
def test_provider_failure_is_http_error_and_same_session_can_recover(runtime, path, extra):
    """Catch false-success HTTP statuses and session locks retained after errors."""
    app, provider = runtime
    provider.enqueue(Reply(status=503), Reply(text="Recovered successfully"))
    failed = app.client.post(path, json={"message": "During outage", "session_id": "recovery", **extra})
    assert failed.status_code == 502, failed.text
    assert failed.json() == {"detail": "Model request failed"}
    provider.take_request()
    assert chat(app, "Try again", "recovery")["response"] == "Recovered successfully"
    history = provider.take_request()["messages"]
    assert history[-1] == {"role": "user", "content": "Try again"}
    assert not any(item["role"] == "assistant" for item in history)
    assert app.client.get("/health").json()["status"] == "healthy"


def test_interrupted_sse_reports_error_without_done_and_releases_session(runtime):
    """Catch premature provider EOF falsely reported as a complete answer."""
    app, provider = runtime
    provider.enqueue(Reply(chunks=("Partial answer",), truncate=True), Reply(text="Next turn works"))
    with app.client.stream("POST", "/chat/stream", json={"message": "Interrupted turn", "session_id": "interrupted"}) as response:
        assert response.status_code == 200
        events = list(sse_events(response))
    assert events[0] == ("message", "Partial answer")
    assert events[-1] == ("error", {"error": "Model stream interrupted"})
    assert ("message", "[DONE]") not in events
    provider.take_request()
    assert chat(app, "Next turn", "interrupted")["response"] == "Next turn works"
    assert provider.take_request()["messages"][-1] == {"role": "user", "content": "Next turn"}
