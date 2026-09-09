"""
HyperClaw Model Router
Intelligent routing to optimize cost, speed, and quality.
Routes simple tasks to ChatJimmy, complex tasks to Claude.
"""

import logging
import math
import os
from hyperclaw.api_utils import extract_text
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Optional

import httpx

logger = logging.getLogger("hyperclaw.model_router")


class ModelTier(Enum):
    """Model tiers by capability and cost."""
    FAST = "fast"       # ChatJimmy, Haiku - simple tasks, low cost
    STANDARD = "standard"  # Sonnet - balanced
    PREMIUM = "premium"    # Opus - complex reasoning


@dataclass
class ModelConfig:
    """Configuration for a model."""
    id: str
    name: str
    tier: ModelTier
    provider: str  # anthropic, chatjimmy, openai
    cost_per_1k_input: Optional[float]
    cost_per_1k_output: Optional[float]
    max_tokens: int
    latency_ms: int  # typical latency
    capabilities: list[str] = field(default_factory=list)
    base_url: Optional[str] = None
    api_key_env: str = ""
    pricing_known: bool = True


# Model ids come from models.yaml (providers.anthropic.models + the
# chatjimmy provider); the static strings below are only last-resort
# fallbacks when the registry is unavailable.
def _yaml_anthropic_models() -> dict:
    try:
        from hyperclaw.providers import registry
        prov = registry().providers.get("anthropic")
        return dict(prov.models) if prov else {}
    except Exception:
        return {}


def _yaml_chatjimmy() -> tuple:
    """(model_id, base_url) for chatjimmy from the registry, or ("", "")."""
    try:
        from hyperclaw.providers import registry
        prov = registry().providers.get("chatjimmy")
        if prov:
            return prov.model_for(), prov.base_url
    except Exception:
        pass
    return "", ""


_ANTH = _yaml_anthropic_models()
_CJ_MODEL, _CJ_URL = _yaml_chatjimmy()

# Model registry
MODELS = {
    # ChatJimmy - Fast & cheap for simple tasks
    "chatjimmy": ModelConfig(
        id=_CJ_MODEL or "chatjimmy",
        name="ChatJimmy (Llama 3.1 8B)",
        tier=ModelTier.FAST,
        provider="chatjimmy",
        cost_per_1k_input=0.00001,
        cost_per_1k_output=0.00001,
        max_tokens=2048,
        latency_ms=50,
        capabilities=["chat", "simple_qa", "classification", "extraction"],
        base_url=_CJ_URL or None,
        api_key_env="CHATJIMMY_API_KEY",
    ),

    # Claude Haiku - Fast Claude for moderate tasks
    "claude-haiku": ModelConfig(
        id=_ANTH.get("fast", "claude-haiku-4-5"),
        name="Claude Haiku 4.5",
        tier=ModelTier.FAST,
        provider="anthropic",
        cost_per_1k_input=0.001,
        cost_per_1k_output=0.005,
        max_tokens=4096,
        latency_ms=500,
        capabilities=["chat", "analysis", "coding", "writing"],
        api_key_env="ANTHROPIC_API_KEY",
    ),

    # Claude Sonnet - Balanced for most tasks
    "claude-sonnet": ModelConfig(
        id=_ANTH.get("standard", "claude-sonnet-5"),
        name="Claude Sonnet 5",
        tier=ModelTier.STANDARD,
        provider="anthropic",
        cost_per_1k_input=0.002,
        cost_per_1k_output=0.010,
        max_tokens=8192,
        latency_ms=1500,
        capabilities=["chat", "analysis", "coding", "writing", "reasoning", "planning"],
        api_key_env="ANTHROPIC_API_KEY",
    ),

    # Claude Opus - Premium for complex tasks
    "claude-opus": ModelConfig(
        id=_ANTH.get("premium", "claude-opus-5"),
        name="Claude Opus 5",
        tier=ModelTier.PREMIUM,
        provider="anthropic",
        cost_per_1k_input=0.005,
        cost_per_1k_output=0.025,
        max_tokens=8192,
        latency_ms=3000,
        capabilities=["chat", "analysis", "coding", "writing", "reasoning", "planning", "complex_reasoning", "research"],
        api_key_env="ANTHROPIC_API_KEY",
    ),
    "claude-fable": ModelConfig(
        id=_ANTH.get("flagship", "claude-fable-5"),
        name="Claude Fable 5",
        tier=ModelTier.PREMIUM,
        provider="anthropic",
        cost_per_1k_input=0.010,
        cost_per_1k_output=0.050,
        max_tokens=8192,
        latency_ms=3000,
        capabilities=["chat", "analysis", "coding", "writing", "reasoning", "planning", "complex_reasoning", "research"],
        api_key_env="ANTHROPIC_API_KEY",
    ),

    # OpenRouter - Meta router for any model
    "openrouter": ModelConfig(
        id="openrouter/auto",
        name="OpenRouter Auto",
        tier=ModelTier.STANDARD,
        provider="openrouter",
        cost_per_1k_input=0.002,
        cost_per_1k_output=0.006,
        max_tokens=4096,
        latency_ms=1500,
        capabilities=["chat", "analysis", "coding", "writing"],
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
    ),
}

# Task complexity patterns
SIMPLE_TASK_PATTERNS = [
    r"^(hi|hello|hey|thanks|thank you|ok|okay|yes|no|sure)[\s\.\!\?]*$",
    r"^what('s| is) (the )?(time|date|day)",
    r"^(list|show|get) (my )?(tasks|emails|events|calendar)",
    r"^(send|forward) (a )?quick (message|note|reply)",
    r"^(check|look up|find) (the )?(weather|status|price)",
    r"^remind me",
    r"^set (a )?(timer|alarm|reminder)",
    r"^(open|launch|start) ",
    r"^(yes|no|confirm|cancel|stop|done|finished)",
]

COMPLEX_TASK_PATTERNS = [
    r"(analyze|analysis|evaluate|assess|review)",
    r"(strategy|strategic|plan|planning)",
    r"(research|investigate|deep dive)",
    r"(write|draft|compose) (a )?(report|proposal|document|article)",
    r"(code|implement|build|develop|architect)",
    r"(compare|contrast|difference|pros and cons)",
    r"(explain|describe|elaborate) (in detail|thoroughly)",
    r"(summarize|synthesize) (multiple|several|all)",
    r"(debug|troubleshoot|diagnose)",
    r"(optimize|improve|refactor)",
]


@dataclass
class UsageStats:
    """Track usage and costs."""
    total_requests: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost: float = 0.0
    requests_by_model: dict = field(default_factory=dict)
    cost_by_model: dict = field(default_factory=dict)
    unpriced_requests_by_model: dict = field(default_factory=dict)
    last_reset: datetime = field(default_factory=datetime.now)


class ModelRouter:
    """Compatibility facade: one registry and transport, with usage accounting."""

    def __init__(self, inference=None):
        from .inference import Inference
        self.inference = inference or Inference()
        self.stats = UsageStats()
        self._daily_budget = float(os.environ.get("DAILY_BUDGET_USD", "10.0"))
        self._prefer_cheap = os.environ.get("PREFER_CHEAP_MODELS", "true").lower() == "true"
        self.inference.route_slot = self._apply_budget
        self.inference.on_usage = self._record_usage
        self.models = {}
        for slot in ("primary", "fast", "tools", "vision"):
            for provider, model in self.inference.candidates(slot):
                self.models.setdefault(model, self._config(provider, model, slot))

    def _config(self, provider, model, slot="primary"):
        known = next((m for m in MODELS.values() if m.id == model), None)
        configured = self.inference.providers.model_configs.get(model, {})
        input_cost = output_cost = None
        if provider.name == "ollama":
            input_cost = output_cost = 0.0
        elif isinstance(configured, dict) and configured.get("provider", provider.name) == provider.name:
            rates = [configured.get("cost_per_1k_input"), configured.get("cost_per_1k_output")]
            if all(isinstance(rate, (int, float)) and not isinstance(rate, bool)
                   and math.isfinite(rate) and rate >= 0 for rate in rates):
                input_cost, output_cost = map(float, rates)
        return ModelConfig(
            id=model, name=model, tier=known.tier if known else (ModelTier.FAST if slot == "fast" else ModelTier.STANDARD),
            provider=provider.name, cost_per_1k_input=input_cost, cost_per_1k_output=output_cost,
            max_tokens=known.max_tokens if known else 4096, latency_ms=known.latency_ms if known else 0,
            capabilities=sorted(provider.capabilities), base_url=provider.base_url,
            api_key_env=provider.api_key_env,
            pricing_known=input_cost is not None and output_cost is not None,
        )

    def classify_complexity(self, message: str, context: dict = None) -> ModelTier:
        if len(message) < 50:
            return ModelTier.FAST
        if len(message) > 500 and any(re.search(p, message, re.I) for p in COMPLEX_TASK_PATTERNS):
            return ModelTier.PREMIUM
        return ModelTier.STANDARD

    def _slot(self, message, preferred_tier=None):
        # Budget advice runs after inference derives the complete request needs.
        return "fast" if preferred_tier == ModelTier.FAST else "primary"

    def _apply_budget(self, slot, required_capabilities=None, model_override=None):
        if self.stats.last_reset.date() != datetime.now().date():
            self.reset_daily_stats()
        if self.stats.unpriced_requests_by_model or self.stats.total_cost >= self._daily_budget:
            candidates = self.inference.providers.resolve("fast", required_capabilities or {"chat"})
            if model_override:
                candidates = [(provider, model) for provider, model in candidates
                              if model_override == model or model_override in provider.models.values()]
            if candidates:
                return "fast"
        return slot

    def _record_usage(self, metadata):
        if self.stats.last_reset.date() != datetime.now().date():
            self.reset_daily_stats()
        provider = self.inference.providers.providers[metadata["provider"]]
        self._track_usage(self._config(provider, metadata["model"]),
                          metadata["input_tokens"], metadata["output_tokens"])

    def select_model(self, message, required_capabilities=None, preferred_tier=None,
                     max_cost=None, context=None):
        slot = self._slot(message, preferred_tier)
        candidates = self.inference.candidates(slot, required_capabilities)
        for provider, model in candidates:
            config = self._config(provider, model, slot)
            if max_cost is None or (config.pricing_known
                    and config.cost_per_1k_input + config.cost_per_1k_output <= max_cost):
                return config
        raise RuntimeError(f"No configured provider supports {slot}")

    async def call(self, message, system="", history=None, model_override=None, **kwargs):
        slot = kwargs.get("slot") or self._slot(message, kwargs.get("preferred_tier"))
        override = MODELS[model_override].id if model_override in MODELS else model_override
        response, metadata = await self.inference.complete(
            list(history or []) + [{"role": "user", "content": message}], system,
            slot=slot, model_override=override, max_tokens=kwargs.get("max_tokens", 4096),
            required_capabilities=kwargs.get("required_capabilities"),
        )
        provider = self.inference.providers.providers[metadata["provider"]]
        model = self._config(provider, metadata["model"], slot)
        metadata["tier"] = model.tier.value
        return extract_text(response), metadata

    def _track_usage(self, model: ModelConfig, input_tokens: int, output_tokens: int):
        """Track usage statistics."""
        cost = None
        if model.pricing_known:
            cost = (
                (input_tokens / 1000) * model.cost_per_1k_input +
                (output_tokens / 1000) * model.cost_per_1k_output
            )
        else:
            self.stats.unpriced_requests_by_model[model.id] = (
                self.stats.unpriced_requests_by_model.get(model.id, 0) + 1
            )

        self.stats.total_requests += 1
        self.stats.total_input_tokens += input_tokens
        self.stats.total_output_tokens += output_tokens
        if cost is not None:
            self.stats.total_cost += cost

        if model.id not in self.stats.requests_by_model:
            self.stats.requests_by_model[model.id] = 0
            self.stats.cost_by_model[model.id] = 0.0

        self.stats.requests_by_model[model.id] += 1
        if cost is not None:
            self.stats.cost_by_model[model.id] += cost

        estimate = f"${cost:.4f}" if cost is not None else "price unknown"
        logger.debug(f"Usage: {model.name} - {input_tokens}+{output_tokens} tokens, {estimate}")

    def get_stats(self) -> dict:
        """Get usage statistics."""
        cost_complete = not self.stats.unpriced_requests_by_model
        return {
            "total_requests": self.stats.total_requests,
            "total_tokens": self.stats.total_input_tokens + self.stats.total_output_tokens,
            "total_cost_usd": round(self.stats.total_cost, 4),
            "cost_complete": cost_complete,
            "unpriced_models": sorted(self.stats.unpriced_requests_by_model),
            "unpriced_requests_by_model": dict(self.stats.unpriced_requests_by_model),
            "daily_budget_usd": self._daily_budget,
            "budget_remaining_usd": round(self._daily_budget - self.stats.total_cost, 4) if cost_complete else None,
            "requests_by_model": self.stats.requests_by_model,
            "cost_by_model": {k: round(v, 4) if k not in self.stats.unpriced_requests_by_model else None
                              for k, v in self.stats.cost_by_model.items()},
            "last_reset": self.stats.last_reset.isoformat(),
        }

    def reset_daily_stats(self):
        """Reset daily statistics (call at midnight)."""
        logger.info(f"Resetting daily stats. Previous: ${self.stats.total_cost:.2f}")
        self.stats = UsageStats()


# Singleton used by the canonical runtime.
_router: Optional[ModelRouter] = None


def get_model_router() -> ModelRouter:
    global _router
    if _router is None:
        _router = ModelRouter()
    return _router
