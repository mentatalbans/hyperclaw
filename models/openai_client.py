"""
OpenAIClient — async OpenAI-compatible client supporting custom base_url.
Works with OpenAI, Azure OpenAI, local vLLM, LiteLLM, Ollama, and any
endpoint that implements the OpenAI chat completions API.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator

from openai import AsyncOpenAI, APIConnectionError, APITimeoutError, RateLimitError

log = logging.getLogger("hyperclaw.openai_client")


class OpenAIClient:
    """
    Async OpenAI-compatible client with retry logic and cost tracking.

    Pass base_url to point at any OpenAI-compatible endpoint:
      - Azure OpenAI: https://<resource>.openai.azure.com/openai/deployments/<deployment>
      - vLLM:         http://localhost:8000/v1
      - LiteLLM:      http://localhost:4000/v1
      - Ollama:       http://localhost:11434/v1
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        max_tokens: int = 4096,
        base_url: str | None = None,
        cost_per_1k_input: float = 0.005,
        cost_per_1k_output: float = 0.015,
    ) -> None:
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        self.model = model
        self.max_tokens = max_tokens
        self._cost_in = cost_per_1k_input
        self._cost_out = cost_per_1k_output

    async def chat(
        self,
        messages: list[dict],
        system: str = "",
    ) -> str:
        """
        Send a chat request with retry logic (3 attempts, exponential backoff).
        Returns the assistant's text response.
        """
        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return await self._do_chat(full_messages)
            except (APIConnectionError, APITimeoutError) as e:
                last_error = e
                wait = 2 ** attempt
                log.warning(f"OpenAI transient error (attempt {attempt+1}/3): {e}. Retrying in {wait}s...")
                await asyncio.sleep(wait)
            except RateLimitError as e:
                last_error = e
                wait = 4 ** attempt
                log.warning(f"OpenAI rate limit (attempt {attempt+1}/3). Retrying in {wait}s...")
                await asyncio.sleep(wait)
        raise last_error or RuntimeError("OpenAI chat failed after 3 attempts")

    async def _do_chat(self, messages: list[dict]) -> str:
        t0 = time.time()
        response = await self._client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=messages,
        )
        latency_ms = (time.time() - t0) * 1000

        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0
        cost = (input_tokens / 1000 * self._cost_in) + (output_tokens / 1000 * self._cost_out)
        text = response.choices[0].message.content or "" if response.choices else ""

        log.info(
            f"OpenAI call | model={self.model} | "
            f"in={input_tokens} out={output_tokens} | "
            f"latency={latency_ms:.0f}ms | cost=${cost:.5f}"
        )
        return text

    async def chat_stream(
        self,
        messages: list[dict],
        system: str = "",
    ) -> AsyncIterator[str]:
        """Stream a chat response. Yields text chunks as they arrive."""
        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        stream = await self._client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=full_messages,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta
