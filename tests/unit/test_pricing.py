"""Exact configured prices and incomplete budgets, without external services."""

import json

import httpx
import pytest
import yaml

from hyperclaw.inference import Inference
from hyperclaw.model_router import ModelRouter
from hyperclaw.providers import ProviderRegistry


@pytest.fixture
def pricing_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HYPERCLAW_ROOT", str(tmp_path))
    for key in (
        "HYPERCLAW_PROVIDER", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL",
        "OPENAI_MODEL", "OLLAMA_BASE_URL", "OLLAMA_MODEL", "HYPERSPEED_BASE_URL",
        "HYPERSPEED_API_KEY", "HYPERSPEED_MODEL", "CHATJIMMY_BASE_URL",
        "CHATJIMMY_API_KEY", "CHATJIMMY_MODEL", "DAILY_BUDGET_USD",
    ):
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def user_registry(root, model_configs):
    directory = root / "config"
    directory.mkdir()
    config = {
        "models": model_configs,
        "providers": {"remote": {
            "kind": "anthropic", "capabilities": ["chat", "streaming", "tool_use"],
            "models": {"default": "paid-main", "fast": "paid-fast"},
        }},
        "slots": {"primary": ["remote"], "tools": ["remote"], "vision": ["remote"],
                  "fast": ["remote:fast"]},
    }
    (directory / "models.yaml").write_text(yaml.safe_dump(config))
    return ProviderRegistry.load()


def priced_router(registry, seen):
    def respond(request):
        model = json.loads(request.content)["model"]
        seen.append(model)
        return httpx.Response(200, json={
            "id": "synthetic", "type": "message", "role": "assistant", "model": model,
            "content": [{"type": "text", "text": "synthetic answer"}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 1000, "output_tokens": 1000},
        })

    return ModelRouter(Inference(registry, httpx.MockTransport(respond)))


@pytest.mark.asyncio
async def test_default_native_model_has_unknown_price_and_incomplete_budget(pricing_root, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic")
    registry = ProviderRegistry.load()
    seen = []
    router = priced_router(registry, seen)
    await router.call("synthetic request")
    stats = router.get_stats()
    default_model = registry.providers["anthropic"].model_for()
    assert stats["total_requests"] == 1
    assert stats["total_tokens"] == 2000
    assert stats["total_cost_usd"] == 0.0
    assert stats["cost_complete"] is False
    assert stats["budget_remaining_usd"] is None
    assert stats["unpriced_models"] == [default_model]
    assert stats["cost_by_model"][default_model] is None
    assert router.models[default_model].pricing_known is False
    assert router.models[default_model].cost_per_1k_input is None
    assert router.models[default_model].cost_per_1k_output is None
    assert router.inference.candidates("primary")[0][1] == registry.providers["anthropic"].model_for("fast")


@pytest.mark.asyncio
async def test_explicit_user_model_rates_drive_cost_and_budget_routing(pricing_root):
    registry = user_registry(pricing_root, {
        "paid-main": {"provider": "remote", "cost_per_1k_input": 0.003, "cost_per_1k_output": 0.011},
        "paid-fast": {"provider": "remote", "cost_per_1k_input": 0.001, "cost_per_1k_output": 0.002},
    })
    seen = []
    router = priced_router(registry, seen)
    router._daily_budget = 0.01
    await router.call("first request")
    await router.call("second request")
    assert seen == ["paid-main", "paid-fast"]
    stats = router.get_stats()
    assert stats["total_cost_usd"] == pytest.approx(0.017)
    assert stats["total_tokens"] == 4000
    assert stats["cost_complete"] is True
    assert stats["unpriced_models"] == []
    assert stats["budget_remaining_usd"] == pytest.approx(-0.007)
    assert router.models["paid-main"].pricing_known is True
    assert router.models["paid-main"].cost_per_1k_input == 0.003
    assert router.models["paid-main"].cost_per_1k_output == 0.011


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_provider", [False, True])
async def test_unknown_request_forces_fast_and_preserves_known_cost_subtotal(
    pricing_root, monkeypatch, explicit_provider
):
    registry = user_registry(pricing_root, {
        "paid-fast": {"provider": "remote", "cost_per_1k_input": 0.001, "cost_per_1k_output": 0.002},
    })
    if explicit_provider:
        monkeypatch.setenv("HYPERCLAW_PROVIDER", "remote")
    seen = []
    router = priced_router(registry, seen)
    await router.call("unpriced request")
    await router.call("priced request")
    assert seen == ["paid-main", "paid-fast"]
    stats = router.get_stats()
    assert stats["total_requests"] == 2
    assert stats["total_tokens"] == 4000
    assert stats["total_cost_usd"] == pytest.approx(0.003)
    assert stats["cost_complete"] is False
    assert stats["unpriced_models"] == ["paid-main"]
    assert stats["unpriced_requests_by_model"] == {"paid-main": 1}
    assert stats["cost_by_model"] == {"paid-main": None, "paid-fast": 0.003}
    assert stats["budget_remaining_usd"] is None
    router.reset_daily_stats()
    assert router.get_stats()["cost_complete"] is True
    assert router.get_stats()["unpriced_models"] == []
    assert router.get_stats()["budget_remaining_usd"] == router._daily_budget


@pytest.mark.asyncio
async def test_ollama_zero_cost_is_known(pricing_root, monkeypatch):
    monkeypatch.setenv("HYPERCLAW_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "synthetic-qwen")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://synthetic.invalid")
    router = priced_router(ProviderRegistry.load(), [])
    await router.call("local request")
    stats = router.get_stats()
    assert stats["total_requests"] == 1
    assert stats["total_cost_usd"] == 0.0
    assert stats["cost_complete"] is True
    assert stats["unpriced_models"] == []
    assert stats["budget_remaining_usd"] == router._daily_budget
    assert router.models["synthetic-qwen"].pricing_known is True
    assert router.models["synthetic-qwen"].cost_per_1k_input == 0.0


def test_unknown_model_cannot_satisfy_an_explicit_cost_limit(pricing_root):
    router = priced_router(user_registry(pricing_root, {}), [])
    with pytest.raises(RuntimeError, match="No configured provider"):
        router.select_model("synthetic request", max_cost=0.0)


@pytest.mark.parametrize("rates", [
    {"provider": "other-provider", "cost_per_1k_input": 0.001, "cost_per_1k_output": 0.002},
    {"provider": "remote", "cost_per_1k_input": 0.001},
    {"provider": "remote", "cost_per_1k_input": -0.001, "cost_per_1k_output": 0.002},
])
def test_mismatched_or_incomplete_rates_are_unknown(pricing_root, rates):
    router = priced_router(user_registry(pricing_root, {"paid-main": rates}), [])
    assert router.models["paid-main"].pricing_known is False
    assert router.models["paid-main"].cost_per_1k_input is None
