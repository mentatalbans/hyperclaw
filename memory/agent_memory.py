"""
HyperMemory AgentMemory — per-agent episodic, semantic, and procedural memory.
"""
from __future__ import annotations

import json
import logging
from uuid import UUID

import asyncpg

from .vector_store import VectorStore

log = logging.getLogger("hyperclaw.agent_memory")

MEMORY_TYPES = frozenset(["episodic", "semantic", "procedural"])


class AgentMemory:
    """
    Per-agent memory store backed by pgvector.
    Memories are stored as knowledge_nodes with node_type='agent' and
    metadata.agent_id identifying the owning agent.
    """

    def __init__(self, pool: asyncpg.Pool, vector_store: VectorStore) -> None:
        self._pool = pool
        self._vs = vector_store

    async def remember(
        self,
        agent_id: str,
        content: str,
        embedding: list[float],
        memory_type: str,
        metadata: dict | None = None,
    ) -> UUID:
        """
        Store a memory for an agent.
        memory_type: 'episodic' | 'semantic' | 'procedural'
        Returns the UUID of the new memory node.
        """
        if memory_type not in MEMORY_TYPES:
            raise ValueError(f"memory_type must be one of {MEMORY_TYPES}, got '{memory_type}'")

        meta = {**(metadata or {}), "agent_id": agent_id, "memory_type": memory_type}

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO knowledge_nodes (label, node_type, domain, embedding, metadata)
                VALUES ($1, 'agent', 'recursive', $2::vector, $3::jsonb)
                RETURNING id
                """,
                content,
                str(embedding),
                json.dumps(meta),
            )
        log.debug(f"AgentMemory: remembered for agent={agent_id} type={memory_type} id={row['id']}")
        return row["id"]

    async def recall(
        self,
        agent_id: str,
        query_embedding: list[float],
        top_k: int = 5,
        memory_type: str | None = None,
    ) -> list[dict]:
        """
        Similarity search filtered to this agent's memories.
        Optionally filter by memory_type.
        """
        conditions = [
            "embedding IS NOT NULL",
            "node_type = 'agent'",
            f"metadata->>'agent_id' = $3",
        ]
        params: list = [str(query_embedding), top_k, agent_id]
        idx = 4

        if memory_type is not None:
            conditions.append(f"metadata->>'memory_type' = ${idx}")
            params.append(memory_type)

        where_clause = " AND ".join(conditions)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT id, label, metadata,
                       1 - (embedding <=> $1::vector) AS similarity_score
                FROM knowledge_nodes
                WHERE {where_clause}
                ORDER BY embedding <=> $1::vector
                LIMIT $2
                """,
                *params,
            )

        return [
            {
                "id": r["id"],
                "content": r["label"],
                "metadata": r["metadata"],
                "similarity_score": r["similarity_score"],
            }
            for r in rows
        ]

    async def forget(self, memory_id: UUID) -> None:
        """Delete a specific memory by UUID."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM knowledge_nodes WHERE id = $1 AND node_type = 'agent'",
                memory_id,
            )

    async def recall_recent(self, agent_id: str, limit: int = 10) -> list[dict]:
        """Most recent memories for this agent, ordered by created_at DESC."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, label, metadata, created_at
                FROM knowledge_nodes
                WHERE node_type = 'agent' AND metadata->>'agent_id' = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                agent_id, limit,
            )
        return [
            {
                "id": r["id"],
                "content": r["label"],
                "metadata": r["metadata"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]
