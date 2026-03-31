"""
HyperClaw Model Router
Intelligent routing to optimize cost, speed, and quality.
Routes simple tasks to ChatJimmy, complex tasks to Claude.
"""

import logging
import os
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
    cost_per_1k_input: float
    cost_per_1k_output: float
    max_tokens: int
    latency_ms: int  # typical latency
    capabilities: list[str] = field(default_factory=list)
    base_url: Optional[str] = None
    api_key_env: str = ""


# Model registry
MODELS = {
    # ChatJimmy - Fast & cheap for simple tasks
    "chatjimmy": ModelConfig(
        id="chatjimmy",
        name="ChatJimmy (Llama 3.1 8B)",
        tier=ModelTier.FAST,
        provider="chatjimmy",
        cost_per_1k_input=0.00001,
        cost_per_1k_output=0.00001,
        max_tokens=2048,
        latency_ms=50,
        capabilities=["chat", "simple_qa", "classification", "extraction"],
        base_url="https://api.taalas.ai/v1",
        api_key_env="CHATJIMMY_API_KEY",
    ),

    # Claude Haiku - Fast Claude for moderate tasks
    "claude-haiku": ModelConfig(
        id="claude-haiku-4-5-20251001",
        name="Claude Haiku 4.5",
        tier=ModelTier.FAST,
        provider="anthropic",
        cost_per_1k_input=0.0008,
        cost_per_1k_output=0.004,
        max_tokens=4096,
        latency_ms=500,
        capabilities=["chat", "analysis", "coding", "writing"],
        api_key_env="ANTHROPIC_API_KEY",
    ),

    # Claude Sonnet - Balanced for most tasks
    "claude-sonnet": ModelConfig(
        id="claude-sonnet-4-20250514",
        name="Claude Sonnet 4",
        tier=ModelTier.STANDARD,
        provider="anthropic",
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        max_tokens=8192,
        latency_ms=1500,
        capabilities=["chat", "analysis", "coding", "writing", "reasoning", "planning"],
        api_key_env="ANTHROPIC_API_KEY",
    ),

    # Claude Opus - Premium for complex tasks
    "claude-opus": ModelConfig(
        id="claude-opus-4-20250514",
        name="Claude Opus 4",
        tier=ModelTier.PREMIUM,
        provider="anthropic",
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
        max_tokens=8192,
        latency_ms=3000,
        capabilities=["chat", "analysis", "coding", "writing", "reasoning", "planning", "complex_reasoning", "research"],
        api_key_env="ANTHROPIC_API_KEY",
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
    last_reset: datetime = field(default_factory=datetime.now)


class ModelRouter:
    """
    Intelligent model router that selects the best model based on:
    - Task complexity
    - Cost constraints
    - Required capabilities
    - Current load/latency
    """

    def __init__(self):
        self.models = MODELS.copy()
        self.stats = UsageStats()
        self._clients: dict[str, Any] = {}
        self._daily_budget = float(os.environ.get("DAILY_BUDGET_USD", "10.0"))
        self._prefer_cheap = os.environ.get("PREFER_CHEAP_MODELS", "true").lower() == "true"

    def _get_client(self, model_config: ModelConfig):
        """Get or create API client for a model."""
        provider = model_config.provider

        if provider not in self._clients:
            if provider == "anthropic":
                import anthropic
                api_key = os.environ.get(model_config.api_key_env, "")
                self._clients[provider] = anthropic.Anthropic(api_key=api_key)

            elif provider == "chatjimmy":
                # ChatJimmy uses OpenAI-compatible API
                api_key = os.environ.get(model_config.api_key_env, "")
                self._clients[provider] = {
                    "base_url": model_config.base_url,
                    "api_key": api_key,
                }

        return self._clients.get(provider)

    def classify_complexity(self, message: str, context: dict = None) -> ModelTier:
        """Classify task complexity to determine model tier."""
        message_lower = message.lower().strip()

        # Check for simple patterns
        for pattern in SIMPLE_TASK_PATTERNS:
            if re.search(pattern, message_lower, re.IGNORECASE):
                return ModelTier.FAST

        # Check for complex patterns
        for pattern in COMPLEX_TASK_PATTERNS:
            if re.search(pattern, message_lower, re.IGNORECASE):
                return ModelTier.PREMIUM if len(message) > 500 else ModelTier.STANDARD

        # Check message length as proxy for complexity
        if len(message) < 50:
            return ModelTier.FAST
        elif len(message) < 200:
            return ModelTier.STANDARD
        else:
            return ModelTier.STANDARD  # Default to standard, not premium

    def select_model(
        self,
        message: str,
        required_capabilities: list[str] = None,
        preferred_tier: ModelTier = None,
        max_cost: float = None,
        context: dict = None
    ) -> ModelConfig:
        """
        Select the best model for a task.

        Args:
            message: The user message/task
            required_capabilities: Capabilities the model must have
            preferred_tier: Override automatic tier selection
            max_cost: Maximum cost per request (approximate)
            context: Additional context for routing

        Returns:
            Selected ModelConfig
        """
        # Determine tier
        if preferred_tier:
            tier = preferred_tier
        else:
            tier = self.classify_complexity(message, context)

        # Check budget
        if self.stats.total_cost >= self._daily_budget:
            logger.warning(f"Daily budget exceeded (${self.stats.total_cost:.2f}), forcing FAST tier")
            tier = ModelTier.FAST

        # Filter models by tier and capabilities
        candidates = []
        for model_id, config in self.models.items():
            # Check tier
            if self._prefer_cheap:
                # Allow same tier or cheaper
                if config.tier.value > tier.value:
                    continue
            else:
                # Exact tier match
                if config.tier != tier:
                    continue

            # Check capabilities
            if required_capabilities:
                if not all(cap in config.capabilities for cap in required_capabilities):
                    continue

            # Check API key available
            if config.api_key_env and not os.environ.get(config.api_key_env):
                continue

            candidates.append(config)

        if not candidates:
            # Fallback to any available model
            for model_id, config in self.models.items():
                if config.api_key_env and os.environ.get(config.api_key_env):
                    candidates.append(config)
                    break

        if not candidates:
            raise RuntimeError("No models available - check API keys")

        # Sort by cost (cheapest first if prefer_cheap)
        if self._prefer_cheap:
            candidates.sort(key=lambda m: m.cost_per_1k_input + m.cost_per_1k_output)
        else:
            # Sort by capability (most capable first)
            tier_order = {ModelTier.PREMIUM: 0, ModelTier.STANDARD: 1, ModelTier.FAST: 2}
            candidates.sort(key=lambda m: tier_order.get(m.tier, 2))

        selected = candidates[0]
        logger.debug(f"Selected model: {selected.name} (tier={tier.value}, cost=${selected.cost_per_1k_input}/1k)")

        return selected

    async def call(
        self,
        message: str,
        system: str = "",
        history: list[dict] = None,
        model_override: str = None,
        **kwargs
    ) -> tuple[str, dict]:
        """
        Call the appropriate model.

        Returns:
            Tuple of (response_text, metadata)
        """
        # Select model
        if model_override and model_override in self.models:
            model = self.models[model_override]
        else:
            model = self.select_model(message, context=kwargs.get("context"))

        start_time = time.time()

        try:
            if model.provider == "anthropic":
                response, metadata = await self._call_anthropic(model, message, system, history)
            elif model.provider == "chatjimmy":
                response, metadata = await self._call_chatjimmy(model, message, system, history)
            else:
                raise ValueError(f"Unknown provider: {model.provider}")

            # Track usage
            latency_ms = int((time.time() - start_time) * 1000)
            self._track_usage(model, metadata.get("input_tokens", 0), metadata.get("output_tokens", 0))

            metadata["model"] = model.id
            metadata["model_name"] = model.name
            metadata["tier"] = model.tier.value
            metadata["latency_ms"] = latency_ms

            return response, metadata

        except Exception as e:
            logger.error(f"Model call failed ({model.name}): {e}")

            # Try fallback to cheaper model
            if model.tier != ModelTier.FAST:
                logger.info("Attempting fallback to fast tier")
                fallback = self.select_model(message, preferred_tier=ModelTier.FAST)
                if fallback.id != model.id:
                    return await self.call(message, system, history, model_override=fallback.id, **kwargs)

            raise

    async def _call_anthropic(
        self,
        model: ModelConfig,
        message: str,
        system: str,
        history: list[dict]
    ) -> tuple[str, dict]:
        """Call Anthropic API."""
        import anthropic
        import asyncio

        client = self._get_client(model)

        messages = []
        if history:
            messages.extend(history[-20:])  # Last 20 messages
        messages.append({"role": "user", "content": message})

        response = await asyncio.to_thread(
            client.messages.create,
            model=model.id,
            max_tokens=model.max_tokens,
            system=system if system else None,
            messages=messages,
        )

        return response.content[0].text, {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }

    async def _call_chatjimmy(
        self,
        model: ModelConfig,
        message: str,
        system: str,
        history: list[dict]
    ) -> tuple[str, dict]:
        """Call ChatJimmy (OpenAI-compatible) API."""
        config = self._get_client(model)

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        if history:
            messages.extend(history[-10:])  # Smaller context for fast model
        messages.append({"role": "user", "content": message})

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{config['base_url']}/chat/completions",
                headers={
                    "Authorization": f"Bearer {config['api_key']}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": messages,
                    "max_tokens": model.max_tokens,
                    "temperature": 0.7,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

        choice = data["choices"][0]
        usage = data.get("usage", {})

        return choice["message"]["content"], {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        }

    def _track_usage(self, model: ModelConfig, input_tokens: int, output_tokens: int):
        """Track usage statistics."""
        cost = (
            (input_tokens / 1000) * model.cost_per_1k_input +
            (output_tokens / 1000) * model.cost_per_1k_output
        )

        self.stats.total_requests += 1
        self.stats.total_input_tokens += input_tokens
        self.stats.total_output_tokens += output_tokens
        self.stats.total_cost += cost

        if model.id not in self.stats.requests_by_model:
            self.stats.requests_by_model[model.id] = 0
            self.stats.cost_by_model[model.id] = 0.0

        self.stats.requests_by_model[model.id] += 1
        self.stats.cost_by_model[model.id] += cost

        logger.debug(f"Usage: {model.name} - {input_tokens}+{output_tokens} tokens, ${cost:.4f}")

    def get_stats(self) -> dict:
        """Get usage statistics."""
        return {
            "total_requests": self.stats.total_requests,
            "total_tokens": self.stats.total_input_tokens + self.stats.total_output_tokens,
            "total_cost_usd": round(self.stats.total_cost, 4),
            "daily_budget_usd": self._daily_budget,
            "budget_remaining_usd": round(self._daily_budget - self.stats.total_cost, 4),
            "requests_by_model": self.stats.requests_by_model,
            "cost_by_model": {k: round(v, 4) for k, v in self.stats.cost_by_model.items()},
            "last_reset": self.stats.last_reset.isoformat(),
        }

    def reset_daily_stats(self):
        """Reset daily statistics (call at midnight)."""
        logger.info(f"Resetting daily stats. Previous: ${self.stats.total_cost:.2f}")
        self.stats = UsageStats()


# Singleton
_router: Optional[ModelRouter] = None


def get_model_router() -> ModelRouter:
    """Get or create model router singleton."""
    global _router
    if _router is None:
        _router = ModelRouter()
    return _router
