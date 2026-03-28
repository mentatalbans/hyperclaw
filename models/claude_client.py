"""
ClaudeClient — async Anthropic API client with retry logic, streaming, and cost tracking.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator

import anthropic

log = logging.getLogger("hyperclaw.claude_client")

# Cost per 1K tokens (approximate, subject to Anthropic pricing changes)
_INPUT_COST_PER_1K = 0.003
_OUTPUT_COST_PER_1K = 0.015


class ClaudeClient:
    """
    Async Anthropic Claude client.

    Retries up to 3 times with exponential backoff on transient errors.
    Logs every call: model, token counts, latency, and cost estimate.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 4096,
    ) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    async def chat(
        self,
        messages: list[dict],
        system: str = "",
    ) -> str:
        """
        Send a chat request with retry logic (3 attempts, exponential backoff).
        Returns the assistant's text response.
        """
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return await self._do_chat(messages, system)
            except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
                last_error = e
                wait = 2 ** attempt
                log.warning(f"Claude transient error (attempt {attempt+1}/3): {e}. Retrying in {wait}s...")
                await asyncio.sleep(wait)
            except anthropic.RateLimitError as e:
                last_error = e
                wait = 4 ** attempt
                log.warning(f"Claude rate limit (attempt {attempt+1}/3). Retrying in {wait}s...")
                await asyncio.sleep(wait)
        raise last_error or RuntimeError("Claude chat failed after 3 attempts")

    async def _do_chat(self, messages: list[dict], system: str) -> str:
        t0 = time.time()
        kwargs: dict = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=messages,
        )
        if system:
            kwargs["system"] = system

        response = await self._client.messages.create(**kwargs)
        latency_ms = (time.time() - t0) * 1000

        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost = (input_tokens / 1000 * _INPUT_COST_PER_1K) + (output_tokens / 1000 * _OUTPUT_COST_PER_1K)
        text = response.content[0].text if response.content else ""

        log.info(
            f"Claude call | model={self.model} | "
            f"in={input_tokens} out={output_tokens} | "
            f"latency={latency_ms:.0f}ms | cost=${cost:.5f}"
        )
        return text

    async def chat_stream(
        self,
        messages: list[dict],
        system: str = "",
    ) -> AsyncIterator[str]:
        """
        Stream a chat response. Yields text chunks as they arrive.
        """
        kwargs: dict = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=messages,
        )
        if system:
            kwargs["system"] = system

        async with self._client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text
