"""Exercise routing and SDK wire requests without external services."""
import json

import httpx
import pytest

from hyperclaw.providers import Provider, ProviderRegistry


def message(text="ok", model="local-qwen"):
    return {"id": "msg_test", "type": "message", "role": "assistant",
            "model": model, "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 2}}


def providers(monkeypatch):
    monkeypatch.setenv("TEST_LOCAL_URL", "http://local.test")
    monkeypatch.setenv("TEST_CLOUD_KEY", "test-key")
    monkeypatch.delenv("HYPERCLAW_PROVIDER", raising=False)
    local = Provider("local", "anthropic", frozenset({"chat", "streaming", "tool_use", "images"}),
                     base_url_env="TEST_LOCAL_URL", models={"default": "local-qwen"})
    cloud = Provider("cloud", "anthropic", local.capabilities,
                     api_key_env="TEST_CLOUD_KEY", models={"default": "cloud-model"})
    return ProviderRegistry({"local": local, "cloud": cloud},
                            {"primary": ["local", "cloud"], "tools": ["local", "cloud"],
                             "fast": ["local", "cloud"]})


@pytest.mark.asyncio
async def test_local_request_uses_configured_host_and_model(monkeypatch):
    from hyperclaw.inference import Inference
    reg = providers(monkeypatch)
    seen = []

    def respond(request):
        seen.append((request.url.host, json.loads(request.content)))
        return httpx.Response(200, json=message("local reply"))

    reply, meta = await Inference(reg, httpx.MockTransport(respond)).complete(
        [{"role": "user", "content": "hello"}], system="Be concise.")
    assert reply.content[0].text == "local reply"
    assert seen[0][0] == "local.test"
    assert seen[0][1]["model"] == "local-qwen"
    assert meta["provider"] == "local"
    assert meta["input_tokens"] == 10


@pytest.mark.asyncio
async def test_fallback_visits_each_provider_once(monkeypatch):
    from hyperclaw.inference import Inference
    seen = []

    def respond(request):
        seen.append(json.loads(request.content)["model"])
        if request.url.host == "local.test":
            return httpx.Response(503, json={"error": {"type": "overloaded_error", "message": "offline"}})
        return httpx.Response(200, json=message("fallback reply", "cloud-model"))

    reply, meta = await Inference(providers(monkeypatch), httpx.MockTransport(respond)).complete(
        [{"role": "user", "content": "hello"}])
    assert reply.content[0].text == "fallback reply"
    assert seen == ["local-qwen", "cloud-model"]


@pytest.mark.asyncio
async def test_explicit_local_selection_never_falls_back_to_cloud(monkeypatch):
    from hyperclaw.inference import Inference
    reg = providers(monkeypatch)
    monkeypatch.setenv("HYPERCLAW_PROVIDER", "local")
    seen = []

    def respond(request):
        seen.append(request.url.host)
        return httpx.Response(503, json={"error": {"type": "overloaded_error", "message": "offline"}})

    with pytest.raises(Exception, match="offline"):
        await Inference(reg, httpx.MockTransport(respond)).complete(
            [{"role": "user", "content": "private local content"}])
    assert seen == ["local.test"]


@pytest.mark.asyncio
async def test_explicit_model_id_reaches_the_wire(monkeypatch):
    from hyperclaw.inference import Inference
    seen = []
    def respond(request):
        seen.append(json.loads(request.content)["model"])
        return httpx.Response(200, json=message())
    await Inference(providers(monkeypatch), httpx.MockTransport(respond)).complete(
        [{"role": "user", "content": "hello"}], model_override="cloud-model")
    assert seen == ["cloud-model"]


@pytest.mark.asyncio
async def test_tool_blocks_and_results_keep_their_ids(monkeypatch):
    from hyperclaw.inference import Inference
    payloads = []
    def respond(request):
        payloads.append(json.loads(request.content))
        data = message()
        data.update(content=[{"type": "tool_use", "id": "call_one", "name": "read_file", "input": {"path": "/tmp/example"}}], stop_reason="tool_use")
        return httpx.Response(200, json=data)
    tool = {"name": "read_file", "description": "Read a file", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}}
    reply, _ = await Inference(providers(monkeypatch), httpx.MockTransport(respond)).complete(
        [{"role": "user", "content": "read example"}], tools=[tool], slot="tools")
    assert reply.content[0].id == "call_one"
    assert reply.content[0].input == {"path": "/tmp/example"}
    assert payloads[0]["tools"] == [tool]


def test_ollama_selected_without_cloud_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("HYPERCLAW_ROOT", str(tmp_path))
    monkeypatch.setenv("HYPERCLAW_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3.8:27b-mlx")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    reg = ProviderRegistry.load()
    for slot in ("primary", "tools", "vision", "fast"):
        candidates = reg.resolve(slot, {"chat"})
        assert [(p.name, model) for p, model in candidates] == [("ollama", "qwen3.8:27b-mlx")]
    assert reg.resolve("images", {"image_generation"}) == []


@pytest.mark.asyncio
async def test_stream_interruption_does_not_reanswer(monkeypatch):
    from hyperclaw.inference import Inference
    seen = []
    def respond(request):
        seen.append(request.url.host)
        # A premature EOF after output must end this answer, never start a new one.
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
            text='data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"partial"}}\n\n')
    output = []
    with pytest.raises(RuntimeError, match="completion marker"):
        async for item in Inference(providers(monkeypatch), httpx.MockTransport(respond)).stream_events(
                [{"role": "user", "content": "hello"}]):
            output.append(item)
    assert output[0] == ("text", "partial")
    assert "interrupted" in output[1][1]
    assert seen == ["local.test"]


@pytest.mark.asyncio
async def test_router_uses_the_same_registry_for_task_calls(monkeypatch):
    from hyperclaw.inference import Inference
    from hyperclaw.model_router import ModelRouter
    seen = []
    def respond(request):
        seen.append(json.loads(request.content)["model"])
        return httpx.Response(200, json=message("task answer"))
    router = ModelRouter(inference=Inference(providers(monkeypatch), httpx.MockTransport(respond)))
    text, meta = await router.call("Do this task", model_override="local-qwen")
    assert text == "task answer"
    assert seen == ["local-qwen"]
    assert router.get_stats()["total_requests"] == 1


@pytest.mark.asyncio
async def test_attachments_in_history_and_tool_results_require_capabilities(monkeypatch):
    from hyperclaw.inference import Inference
    inference = Inference(providers(monkeypatch), httpx.MockTransport(lambda _: pytest.fail("unsupported document sent")))
    document = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "AA=="}}
    for content in ([document], [{"type": "tool_result", "tool_use_id": "one", "content": [document]}]):
        with pytest.raises(RuntimeError, match="documents"):
            await inference.complete([{"role": "user", "content": content}, {"role": "user", "content": "Explain"}],
                slot="tools", tools=[{"name": "lookup", "input_schema": {}}])


@pytest.mark.asyncio
async def test_stream_and_direct_tool_requests_are_accounted(monkeypatch):
    from hyperclaw.inference import Inference
    from hyperclaw.model_router import ModelRouter
    def respond(request):
        if json.loads(request.content).get("stream"):
            events = [
                {"type": "message_start", "message": {"usage": {"input_tokens": 20, "output_tokens": 0}}},
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ok"}},
                {"type": "message_delta", "usage": {"output_tokens": 4}},
                {"type": "message_stop"}]
            return httpx.Response(200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events))
        return httpx.Response(200, json=message())
    router = ModelRouter(Inference(providers(monkeypatch), httpx.MockTransport(respond)))
    await router.inference.complete([{"role": "user", "content": "tool turn"}], slot="tools", tools=[{"name": "lookup", "input_schema": {}}])
    assert [item async for item in router.inference.stream_events([{"role": "user", "content": "stream"}])] == [("text", "ok")]
    assert router.stats.total_requests == 2
    assert router.stats.total_input_tokens == 30
    assert router.stats.total_output_tokens == 6


@pytest.mark.asyncio
async def test_budget_applies_to_explicit_slots_and_identity_follows_override(monkeypatch):
    from hyperclaw.inference import Inference
    from hyperclaw.model_router import ModelRouter
    reg = providers(monkeypatch)
    reg.slots["fast"] = ["cloud"]
    seen = []
    def respond(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=message())
    router = ModelRouter(Inference(reg, httpx.MockTransport(respond)))
    router.stats.total_cost = router._daily_budget
    await router.inference.complete([{"role": "user", "content": "hi"}], slot="primary")
    assert seen[-1]["model"] == "cloud-model"
    assert "cloud-model" in seen[-1]["system"]
