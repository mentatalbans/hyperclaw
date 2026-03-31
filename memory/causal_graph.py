"""
memory/causal_graph.py — CausalGraph: cause→effect knowledge graph backed by asyncpg.

Nodes represent actions, outcomes, skills, etc.
Edges represent causal relationships with a confidence score [0.0, 1.0].
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Optional
from uuid import UUID


class CausalGraph:
    """Async causal knowledge graph backed by an asyncpg connection pool."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def add_node(
        self,
        label: str,
        node_type: str,
        domain: str,
        embedding: Optional[list] = None,
        metadata: Optional[dict] = None,
    ) -> UUID:
        """Insert a node into knowledge_nodes. Returns the new node UUID."""
        meta_json = json.dumps(metadata or {})

        if embedding is not None:
            sql = (
                "INSERT INTO knowledge_nodes (id, label, node_type, domain, embedding, metadata) "
                "VALUES ($1, $2, $3, $4, $5::vector, $6) RETURNING id"
            )
            args = (uuid.uuid4(), label, node_type, domain, embedding, meta_json)
        else:
            sql = (
                "INSERT INTO knowledge_nodes (id, label, node_type, domain, metadata) "
                "VALUES ($1, $2, $3, $4, $5) RETURNING id"
            )
            args = (uuid.uuid4(), label, node_type, domain, meta_json)

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, *args)
        return row["id"]

    async def add_edge(
        self,
        cause_id: UUID,
        effect_id: UUID,
        confidence: float,
        domain: str,
    ) -> UUID:
        """Insert a causal edge. confidence must be in [0.0, 1.0]."""
        if confidence < 0.0 or confidence > 1.0:
            raise ValueError(f"confidence must be between 0.0 and 1.0, got {confidence}")

        sql = (
            "INSERT INTO kg_facts (id, cause_id, effect_id, confidence, domain) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING id"
        )
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, uuid.uuid4(), cause_id, effect_id, confidence, domain)
        return row["id"]

    async def find_path(
        self,
        start_id: UUID,
        end_id: UUID,
        max_depth: int = 10,
    ) -> list[list[UUID]]:
        """BFS path-find from start_id to end_id. Returns list of paths (each a list of UUIDs)."""
        if start_id == end_id:
            return [[start_id]]

        sql = "SELECT cause_id, effect_id FROM kg_facts"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql)

        # Build adjacency map
        adj: dict[UUID, list[UUID]] = {}
        for row in rows:
            c, e = row["cause_id"], row["effect_id"]
            adj.setdefault(c, []).append(e)

        # BFS
        queue: list[list[UUID]] = [[start_id]]
        visited: set[UUID] = {start_id}
        found: list[list[UUID]] = []

        while queue:
            path = queue.pop(0)
            current = path[-1]
            if len(path) - 1 >= max_depth:
                continue
            for neighbor in adj.get(current, []):
                if neighbor == end_id:
                    found.append(path + [neighbor])
                elif neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(path + [neighbor])

        return found

    async def write_certified_method(
        self,
        method_description: str,
        cause_description: str,
        effect_description: str,
        domain: str,
        confidence: float,
        method_id: Optional[UUID] = None,
    ) -> tuple[UUID, UUID, UUID]:
        """Create a cause node, effect node, and edge. Returns (cause_id, effect_id, edge_id)."""
        cause_id = await self.add_node(cause_description, "action", domain)
        effect_id = await self.add_node(effect_description, "outcome", domain)
        edge_id = await self.add_edge(cause_id, effect_id, confidence, domain)
        return cause_id, effect_id, edge_id
