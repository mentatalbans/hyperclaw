"""
ChatJimmyClient — client for ChatJimmy, powered by Taalas HC1 silicon.
Llama 3.1 8B hardwired at 17,000 tokens/sec.

IMPORTANT: ChatJimmy outputs are NEVER auto-certified.
All ChatJimmy outputs must pass through Claude verification before certification.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import AsyncIterator

import httpx

log = logging.getLogger("hyperclaw.chatjimmy")


class ChatJimmyTimeoutError(Exception):
    """Raised when ChatJimmy does not respond within the 5.0s hard timeout."""
    pass


@dataclass
class ChatJimmyStats:
    prefill_tokens: int = 0
    prefill_rate: float = 0.0
    decode_tokens: int = 0
    decode_rate: float = 0.0
    total_tokens: int = 0
    ttft_seconds: float = 0.0
    total_time_seconds: float = 0.0
    roundtrip_ms: float = 0.0
    done_reason: str = "stop"


@dataclass
class ChatJimmyResponse:
    text: str
    stats: ChatJimmyStats
    certified: bool = False

    @property
    def is_suitable_for_certification(self) -> bool:
        """
        ChatJimmy outputs are never auto-certified.
        All ChatJimmy outputs must pass through Claude verification before certification.
        """
        return False


class ChatJimmyClient:
    """
    HTTP client for ChatJimmy API (Taalas HC1, Llama 3.1 8B @ 17k tok/sec).

    Suitable for fast, cheap, low-stakes tasks.
    NOT suitable for tasks requiring certified outputs — always route through Claude first.
    """

    BASE_URL: str = "https://chatjimmy.ai/api"
    TIMEOUT_SECONDS: float = 5.0

    SUITABLE_TASK_TYPES: frozenset[str] = frozenset([
        "routing", "classification", "quick_lookup",
        "summarization", "draft", "triage", "status_check",
    ])

    def __init__(
        self,
        api_key: str = "",
        base_url: str | None = None,
        _mock: bool = False,
        _mock_latency_ms: float = 50.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url or self.BASE_URL
        self._mock = _mock
        self._mock_latency_ms = _mock_latency_ms
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=self.TIMEOUT_SECONDS,
        )

    async def chat(
        self,
        messages: list[dict],
        system_prompt: str = "",
    ) -> ChatJimmyResponse:
        """
        Send a chat request. Hard 5.0s timeout — raises ChatJimmyTimeoutError on expiry.
        """
        if self._mock:
            return await self._mock_chat(messages, system_prompt)

        payload: dict = {"messages": messages}
        if system_prompt:
            payload["system"] = system_prompt

        try:
            import time
            t0 = time.time()
            response = await self._http.post("/chat", json=payload)
            roundtrip_ms = (time.time() - t0) * 1000
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as e:
            raise ChatJimmyTimeoutError(
                f"ChatJimmy did not respond within {self.TIMEOUT_SECONDS}s: {e}"
            ) from e

        text = data.get("text") or data.get("content") or data.get("response", "")
        stats_raw = data.get("stats", {})
        stats = ChatJimmyStats(
            prefill_tokens=stats_raw.get("prefill_tokens", 0),
            prefill_rate=stats_raw.get("prefill_rate", 0.0),
            decode_tokens=stats_raw.get("decode_tokens", 0),
            decode_rate=stats_raw.get("decode_rate", 17000.0),
            total_tokens=stats_raw.get("total_tokens", 0),
            ttft_seconds=stats_raw.get("ttft_seconds", 0.0),
            total_time_seconds=stats_raw.get("total_time_seconds", 0.0),
            roundtrip_ms=roundtrip_ms,
            done_reason=stats_raw.get("done_reason", "stop"),
        )
        return ChatJimmyResponse(text=text, stats=stats, certified=False)

    async def chat_stream(
        self,
        messages: list[dict],
        system_prompt: str = "",
    ) -> AsyncIterator[str]:
        """Stream a ChatJimmy response. Yields text chunks."""
        if self._mock:
            mock_response = await self._mock_chat(messages, system_prompt)
            for chunk in mock_response.text.split():
                yield chunk + " "
                await asyncio.sleep(0.003)
            return

        payload: dict = {"messages": messages, "stream": True}
        if system_prompt:
            payload["system"] = system_prompt

        try:
            async with self._http.stream("POST", "/chat", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        chunk = line[6:]
                        if chunk != "[DONE]":
                            yield chunk
        except httpx.TimeoutException as e:
            raise ChatJimmyTimeoutError(
                f"ChatJimmy stream timed out: {e}"
            ) from e

    async def health(self) -> bool:
        """Check if ChatJimmy API is reachable. Returns True if healthy."""
        if self._mock:
            return True
        try:
            r = await self._http.get("/health", timeout=2.0)
            return r.status_code == 200
        except Exception:
            return False

    @classmethod
    def is_suitable_for(cls, task_type: str) -> bool:
        """Return True if ChatJimmy is appropriate for this task type."""
        return task_type in cls.SUITABLE_TASK_TYPES

    async def _mock_chat(
        self,
        messages: list[dict],
        system_prompt: str = "",
    ) -> ChatJimmyResponse:
        """Simulate a realistic ChatJimmy response with configured latency."""
        await asyncio.sleep(self._mock_latency_ms / 1000.0)
        last_content = messages[-1].get("content", "hello") if messages else "hello"
        text = f"[ChatJimmy mock] Processed: {str(last_content)[:80]}"
        stats = ChatJimmyStats(
            prefill_tokens=len(str(last_content).split()),
            prefill_rate=17000.0,
            decode_tokens=len(text.split()),
            decode_rate=17000.0,
            total_tokens=len(str(last_content).split()) + len(text.split()),
            ttft_seconds=self._mock_latency_ms / 1000.0 * 0.1,
            total_time_seconds=self._mock_latency_ms / 1000.0,
            roundtrip_ms=self._mock_latency_ms,
            done_reason="stop",
        )
        return ChatJimmyResponse(text=text, stats=stats, certified=False)

    async def __aenter__(self) -> "ChatJimmyClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self._http.aclose()


def mock_client() -> ChatJimmyClient:
    """
    Return a mock ChatJimmyClient with realistic responses and 50ms simulated latency.
    Safe for use in tests without network access.
    """
    return ChatJimmyClient(_mock=True, _mock_latency_ms=50.0)
