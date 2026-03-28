"""
HyperMemory CausalGraph — knowledge backbone for the HyperClaw swarm.
Stores cause→effect relationships from every certified method.
"""
from __future__ import annotations

import json
import logging
from collections import deque
from uuid import UUID

import asyncpg

log = logging.getLogger("hyperclaw.causal_graph")


class CausalGraph:
    """
    Directed causal graph stored in PostgreSQL.
    Every certified method writes a cause→effect edge here.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def add_node(
        self,
        label: str,
        node_type: str,
        domain: str,
        embedding: list[float] | None = None,
        metadata: dict | None = None,
    ) -> UUID:
        """
        Add a knowledge node. Returns the new node's UUID.
        node_type: action | outcome | condition | agent | discovery | skill
        """
        async with self._pool.acquire() as conn:
            if embedding is not None:
                row = await conn.fetchrow(
                    """
                    INSERT INTO knowledge_nodes (label, node_type, domain, embedding, metadata)
                    VALUES ($1, $2, $3, $4::vector, $5::jsonb)
                    RETURNING id
                    """,
                    label, node_type, domain,
                    str(embedding),
                    json.dumps(metadata or {}),
                )
            else:
                row = await conn.fetchrow(
                    """
                    INSERT INTO knowledge_nodes (label, node_type, domain, metadata)
                    VALUES ($1, $2, $3, $4::jsonb)
                    RETURNING id
                    """,
                    label, node_type, domain,
                    json.dumps(metadata or {}),
                )
        return row["id"]

    async def add_edge(
        self,
        cause_id: UUID,
        effect_id: UUID,
        confidence: float,
        domain: str,
        method_id: UUID | None = None,
        context: dict | None = None,
    ) -> UUID:
        """
        Add a causal edge. confidence must be in [0.0, 1.0].
        Raises ValueError if confidence out of range.
        """
        if not (0.0 <= confidence <= 1.0):
            raise ValueError(f"confidence must be in [0.0, 1.0], got {confidence}")

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO causal_edges (cause_id, effect_id, confidence, domain, method_id, context)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                RETURNING id
                """,
                cause_id, effect_id, confidence, domain,
                method_id,
                json.dumps(context or {}),
            )
        return row["id"]

    async def get_effects(self, cause_id: UUID) -> list[dict]:
        """Return all nodes this cause leads to, ordered by confidence DESC."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT n.id, n.label, n.node_type, n.domain, n.metadata,
                       e.confidence, e.id as edge_id
                FROM causal_edges e
                JOIN knowledge_nodes n ON n.id = e.effect_id
                WHERE e.cause_id = $1
                ORDER BY e.confidence DESC
                """,
                cause_id,
            )
        return [dict(r) for r in rows]

    async def get_causes(self, effect_id: UUID) -> list[dict]:
        """Return all nodes that lead to this effect, ordered by confidence DESC."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT n.id, n.label, n.node_type, n.domain, n.metadata,
                       e.confidence, e.id as edge_id
                FROM causal_edges e
                JOIN knowledge_nodes n ON n.id = e.cause_id
                WHERE e.effect_id = $1
                ORDER BY e.confidence DESC
                """,
                effect_id,
            )
        return [dict(r) for r in rows]

    async def find_path(
        self,
        start_id: UUID,
        end_id: UUID,
        max_depth: int = 5,
    ) -> list[list[UUID]]:
        """
        BFS over causal edges. Returns all paths from start to end up to max_depth.
        Returns empty list if no path found.
        """
        if start_id == end_id:
            return [[start_id]]

        # Load all edges into memory for BFS (efficient for small/medium graphs)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT cause_id, effect_id FROM causal_edges"
            )

        # Build adjacency list
        adjacency: dict[UUID, list[UUID]] = {}
        for row in rows:
            c, e = row["cause_id"], row["effect_id"]
            adjacency.setdefault(c, []).append(e)

        # BFS
        paths: list[list[UUID]] = []
        queue: deque[list[UUID]] = deque([[start_id]])

        while queue:
            path = queue.popleft()
            if len(path) > max_depth:
                continue
            current = path[-1]
            for neighbor in adjacency.get(current, []):
                new_path = path + [neighbor]
                if neighbor == end_id:
                    paths.append(new_path)
                elif neighbor not in path:  # avoid cycles
                    queue.append(new_path)

        return paths

    async def search_similar(
        self,
        embedding: list[float],
        domain: str,
        top_k: int = 10,
    ) -> list[dict]:
        """
        pgvector cosine similarity search within a domain.
        Returns [{id, label, node_type, similarity_score, metadata}]
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, label, node_type, metadata,
                       1 - (embedding <=> $1::vector) AS similarity_score
                FROM knowledge_nodes
                WHERE domain = $2 AND embedding IS NOT NULL
                ORDER BY embedding <=> $1::vector
                LIMIT $3
                """,
                str(embedding), domain, top_k,
            )
        return [
            {
                "id": r["id"],
                "label": r["label"],
                "node_type": r["node_type"],
                "similarity_score": r["similarity_score"],
                "metadata": r["metadata"],
            }
            for r in rows
        ]

    async def write_certified_method(
        self,
        method_description: str,
        cause_description: str,
        effect_description: str,
        domain: str,
        confidence: float,
        method_id: UUID,
        embedding: list[float] | None = None,
    ) -> tuple[UUID, UUID, UUID]:
        """
        High-level helper: creates cause node + effect node + causal edge.
        Returns (cause_id, effect_id, edge_id).
        Called automatically by Certifier on every successful certification.
        """
        cause_id = await self.add_node(
            label=cause_description,
            node_type="action",
            domain=domain,
            embedding=embedding,
            metadata={"method_id": str(method_id), "source": "certifier"},
        )
        effect_id = await self.add_node(
            label=effect_description,
            node_type="outcome",
            domain=domain,
            metadata={"method_id": str(method_id), "source": "certifier"},
        )
        edge_id = await self.add_edge(
            cause_id=cause_id,
            effect_id=effect_id,
            confidence=confidence,
            domain=domain,
            method_id=method_id,
        )
        log.info(
            f"CausalGraph: certified method written — "
            f"cause={cause_id} effect={effect_id} edge={edge_id} confidence={confidence}"
        )
        return cause_id, effect_id, edge_id
