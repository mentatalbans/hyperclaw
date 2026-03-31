"""
LLM Provider Abstraction Layer.
Unified interface for 20+ AI/LLM providers.
"""
from __future__ import annotations

import os
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class Message:
    """Chat message."""
    role: str  # "user", "assistant", "system"
    content: str


@dataclass
class CompletionResponse:
    """Unified completion response."""
    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = "stop"
    raw: dict = field(default_factory=dict)


class LLMProvider(ABC):
    """Abstract base for LLM providers."""

    name: str = "base"
    supports_streaming: bool = True
    supports_vision: bool = False
    supports_tools: bool = True

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> CompletionResponse:
        """Generate a completion."""
        pass

    @abstractmethod
    async def stream(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        """Stream a completion."""
        pass

    def available_models(self) -> list[str]:
        """Return list of available models."""
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# ANTHROPIC
# ═══════════════════════════════════════════════════════════════════════════════

class AnthropicProvider(LLMProvider):
    """Anthropic Claude provider."""

    name = "anthropic"
    supports_vision = True
    default_model = "claude-sonnet-4-20250514"

    MODELS = [
        "claude-opus-4-20250514",
        "claude-sonnet-4-20250514",
        "claude-haiku-4-5-20251001",
        "claude-3-5-sonnet-20241022",
        "claude-3-opus-20240229",
    ]

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.base_url = "https://api.anthropic.com/v1"

    async def complete(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> CompletionResponse:
        model = model or self.default_model

        async with httpx.AsyncClient() as client:
            payload = {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
            }
            if system:
                payload["system"] = system

            response = await client.post(
                f"{self.base_url}/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()

            return CompletionResponse(
                content=data["content"][0]["text"],
                model=data["model"],
                input_tokens=data["usage"]["input_tokens"],
                output_tokens=data["usage"]["output_tokens"],
                finish_reason=data["stop_reason"],
                raw=data,
            )

    async def stream(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        model = model or self.default_model

        async with httpx.AsyncClient() as client:
            payload = {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
                "stream": True,
            }
            if system:
                payload["system"] = system

            async with client.stream(
                "POST",
                f"{self.base_url}/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
                timeout=120.0,
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        import json
                        try:
                            data = json.loads(line[6:])
                            if data.get("type") == "content_block_delta":
                                yield data["delta"].get("text", "")
                        except json.JSONDecodeError:
                            pass

    def available_models(self) -> list[str]:
        return self.MODELS


# ═══════════════════════════════════════════════════════════════════════════════
# OPENAI
# ═══════════════════════════════════════════════════════════════════════════════

class OpenAIProvider(LLMProvider):
    """OpenAI GPT provider."""

    name = "openai"
    supports_vision = True
    default_model = "gpt-4o"

    MODELS = [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-turbo",
        "gpt-4",
        "gpt-3.5-turbo",
        "o1-preview",
        "o1-mini",
    ]

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.base_url = base_url or "https://api.openai.com/v1"

    async def complete(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> CompletionResponse:
        model = model or self.default_model

        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend([{"role": m.role, "content": m.content} for m in messages])

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model,
                    "messages": msgs,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()

            return CompletionResponse(
                content=data["choices"][0]["message"]["content"],
                model=data["model"],
                input_tokens=data["usage"]["prompt_tokens"],
                output_tokens=data["usage"]["completion_tokens"],
                finish_reason=data["choices"][0]["finish_reason"],
                raw=data,
            )

    async def stream(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        model = model or self.default_model

        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend([{"role": m.role, "content": m.content} for m in messages])

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model,
                    "messages": msgs,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": True,
                },
                timeout=120.0,
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: ") and line != "data: [DONE]":
                        import json
                        try:
                            data = json.loads(line[6:])
                            delta = data["choices"][0].get("delta", {})
                            if "content" in delta:
                                yield delta["content"]
                        except json.JSONDecodeError:
                            pass

    def available_models(self) -> list[str]:
        return self.MODELS


# ═══════════════════════════════════════════════════════════════════════════════
# GOOGLE GEMINI
# ═══════════════════════════════════════════════════════════════════════════════

class GeminiProvider(LLMProvider):
    """Google Gemini provider."""

    name = "gemini"
    supports_vision = True
    default_model = "gemini-1.5-pro"

    MODELS = [
        "gemini-2.0-flash",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
        "gemini-1.0-pro",
    ]

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"

    async def complete(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> CompletionResponse:
        model = model or self.default_model

        contents = []
        for m in messages:
            role = "user" if m.role == "user" else "model"
            contents.append({"role": role, "parts": [{"text": m.content}]})

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature,
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/models/{model}:generateContent",
                params={"key": self.api_key},
                json=payload,
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()

            content = data["candidates"][0]["content"]["parts"][0]["text"]
            usage = data.get("usageMetadata", {})

            return CompletionResponse(
                content=content,
                model=model,
                input_tokens=usage.get("promptTokenCount", 0),
                output_tokens=usage.get("candidatesTokenCount", 0),
                finish_reason=data["candidates"][0].get("finishReason", "STOP"),
                raw=data,
            )

    async def stream(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        model = model or self.default_model

        contents = []
        for m in messages:
            role = "user" if m.role == "user" else "model"
            contents.append({"role": role, "parts": [{"text": m.content}]})

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature,
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/models/{model}:streamGenerateContent",
                params={"key": self.api_key, "alt": "sse"},
                json=payload,
                timeout=120.0,
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        import json
                        try:
                            data = json.loads(line[6:])
                            if "candidates" in data:
                                parts = data["candidates"][0].get("content", {}).get("parts", [])
                                for part in parts:
                                    if "text" in part:
                                        yield part["text"]
                        except json.JSONDecodeError:
                            pass

    def available_models(self) -> list[str]:
        return self.MODELS


# ═══════════════════════════════════════════════════════════════════════════════
# MISTRAL
# ═══════════════════════════════════════════════════════════════════════════════

class MistralProvider(LLMProvider):
    """Mistral AI provider."""

    name = "mistral"
    supports_vision = False
    default_model = "mistral-large-latest"

    MODELS = [
        "mistral-large-latest",
        "mistral-medium-latest",
        "mistral-small-latest",
        "open-mixtral-8x22b",
        "open-mixtral-8x7b",
        "codestral-latest",
    ]

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("MISTRAL_API_KEY")
        self.base_url = "https://api.mistral.ai/v1"

    async def complete(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> CompletionResponse:
        model = model or self.default_model

        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend([{"role": m.role, "content": m.content} for m in messages])

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model,
                    "messages": msgs,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()

            return CompletionResponse(
                content=data["choices"][0]["message"]["content"],
                model=data["model"],
                input_tokens=data["usage"]["prompt_tokens"],
                output_tokens=data["usage"]["completion_tokens"],
                finish_reason=data["choices"][0]["finish_reason"],
                raw=data,
            )

    async def stream(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        model = model or self.default_model

        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend([{"role": m.role, "content": m.content} for m in messages])

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model,
                    "messages": msgs,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": True,
                },
                timeout=120.0,
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: ") and line != "data: [DONE]":
                        import json
                        try:
                            data = json.loads(line[6:])
                            delta = data["choices"][0].get("delta", {})
                            if "content" in delta:
                                yield delta["content"]
                        except json.JSONDecodeError:
                            pass

    def available_models(self) -> list[str]:
        return self.MODELS


# ═══════════════════════════════════════════════════════════════════════════════
# GROQ
# ═══════════════════════════════════════════════════════════════════════════════

class GroqProvider(LLMProvider):
    """Groq ultra-fast inference provider."""

    name = "groq"
    supports_vision = False
    default_model = "llama-3.3-70b-versatile"

    MODELS = [
        "llama-3.3-70b-versatile",
        "llama-3.1-70b-versatile",
        "llama-3.1-8b-instant",
        "mixtral-8x7b-32768",
        "gemma2-9b-it",
    ]

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("GROQ_API_KEY")
        self.base_url = "https://api.groq.com/openai/v1"

    async def complete(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> CompletionResponse:
        model = model or self.default_model

        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend([{"role": m.role, "content": m.content} for m in messages])

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model,
                    "messages": msgs,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()

            return CompletionResponse(
                content=data["choices"][0]["message"]["content"],
                model=data["model"],
                input_tokens=data["usage"]["prompt_tokens"],
                output_tokens=data["usage"]["completion_tokens"],
                finish_reason=data["choices"][0]["finish_reason"],
                raw=data,
            )

    async def stream(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        model = model or self.default_model

        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend([{"role": m.role, "content": m.content} for m in messages])

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model,
                    "messages": msgs,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": True,
                },
                timeout=60.0,
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: ") and line != "data: [DONE]":
                        import json
                        try:
                            data = json.loads(line[6:])
                            delta = data["choices"][0].get("delta", {})
                            if "content" in delta:
                                yield delta["content"]
                        except json.JSONDecodeError:
                            pass

    def available_models(self) -> list[str]:
        return self.MODELS


# ═══════════════════════════════════════════════════════════════════════════════
# XAI (GROK)
# ═══════════════════════════════════════════════════════════════════════════════

class XAIProvider(LLMProvider):
    """xAI Grok provider."""

    name = "xai"
    supports_vision = True
    default_model = "grok-2"

    MODELS = ["grok-2", "grok-2-vision", "grok-beta"]

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("XAI_API_KEY")
        self.base_url = "https://api.x.ai/v1"

    async def complete(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> CompletionResponse:
        model = model or self.default_model

        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend([{"role": m.role, "content": m.content} for m in messages])

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model,
                    "messages": msgs,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()

            return CompletionResponse(
                content=data["choices"][0]["message"]["content"],
                model=data["model"],
                input_tokens=data["usage"]["prompt_tokens"],
                output_tokens=data["usage"]["completion_tokens"],
                finish_reason=data["choices"][0]["finish_reason"],
                raw=data,
            )

    async def stream(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        # xAI uses OpenAI-compatible streaming
        model = model or self.default_model

        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend([{"role": m.role, "content": m.content} for m in messages])

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model,
                    "messages": msgs,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": True,
                },
                timeout=120.0,
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: ") and line != "data: [DONE]":
                        import json
                        try:
                            data = json.loads(line[6:])
                            delta = data["choices"][0].get("delta", {})
                            if "content" in delta:
                                yield delta["content"]
                        except json.JSONDecodeError:
                            pass

    def available_models(self) -> list[str]:
        return self.MODELS


# ═══════════════════════════════════════════════════════════════════════════════
# AMAZON BEDROCK
# ═══════════════════════════════════════════════════════════════════════════════

class BedrockProvider(LLMProvider):
    """AWS Bedrock provider."""

    name = "bedrock"
    supports_vision = True
    default_model = "anthropic.claude-3-5-sonnet-20241022-v2:0"

    MODELS = [
        "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "anthropic.claude-3-opus-20240229-v1:0",
        "anthropic.claude-3-haiku-20240307-v1:0",
        "amazon.titan-text-premier-v1:0",
        "meta.llama3-70b-instruct-v1:0",
        "cohere.command-r-plus-v1:0",
    ]

    def __init__(
        self,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str = "us-east-1",
    ):
        self.access_key = access_key or os.environ.get("AWS_ACCESS_KEY_ID")
        self.secret_key = secret_key or os.environ.get("AWS_SECRET_ACCESS_KEY")
        self.region = region or os.environ.get("AWS_REGION", "us-east-1")

    async def complete(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> CompletionResponse:
        # Bedrock requires boto3 for proper AWS signing
        try:
            import boto3
        except ImportError:
            raise ImportError("boto3 required for Bedrock: pip install boto3")

        model = model or self.default_model
        client = boto3.client(
            "bedrock-runtime",
            region_name=self.region,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
        )

        # Format depends on model family
        if "anthropic" in model:
            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
            }
            if system:
                body["system"] = system
        else:
            # Generic format for other models
            body = {
                "inputText": "\n".join([f"{m.role}: {m.content}" for m in messages]),
                "textGenerationConfig": {
                    "maxTokenCount": max_tokens,
                    "temperature": temperature,
                },
            }

        import json
        response = client.invoke_model(
            modelId=model,
            body=json.dumps(body),
            contentType="application/json",
        )

        result = json.loads(response["body"].read())

        if "anthropic" in model:
            return CompletionResponse(
                content=result["content"][0]["text"],
                model=model,
                input_tokens=result["usage"]["input_tokens"],
                output_tokens=result["usage"]["output_tokens"],
                raw=result,
            )
        else:
            return CompletionResponse(
                content=result.get("results", [{}])[0].get("outputText", ""),
                model=model,
                raw=result,
            )

    async def stream(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        # Bedrock streaming requires invoke_model_with_response_stream
        # For simplicity, we'll do a non-streaming call and yield the result
        response = await self.complete(messages, model, max_tokens, temperature, system, **kwargs)
        yield response.content

    def available_models(self) -> list[str]:
        return self.MODELS


# ═══════════════════════════════════════════════════════════════════════════════
# OPENROUTER
# ═══════════════════════════════════════════════════════════════════════════════

class OpenRouterProvider(LLMProvider):
    """OpenRouter multi-model gateway."""

    name = "openrouter"
    supports_vision = True
    default_model = "anthropic/claude-3.5-sonnet"

    MODELS = [
        "anthropic/claude-3.5-sonnet",
        "anthropic/claude-3-opus",
        "openai/gpt-4o",
        "openai/gpt-4-turbo",
        "google/gemini-pro-1.5",
        "meta-llama/llama-3.1-405b-instruct",
        "mistralai/mistral-large",
    ]

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        self.base_url = "https://openrouter.ai/api/v1"

    async def complete(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> CompletionResponse:
        model = model or self.default_model

        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend([{"role": m.role, "content": m.content} for m in messages])

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "HTTP-Referer": "https://hyperclaw.ai",
                },
                json={
                    "model": model,
                    "messages": msgs,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()

            usage = data.get("usage", {})
            return CompletionResponse(
                content=data["choices"][0]["message"]["content"],
                model=data.get("model", model),
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                finish_reason=data["choices"][0].get("finish_reason", "stop"),
                raw=data,
            )

    async def stream(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        model = model or self.default_model

        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend([{"role": m.role, "content": m.content} for m in messages])

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "HTTP-Referer": "https://hyperclaw.ai",
                },
                json={
                    "model": model,
                    "messages": msgs,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": True,
                },
                timeout=120.0,
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: ") and line != "data: [DONE]":
                        import json
                        try:
                            data = json.loads(line[6:])
                            delta = data["choices"][0].get("delta", {})
                            if "content" in delta:
                                yield delta["content"]
                        except json.JSONDecodeError:
                            pass

    def available_models(self) -> list[str]:
        return self.MODELS


# ═══════════════════════════════════════════════════════════════════════════════
# TOGETHER AI
# ═══════════════════════════════════════════════════════════════════════════════

class TogetherProvider(LLMProvider):
    """Together AI provider."""

    name = "together"
    supports_vision = False
    default_model = "meta-llama/Llama-3.3-70B-Instruct-Turbo"

    MODELS = [
        "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
        "mistralai/Mixtral-8x22B-Instruct-v0.1",
        "Qwen/Qwen2.5-72B-Instruct-Turbo",
        "deepseek-ai/DeepSeek-V3",
    ]

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("TOGETHER_API_KEY")
        self.base_url = "https://api.together.xyz/v1"

    async def complete(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> CompletionResponse:
        model = model or self.default_model

        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend([{"role": m.role, "content": m.content} for m in messages])

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model,
                    "messages": msgs,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()

            return CompletionResponse(
                content=data["choices"][0]["message"]["content"],
                model=data["model"],
                input_tokens=data["usage"]["prompt_tokens"],
                output_tokens=data["usage"]["completion_tokens"],
                finish_reason=data["choices"][0]["finish_reason"],
                raw=data,
            )

    async def stream(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        model = model or self.default_model

        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend([{"role": m.role, "content": m.content} for m in messages])

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model,
                    "messages": msgs,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": True,
                },
                timeout=120.0,
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: ") and line != "data: [DONE]":
                        import json
                        try:
                            data = json.loads(line[6:])
                            delta = data["choices"][0].get("delta", {})
                            if "content" in delta:
                                yield delta["content"]
                        except json.JSONDecodeError:
                            pass

    def available_models(self) -> list[str]:
        return self.MODELS


# ═══════════════════════════════════════════════════════════════════════════════
# PERPLEXITY
# ═══════════════════════════════════════════════════════════════════════════════

class PerplexityProvider(LLMProvider):
    """Perplexity AI provider (search-augmented)."""

    name = "perplexity"
    supports_vision = False
    default_model = "llama-3.1-sonar-large-128k-online"

    MODELS = [
        "llama-3.1-sonar-large-128k-online",
        "llama-3.1-sonar-small-128k-online",
        "llama-3.1-sonar-huge-128k-online",
    ]

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("PERPLEXITY_API_KEY")
        self.base_url = "https://api.perplexity.ai"

    async def complete(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> CompletionResponse:
        model = model or self.default_model

        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend([{"role": m.role, "content": m.content} for m in messages])

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model,
                    "messages": msgs,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()

            return CompletionResponse(
                content=data["choices"][0]["message"]["content"],
                model=data["model"],
                input_tokens=data["usage"]["prompt_tokens"],
                output_tokens=data["usage"]["completion_tokens"],
                finish_reason=data["choices"][0]["finish_reason"],
                raw=data,
            )

    async def stream(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        model = model or self.default_model

        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend([{"role": m.role, "content": m.content} for m in messages])

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model,
                    "messages": msgs,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": True,
                },
                timeout=120.0,
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: ") and line != "data: [DONE]":
                        import json
                        try:
                            data = json.loads(line[6:])
                            delta = data["choices"][0].get("delta", {})
                            if "content" in delta:
                                yield delta["content"]
                        except json.JSONDecodeError:
                            pass

    def available_models(self) -> list[str]:
        return self.MODELS


# ═══════════════════════════════════════════════════════════════════════════════
# DEEPSEEK
# ═══════════════════════════════════════════════════════════════════════════════

class DeepSeekProvider(LLMProvider):
    """DeepSeek provider."""

    name = "deepseek"
    supports_vision = False
    default_model = "deepseek-chat"

    MODELS = ["deepseek-chat", "deepseek-coder", "deepseek-reasoner"]

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        self.base_url = "https://api.deepseek.com/v1"

    async def complete(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> CompletionResponse:
        model = model or self.default_model

        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend([{"role": m.role, "content": m.content} for m in messages])

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model,
                    "messages": msgs,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()

            return CompletionResponse(
                content=data["choices"][0]["message"]["content"],
                model=data["model"],
                input_tokens=data["usage"]["prompt_tokens"],
                output_tokens=data["usage"]["completion_tokens"],
                finish_reason=data["choices"][0]["finish_reason"],
                raw=data,
            )

    async def stream(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        model = model or self.default_model

        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend([{"role": m.role, "content": m.content} for m in messages])

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model,
                    "messages": msgs,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": True,
                },
                timeout=120.0,
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: ") and line != "data: [DONE]":
                        import json
                        try:
                            data = json.loads(line[6:])
                            delta = data["choices"][0].get("delta", {})
                            if "content" in delta:
                                yield delta["content"]
                        except json.JSONDecodeError:
                            pass

    def available_models(self) -> list[str]:
        return self.MODELS


# ═══════════════════════════════════════════════════════════════════════════════
# QWEN (ALIBABA)
# ═══════════════════════════════════════════════════════════════════════════════

class QwenProvider(LLMProvider):
    """Alibaba Qwen provider."""

    name = "qwen"
    supports_vision = True
    default_model = "qwen-max"

    MODELS = ["qwen-max", "qwen-plus", "qwen-turbo", "qwen-vl-max"]

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("QWEN_API_KEY") or os.environ.get("DASHSCOPE_API_KEY")
        self.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    async def complete(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> CompletionResponse:
        model = model or self.default_model

        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend([{"role": m.role, "content": m.content} for m in messages])

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model,
                    "messages": msgs,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()

            return CompletionResponse(
                content=data["choices"][0]["message"]["content"],
                model=data["model"],
                input_tokens=data["usage"]["prompt_tokens"],
                output_tokens=data["usage"]["completion_tokens"],
                finish_reason=data["choices"][0]["finish_reason"],
                raw=data,
            )

    async def stream(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        model = model or self.default_model

        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend([{"role": m.role, "content": m.content} for m in messages])

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model,
                    "messages": msgs,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": True,
                },
                timeout=120.0,
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: ") and line != "data: [DONE]":
                        import json
                        try:
                            data = json.loads(line[6:])
                            delta = data["choices"][0].get("delta", {})
                            if "content" in delta:
                                yield delta["content"]
                        except json.JSONDecodeError:
                            pass

    def available_models(self) -> list[str]:
        return self.MODELS


# ═══════════════════════════════════════════════════════════════════════════════
# CEREBRAS
# ═══════════════════════════════════════════════════════════════════════════════

class CerebrasProvider(LLMProvider):
    """Cerebras ultra-fast inference provider."""

    name = "cerebras"
    supports_vision = False
    default_model = "llama3.1-70b"

    MODELS = ["llama3.1-70b", "llama3.1-8b"]

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("CEREBRAS_API_KEY")
        self.base_url = "https://api.cerebras.ai/v1"

    async def complete(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> CompletionResponse:
        model = model or self.default_model

        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend([{"role": m.role, "content": m.content} for m in messages])

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model,
                    "messages": msgs,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()

            return CompletionResponse(
                content=data["choices"][0]["message"]["content"],
                model=data["model"],
                input_tokens=data["usage"]["prompt_tokens"],
                output_tokens=data["usage"]["completion_tokens"],
                finish_reason=data["choices"][0]["finish_reason"],
                raw=data,
            )

    async def stream(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        model = model or self.default_model

        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend([{"role": m.role, "content": m.content} for m in messages])

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model,
                    "messages": msgs,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": True,
                },
                timeout=60.0,
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: ") and line != "data: [DONE]":
                        import json
                        try:
                            data = json.loads(line[6:])
                            delta = data["choices"][0].get("delta", {})
                            if "content" in delta:
                                yield delta["content"]
                        except json.JSONDecodeError:
                            pass

    def available_models(self) -> list[str]:
        return self.MODELS


# ═══════════════════════════════════════════════════════════════════════════════
# HUGGING FACE
# ═══════════════════════════════════════════════════════════════════════════════

class HuggingFaceProvider(LLMProvider):
    """Hugging Face Inference API provider."""

    name = "huggingface"
    supports_vision = False
    default_model = "meta-llama/Llama-3.1-70B-Instruct"

    MODELS = [
        "meta-llama/Llama-3.1-70B-Instruct",
        "mistralai/Mixtral-8x7B-Instruct-v0.1",
        "HuggingFaceH4/zephyr-7b-beta",
    ]

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("HUGGINGFACE_API_KEY") or os.environ.get("HF_TOKEN")
        self.base_url = "https://api-inference.huggingface.co/models"

    async def complete(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> CompletionResponse:
        model = model or self.default_model

        # Format as conversation
        prompt = ""
        if system:
            prompt += f"<|system|>\n{system}\n"
        for m in messages:
            prompt += f"<|{m.role}|>\n{m.content}\n"
        prompt += "<|assistant|>\n"

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/{model}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "inputs": prompt,
                    "parameters": {
                        "max_new_tokens": max_tokens,
                        "temperature": temperature,
                        "return_full_text": False,
                    },
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()

            content = data[0]["generated_text"] if isinstance(data, list) else data.get("generated_text", "")

            return CompletionResponse(
                content=content,
                model=model,
                raw=data,
            )

    async def stream(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        # HF Inference API has limited streaming support
        response = await self.complete(messages, model, max_tokens, temperature, system, **kwargs)
        yield response.content

    def available_models(self) -> list[str]:
        return self.MODELS


# ═══════════════════════════════════════════════════════════════════════════════
# OLLAMA (LOCAL)
# ═══════════════════════════════════════════════════════════════════════════════

class OllamaProvider(LLMProvider):
    """Ollama local inference provider."""

    name = "ollama"
    supports_vision = True
    default_model = "llama3.2"

    MODELS = ["llama3.2", "llama3.1", "mistral", "mixtral", "codellama", "llava"]

    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or os.environ.get("OLLAMA_HOST", "http://localhost:11434")

    async def complete(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> CompletionResponse:
        model = model or self.default_model

        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend([{"role": m.role, "content": m.content} for m in messages])

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": model,
                    "messages": msgs,
                    "options": {
                        "num_predict": max_tokens,
                        "temperature": temperature,
                    },
                    "stream": False,
                },
                timeout=300.0,
            )
            response.raise_for_status()
            data = response.json()

            return CompletionResponse(
                content=data["message"]["content"],
                model=model,
                input_tokens=data.get("prompt_eval_count", 0),
                output_tokens=data.get("eval_count", 0),
                raw=data,
            )

    async def stream(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        model = model or self.default_model

        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend([{"role": m.role, "content": m.content} for m in messages])

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json={
                    "model": model,
                    "messages": msgs,
                    "options": {
                        "num_predict": max_tokens,
                        "temperature": temperature,
                    },
                    "stream": True,
                },
                timeout=300.0,
            ) as response:
                async for line in response.aiter_lines():
                    if line:
                        import json
                        try:
                            data = json.loads(line)
                            if "message" in data:
                                yield data["message"].get("content", "")
                        except json.JSONDecodeError:
                            pass

    def available_models(self) -> list[str]:
        return self.MODELS


# ═══════════════════════════════════════════════════════════════════════════════
# VLLM (LOCAL)
# ═══════════════════════════════════════════════════════════════════════════════

class VLLMProvider(LLMProvider):
    """vLLM local inference provider (OpenAI-compatible)."""

    name = "vllm"
    supports_vision = False
    default_model = "default"

    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or os.environ.get("VLLM_HOST", "http://localhost:8000/v1")

    async def complete(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> CompletionResponse:
        model = model or self.default_model

        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend([{"role": m.role, "content": m.content} for m in messages])

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": msgs,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=300.0,
            )
            response.raise_for_status()
            data = response.json()

            return CompletionResponse(
                content=data["choices"][0]["message"]["content"],
                model=data["model"],
                input_tokens=data["usage"]["prompt_tokens"],
                output_tokens=data["usage"]["completion_tokens"],
                finish_reason=data["choices"][0]["finish_reason"],
                raw=data,
            )

    async def stream(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        model = model or self.default_model

        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend([{"role": m.role, "content": m.content} for m in messages])

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": msgs,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": True,
                },
                timeout=300.0,
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: ") and line != "data: [DONE]":
                        import json
                        try:
                            data = json.loads(line[6:])
                            delta = data["choices"][0].get("delta", {})
                            if "content" in delta:
                                yield delta["content"]
                        except json.JSONDecodeError:
                            pass

    def available_models(self) -> list[str]:
        return [self.default_model]


# ═══════════════════════════════════════════════════════════════════════════════
# LITELLM (UNIVERSAL PROXY)
# ═══════════════════════════════════════════════════════════════════════════════

class LiteLLMProvider(LLMProvider):
    """LiteLLM universal proxy — routes to any provider."""

    name = "litellm"
    supports_vision = True
    default_model = "gpt-4o"

    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        self.base_url = base_url or os.environ.get("LITELLM_HOST", "http://localhost:4000/v1")
        self.api_key = api_key or os.environ.get("LITELLM_API_KEY", "")

    async def complete(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> CompletionResponse:
        model = model or self.default_model

        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend([{"role": m.role, "content": m.content} for m in messages])

        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": msgs,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()

            return CompletionResponse(
                content=data["choices"][0]["message"]["content"],
                model=data["model"],
                input_tokens=data["usage"]["prompt_tokens"],
                output_tokens=data["usage"]["completion_tokens"],
                finish_reason=data["choices"][0]["finish_reason"],
                raw=data,
            )

    async def stream(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        model = model or self.default_model

        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend([{"role": m.role, "content": m.content} for m in messages])

        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": msgs,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": True,
                },
                timeout=120.0,
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: ") and line != "data: [DONE]":
                        import json
                        try:
                            data = json.loads(line[6:])
                            delta = data["choices"][0].get("delta", {})
                            if "content" in delta:
                                yield delta["content"]
                        except json.JSONDecodeError:
                            pass

    def available_models(self) -> list[str]:
        return []  # LiteLLM can route to any model


# ═══════════════════════════════════════════════════════════════════════════════
# PROVIDER FACTORY
# ═══════════════════════════════════════════════════════════════════════════════

PROVIDERS: dict[str, type[LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "google": GeminiProvider,
    "mistral": MistralProvider,
    "groq": GroqProvider,
    "xai": XAIProvider,
    "grok": XAIProvider,
    "bedrock": BedrockProvider,
    "aws": BedrockProvider,
    "openrouter": OpenRouterProvider,
    "together": TogetherProvider,
    "perplexity": PerplexityProvider,
    "deepseek": DeepSeekProvider,
    "qwen": QwenProvider,
    "alibaba": QwenProvider,
    "cerebras": CerebrasProvider,
    "huggingface": HuggingFaceProvider,
    "hf": HuggingFaceProvider,
    "ollama": OllamaProvider,
    "vllm": VLLMProvider,
    "litellm": LiteLLMProvider,
}


def get_llm_provider(name: str | None = None, **kwargs) -> LLMProvider:
    """
    Get an LLM provider by name.

    Args:
        name: Provider name (anthropic, openai, gemini, etc.)
              Defaults to HYPERCLAW_LLM_PROVIDER env var or "anthropic"
        **kwargs: Provider-specific configuration

    Returns:
        Configured LLM provider instance
    """
    name = name or os.environ.get("HYPERCLAW_LLM_PROVIDER", "anthropic")
    name = name.lower()

    if name not in PROVIDERS:
        available = ", ".join(sorted(PROVIDERS.keys()))
        raise ValueError(f"Unknown provider: {name}. Available: {available}")

    return PROVIDERS[name](**kwargs)
