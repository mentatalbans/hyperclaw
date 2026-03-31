"""
Unit tests for models/chatjimmy_client.py — ChatJimmyClient, mock_client, certification rules.
"""
from __future__ import annotations

import asyncio
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from models.chatjimmy_client import (
    ChatJimmyClient,
    ChatJimmyResponse,
    ChatJimmyStats,
    ChatJimmyTimeoutError,
    mock_client,
)


@pytest.fixture
def client():
    return mock_client()


class TestChatJimmyResponse:
    def test_is_suitable_for_certification_always_false(self):
        stats = ChatJimmyStats()
        resp = ChatJimmyResponse(text="hello", stats=stats)
        assert resp.is_suitable_for_certification is False

    def test_certified_defaults_false(self):
        stats = ChatJimmyStats()
        resp = ChatJimmyResponse(text="hello", stats=stats)
        assert resp.certified is False

    def test_cannot_certify_even_if_set(self):
        stats = ChatJimmyStats()
        resp = ChatJimmyResponse(text="hello", stats=stats, certified=True)
        # The property always returns False regardless
        assert resp.is_suitable_for_certification is False


class TestChatJimmyClient:
    @pytest.mark.asyncio
    async def test_mock_chat_returns_response(self, client):
        resp = await client.chat([{"role": "user", "content": "hello"}])
        assert isinstance(resp, ChatJimmyResponse)
        assert len(resp.text) > 0

    @pytest.mark.asyncio
    async def test_mock_chat_includes_stats(self, client):
        resp = await client.chat([{"role": "user", "content": "test"}])
        assert isinstance(resp.stats, ChatJimmyStats)
        assert resp.stats.roundtrip_ms > 0

    @pytest.mark.asyncio
    async def test_mock_chat_latency_approximately_50ms(self):
        import time
        c = mock_client()
        t0 = time.time()
        await c.chat([{"role": "user", "content": "test"}])
        elapsed_ms = (time.time() - t0) * 1000
        # Should be ~50ms ± 50ms
        assert 30 <= elapsed_ms <= 200

    @pytest.mark.asyncio
    async def test_mock_health_returns_true(self, client):
        assert await client.health() is True

    def test_is_suitable_for_routing(self):
        assert ChatJimmyClient.is_suitable_for("routing") is True

    def test_is_suitable_for_classification(self):
        assert ChatJimmyClient.is_suitable_for("classification") is True

    def test_not_suitable_for_research(self):
        assert ChatJimmyClient.is_suitable_for("research") is False

    def test_not_suitable_for_code(self):
        assert ChatJimmyClient.is_suitable_for("code") is False

    def test_not_suitable_for_health(self):
        assert ChatJimmyClient.is_suitable_for("health") is False

    def test_all_suitable_task_types(self):
        for task in ["routing", "classification", "quick_lookup", "summarization",
                     "draft", "triage", "status_check"]:
            assert ChatJimmyClient.is_suitable_for(task) is True

    @pytest.mark.asyncio
    async def test_mock_stream(self, client):
        chunks = []
        async for chunk in client.chat_stream([{"role": "user", "content": "test"}]):
            chunks.append(chunk)
        assert len(chunks) > 0

    def test_mock_client_factory(self):
        c = mock_client()
        assert c._mock is True
        assert c._mock_latency_ms == 50.0

    @pytest.mark.asyncio
    async def test_response_not_certified(self, client):
        resp = await client.chat([{"role": "user", "content": "test"}])
        assert resp.certified is False
        assert resp.is_suitable_for_certification is False


class TestChatJimmyTimeoutError:
    def test_is_exception(self):
        err = ChatJimmyTimeoutError("timed out")
        assert isinstance(err, Exception)
        assert str(err) == "timed out"
