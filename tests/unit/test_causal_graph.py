"""
Unit tests for memory/causal_graph.py — CausalGraph with mock asyncpg pool.
"""
from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from memory.causal_graph import CausalGraph


def _make_pool(fetchrow_return=None, fetch_return=None):
    """Build a mock asyncpg pool."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    conn.fetch = AsyncMock(return_value=fetch_return or [])
    conn.execute = AsyncMock()

    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    pool._conn = conn  # expose for assertions
    return pool


class TestCausalGraphAddNode:
    @pytest.mark.asyncio
    async def test_returns_uuid(self):
        node_id = uuid.uuid4()
        pool = _make_pool(fetchrow_return={"id": node_id})
        graph = CausalGraph(pool)
        result = await graph.add_node("test action", "action", "business")
        assert result == node_id

    @pytest.mark.asyncio
    async def test_calls_insert(self):
        node_id = uuid.uuid4()
        pool = _make_pool(fetchrow_return={"id": node_id})
        graph = CausalGraph(pool)
        await graph.add_node("label", "outcome", "scientific")
        pool._conn.fetchrow.assert_called_once()
        args = pool._conn.fetchrow.call_args[0]
        assert "INSERT INTO knowledge_nodes" in args[0]

    @pytest.mark.asyncio
    async def test_with_embedding(self):
        node_id = uuid.uuid4()
        pool = _make_pool(fetchrow_return={"id": node_id})
        graph = CausalGraph(pool)
        embedding = [0.1] * 1536
        result = await graph.add_node("label", "action", "business", embedding=embedding)
        assert result == node_id
        # Should call the embedding variant
        call_sql = pool._conn.fetchrow.call_args[0][0]
        assert "vector" in call_sql

    @pytest.mark.asyncio
    async def test_with_metadata(self):
        node_id = uuid.uuid4()
        pool = _make_pool(fetchrow_return={"id": node_id})
        graph = CausalGraph(pool)
        result = await graph.add_node("label", "skill", "recursive", metadata={"key": "value"})
        assert result == node_id


class TestCausalGraphAddEdge:
    @pytest.mark.asyncio
    async def test_returns_uuid(self):
        edge_id = uuid.uuid4()
        pool = _make_pool(fetchrow_return={"id": edge_id})
        graph = CausalGraph(pool)
        cause = uuid.uuid4()
        effect = uuid.uuid4()
        result = await graph.add_edge(cause, effect, 0.9, "business")
        assert result == edge_id

    @pytest.mark.asyncio
    async def test_rejects_confidence_above_1(self):
        pool = _make_pool()
        graph = CausalGraph(pool)
        with pytest.raises(ValueError, match="confidence"):
            await graph.add_edge(uuid.uuid4(), uuid.uuid4(), 1.1, "business")

    @pytest.mark.asyncio
    async def test_rejects_confidence_below_0(self):
        pool = _make_pool()
        graph = CausalGraph(pool)
        with pytest.raises(ValueError, match="confidence"):
            await graph.add_edge(uuid.uuid4(), uuid.uuid4(), -0.1, "business")

    @pytest.mark.asyncio
    async def test_accepts_boundary_values(self):
        edge_id = uuid.uuid4()
        pool = _make_pool(fetchrow_return={"id": edge_id})
        graph = CausalGraph(pool)
        cause, effect = uuid.uuid4(), uuid.uuid4()
        # Should not raise for exactly 0.0 and 1.0
        r1 = await graph.add_edge(cause, effect, 0.0, "business")
        assert r1 == edge_id
        r2 = await graph.add_edge(cause, effect, 1.0, "business")
        assert r2 == edge_id


class TestCausalGraphFindPath:
    @pytest.mark.asyncio
    async def test_same_start_end_returns_trivial_path(self):
        pool = _make_pool(fetch_return=[])
        graph = CausalGraph(pool)
        nid = uuid.uuid4()
        paths = await graph.find_path(nid, nid)
        assert paths == [[nid]]

    @pytest.mark.asyncio
    async def test_disconnected_returns_empty(self):
        pool = _make_pool(fetch_return=[])
        graph = CausalGraph(pool)
        paths = await graph.find_path(uuid.uuid4(), uuid.uuid4())
        assert paths == []

    @pytest.mark.asyncio
    async def test_finds_direct_path(self):
        a, b = uuid.uuid4(), uuid.uuid4()
        pool = _make_pool(fetch_return=[
            {"cause_id": a, "effect_id": b}
        ])
        graph = CausalGraph(pool)
        paths = await graph.find_path(a, b)
        assert len(paths) == 1
        assert paths[0] == [a, b]

    @pytest.mark.asyncio
    async def test_finds_3_node_path(self):
        a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        pool = _make_pool(fetch_return=[
            {"cause_id": a, "effect_id": b},
            {"cause_id": b, "effect_id": c},
        ])
        graph = CausalGraph(pool)
        paths = await graph.find_path(a, c)
        assert len(paths) == 1
        assert paths[0] == [a, b, c]

    @pytest.mark.asyncio
    async def test_respects_max_depth(self):
        # Create a long chain a→b→c→d→e
        ids = [uuid.uuid4() for _ in range(5)]
        edges = [{"cause_id": ids[i], "effect_id": ids[i+1]} for i in range(4)]
        pool = _make_pool(fetch_return=edges)
        graph = CausalGraph(pool)
        # max_depth=2 should not find a path of length 4
        paths = await graph.find_path(ids[0], ids[4], max_depth=2)
        assert paths == []


class TestWriteCertifiedMethod:
    @pytest.mark.asyncio
    async def test_creates_two_nodes_and_one_edge(self):
        cause_id = uuid.uuid4()
        effect_id = uuid.uuid4()
        edge_id = uuid.uuid4()

        # fetchrow called 3 times: add_node x2, add_edge x1
        pool = MagicMock()
        conn = AsyncMock()
        call_count = {"n": 0}

        async def fetchrow_side_effect(*args, **kwargs):
            n = call_count["n"]
            call_count["n"] += 1
            if n == 0:
                return {"id": cause_id}
            elif n == 1:
                return {"id": effect_id}
            else:
                return {"id": edge_id}

        conn.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
        conn.fetch = AsyncMock(return_value=[])
        conn.execute = AsyncMock()
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        graph = CausalGraph(pool)
        method_id = uuid.uuid4()
        result = await graph.write_certified_method(
            method_description="test method",
            cause_description="cause: test",
            effect_description="effect: test",
            domain="business",
            confidence=0.9,
            method_id=method_id,
        )
        assert result == (cause_id, effect_id, edge_id)
        assert call_count["n"] == 3

    @pytest.mark.asyncio
    async def test_returns_correct_tuple_order(self):
        ids = [uuid.uuid4() for _ in range(3)]
        idx = {"n": 0}

        pool = MagicMock()
        conn = AsyncMock()

        async def fetchrow_se(*a, **kw):
            r = {"id": ids[idx["n"]]}
            idx["n"] += 1
            return r

        conn.fetchrow = AsyncMock(side_effect=fetchrow_se)
        conn.fetch = AsyncMock(return_value=[])
        conn.execute = AsyncMock()
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        graph = CausalGraph(pool)
        cause_id, effect_id, edge_id = await graph.write_certified_method(
            "m", "c", "e", "business", 1.0, uuid.uuid4()
        )
        assert cause_id == ids[0]
        assert effect_id == ids[1]
        assert edge_id == ids[2]
