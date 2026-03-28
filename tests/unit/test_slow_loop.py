"""
Unit tests for core/hyperrouter/slow_loop.py — SlowLoop processes SwarmMessages and updates scores.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from core.hyperrouter.bandit import HyperRouter
from core.hyperrouter.slow_loop import SlowLoop
from core.hyperstate.schema import ModelScore


def _make_pool(fetch_return=None):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"id": uuid.uuid4()})
    conn.fetch = AsyncMock(return_value=fetch_return or [])
    conn.execute = AsyncMock()
    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    pool._conn = conn
    return pool


def _make_causal_graph():
    cg = MagicMock()
    cg.add_node = AsyncMock(return_value=uuid.uuid4())
    return cg


class TestSlowLoopRunOnce:
    @pytest.mark.asyncio
    async def test_empty_messages_returns_zero_processed(self):
        router = HyperRouter()
        pool = _make_pool(fetch_return=[])
        cg = _make_causal_graph()
        loop = SlowLoop(router, pool, cg)
        summary = await loop.run_once()
        assert summary["messages_processed"] == 0
        assert summary["agents_updated"] == 0
        assert summary["models_updated"] == 0

    @pytest.mark.asyncio
    async def test_processes_swarm_messages(self):
        messages = [
            {"agent_id": "agent-1", "model_used": "claude-sonnet-4-6", "task_type": "research", "certified": True},
            {"agent_id": "agent-1", "model_used": "claude-sonnet-4-6", "task_type": "research", "certified": False},
            {"agent_id": "agent-2", "model_used": "chatjimmy", "task_type": "routing", "certified": True},
        ]
        router = HyperRouter()
        pool = _make_pool(fetch_return=messages)
        cg = _make_causal_graph()
        loop = SlowLoop(router, pool, cg)
        summary = await loop.run_once()
        assert summary["messages_processed"] == 3
        assert summary["agents_updated"] == 2
        assert summary["models_updated"] == 2

    @pytest.mark.asyncio
    async def test_updates_router_model_scores(self):
        messages = [
            {"agent_id": "agent-1", "model_used": "claude-sonnet-4-6", "task_type": "research", "certified": True},
            {"agent_id": "agent-1", "model_used": "claude-sonnet-4-6", "task_type": "research", "certified": True},
        ]
        router = HyperRouter()
        pool = _make_pool(fetch_return=messages)
        cg = _make_causal_graph()
        loop = SlowLoop(router, pool, cg)
        await loop.run_once()
        # Router model scores should be updated
        assert "claude-sonnet-4-6" in router.model_scores
        assert "research" in router.model_scores["claude-sonnet-4-6"]
        assert router.model_scores["claude-sonnet-4-6"]["research"].attempts == 2
        assert router.model_scores["claude-sonnet-4-6"]["research"].successes == 2

    @pytest.mark.asyncio
    async def test_updates_agent_scores(self):
        messages = [
            {"agent_id": "my-agent", "model_used": "claude-sonnet-4-6", "task_type": "code", "certified": True},
        ]
        router = HyperRouter()
        pool = _make_pool(fetch_return=messages)
        cg = _make_causal_graph()
        loop = SlowLoop(router, pool, cg)
        await loop.run_once()
        assert "my-agent" in router.agent_scores
        assert router.agent_scores["my-agent"].attempts == 1
        assert router.agent_scores["my-agent"].successes == 1

    @pytest.mark.asyncio
    async def test_failure_increments_attempts_not_successes(self):
        messages = [
            {"agent_id": "agent-1", "model_used": "claude-sonnet-4-6", "task_type": "research", "certified": False},
        ]
        router = HyperRouter()
        pool = _make_pool(fetch_return=messages)
        cg = _make_causal_graph()
        loop = SlowLoop(router, pool, cg)
        await loop.run_once()
        score = router.model_scores["claude-sonnet-4-6"]["research"]
        assert score.attempts == 1
        assert score.successes == 0

    @pytest.mark.asyncio
    async def test_writes_to_causal_graph_when_messages_present(self):
        messages = [
            {"agent_id": "agent-1", "model_used": "chatjimmy", "task_type": "routing", "certified": True},
        ]
        router = HyperRouter()
        pool = _make_pool(fetch_return=messages)
        cg = _make_causal_graph()
        loop = SlowLoop(router, pool, cg)
        await loop.run_once()
        cg.add_node.assert_called_once()
        call_kwargs = cg.add_node.call_args
        assert call_kwargs[1]["node_type"] == "outcome"
        assert call_kwargs[1]["domain"] == "recursive"

    @pytest.mark.asyncio
    async def test_does_not_write_causal_graph_when_no_messages(self):
        router = HyperRouter()
        pool = _make_pool(fetch_return=[])
        cg = _make_causal_graph()
        loop = SlowLoop(router, pool, cg)
        await loop.run_once()
        cg.add_node.assert_not_called()

    @pytest.mark.asyncio
    async def test_summary_has_timestamp(self):
        pool = _make_pool(fetch_return=[])
        router = HyperRouter()
        cg = _make_causal_graph()
        loop = SlowLoop(router, pool, cg)
        summary = await loop.run_once()
        assert "timestamp" in summary
        assert summary["timestamp"] is not None


class TestCertifierCausalGraphIntegration:
    """Integration test: Certifier wires to CausalGraph on successful certification."""

    @pytest.mark.asyncio
    async def test_certification_triggers_causal_write(self):
        from core.hyperstate.certifier import Certifier
        from core.hyperstate.schema import ExperimentEntry
        import asyncio

        cg = MagicMock()
        written = {}

        async def fake_write(**kwargs):
            written.update(kwargs)
            return (uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

        cg.write_certified_method = AsyncMock(side_effect=fake_write)

        certifier = Certifier(causal_graph=cg)
        entry = ExperimentEntry(
            method="test_integration",
            model_used="claude-sonnet-4-6",
            result="42 passed",
            certified=True,
            test_trace="test_foo: PASS\ntest_bar: PASS",
        )

        cm = certifier.certify(entry, domain="scientific")
        assert cm.method_id == "test_integration"

        # Give event loop a tick to process the task
        await asyncio.sleep(0.01)
        cg.write_certified_method.assert_called_once()
        call_kwargs = cg.write_certified_method.call_args[1]
        assert call_kwargs["domain"] == "scientific"
        assert call_kwargs["confidence"] == 1.0
