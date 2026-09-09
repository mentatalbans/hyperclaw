"""Budget advice must not remove the only provider that can serve a request."""
import json

import httpx
import pytest

from hyperclaw.inference import Inference
from hyperclaw.model_router import ModelRouter
from hyperclaw.providers import Provider, ProviderRegistry
from tests.unit.test_runtime import wire_message


def make_router(slots):
    capable = Provider("capable", "anthropic", frozenset({"chat", "images", "tool_use"}),
                       models={"default": "capable-model"})
    fast = Provider("text", "anthropic", frozenset({"chat"}), models={"default": "text-model"})
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=wire_message("synthetic answer"))
    return ModelRouter(Inference(ProviderRegistry({"capable": capable, "text": fast}, slots),
                                 httpx.MockTransport(respond))), requests


@pytest.mark.asyncio
async def test_unpriced_primary_only_provider_keeps_serving_subsequent_turns(monkeypatch):
    monkeypatch.delenv("HYPERCLAW_PROVIDER", raising=False)
    router, requests = make_router({"primary": ["capable"]})
    await router.call("first request")
    answer, _ = await router.call("second request")
    assert answer == "synthetic answer"
    assert [request["model"] for request in requests] == ["capable-model", "capable-model"]
    assert router.get_stats()["cost_complete"] is False


@pytest.mark.asyncio
async def test_budget_routing_keeps_vision_provider_when_fast_cannot_see(monkeypatch):
    monkeypatch.delenv("HYPERCLAW_PROVIDER", raising=False)
    router, requests = make_router({"primary": ["capable"], "vision": ["capable"], "fast": ["text"]})
    await router.call("first unpriced request")
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA=="}}
    # Deliberately omit explicit capabilities: inference derives them from blocks.
    await router.call([image, {"type": "text", "text": "Describe this"}])
    assert requests[-1]["model"] == "capable-model"
    assert router.select_model("describe image", required_capabilities={"chat", "images"}).id == "capable-model"


@pytest.mark.asyncio
async def test_budget_routing_preserves_tool_rounds_and_explicit_model(monkeypatch):
    monkeypatch.delenv("HYPERCLAW_PROVIDER", raising=False)
    router, requests = make_router({"primary": ["capable"], "tools": ["capable"], "fast": ["text"]})
    await router.call("first unpriced request")
    await router.inference.complete([{"role": "user", "content": "tool turn"}], slot="tools",
        tools=[{"name": "lookup", "input_schema": {"type": "object"}}])
    await router.call("explicit model", model_override="capable-model")
    assert [request["model"] for request in requests] == ["capable-model"] * 3


@pytest.mark.asyncio
async def test_compatible_fast_provider_is_still_used(monkeypatch):
    monkeypatch.delenv("HYPERCLAW_PROVIDER", raising=False)
    router, requests = make_router({"primary": ["capable"], "fast": ["text"]})
    await router.call("first unpriced request")
    await router.call("ordinary text request")
    assert [request["model"] for request in requests] == ["capable-model", "text-model"]
