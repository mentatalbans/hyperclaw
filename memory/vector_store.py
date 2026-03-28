"""
HyperMemory VectorStore — pgvector-backed semantic search for HyperClaw agents.
"""
from __future__ import annotations

import json
import logging
from uuid import UUID

import asyncpg

log = logging.getLogger("hyperclaw.vector_store")


class VectorStore:
    """
    Cosine similarity search over knowledge_nodes using pgvector.
    Supports optional domain and node_type filters.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def upsert(
        self,
        id: UUID,
        embedding: list[float],
        metadata: dict,
        table: str = "knowledge_nodes",
    ) -> None:
        """
        Insert or update an embedding for a node.
        Updates embedding and metadata if id already exists.
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                UPDATE {table}
                SET embedding = $1::vector, metadata = $2::jsonb
                WHERE id = $3
                """,
                str(embedding),
                json.dumps(metadata),
                id,
            )

    async def search(
        self,
        embedding: list[float],
        top_k: int = 10,
        domain: str | None = None,
        node_type: str | None = None,
    ) -> list[dict]:
        """
        Cosine similarity search with optional domain/node_type filters.
        Returns [{id, label, similarity_score, metadata, node_type, domain}]
        ordered by similarity DESC.
        """
        conditions = ["embedding IS NOT NULL"]
        params: list = [str(embedding), top_k]
        idx = 3

        if domain is not None:
            conditions.append(f"domain = ${idx}")
            params.append(domain)
            idx += 1

        if node_type is not None:
            conditions.append(f"node_type = ${idx}")
            params.append(node_type)
            idx += 1

        where_clause = " AND ".join(conditions)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT id, label, node_type, domain, metadata,
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
                "label": r["label"],
                "node_type": r["node_type"],
                "domain": r["domain"],
                "metadata": r["metadata"],
                "similarity_score": r["similarity_score"],
            }
            for r in rows
        ]

    async def delete(self, id: UUID, table: str = "knowledge_nodes") -> None:
        """Delete a node by ID."""
        async with self._pool.acquire() as conn:
            await conn.execute(f"DELETE FROM {table} WHERE id = $1", id)

    async def count(self, domain: str | None = None) -> int:
        """Count knowledge nodes, optionally filtered by domain."""
        async with self._pool.acquire() as conn:
            if domain is not None:
                row = await conn.fetchrow(
                    "SELECT COUNT(*) FROM knowledge_nodes WHERE domain = $1", domain
                )
            else:
                row = await conn.fetchrow("SELECT COUNT(*) FROM knowledge_nodes")
        return row["count"]
