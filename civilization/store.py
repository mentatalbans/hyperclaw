"""
CivilizationStore — all DB operations for civilization nodes.
Wraps asyncpg pool (or stub when pool=None for testing).
"""
from __future__ import annotations
import json
import logging
from uuid import UUID
from datetime import datetime
from typing import Any

from .schema import CivilizationNode, NodeType, NodeStatus

logger = logging.getLogger(__name__)


class CivilizationStore:
    def __init__(self, pool=None):
        self.pool = pool  # asyncpg pool; None = no-DB mode (tests)

    async def save(self, node: CivilizationNode) -> CivilizationNode:
        """Upsert a node into civilization_nodes."""
        if not self.pool:
            return node
        content = node.model_dump(mode="json", exclude={"embedding"})
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO civilization_nodes
                    (id, org_id, node_type, title, status, version, owner_id,
                     tags, source, source_url, content, embedding, metadata,
                     created_at, updated_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::vector,$13,$14,$15)
                ON CONFLICT (id) DO UPDATE SET
                    title=EXCLUDED.title, status=EXCLUDED.status,
                    version=EXCLUDED.version, content=EXCLUDED.content,
                    embedding=EXCLUDED.embedding, updated_at=EXCLUDED.updated_at
            """,
            node.id, node.org_id, node.node_type.value, node.title,
            node.status.value, node.version, node.owner_id,
            node.tags, node.source, node.source_url,
            json.dumps(content),
            node.embedding,
            json.dumps(node.metadata),
            node.created_at, node.updated_at)
        return node

    async def get(self, node_id: UUID, org_id: str) -> CivilizationNode | None:
        if not self.pool:
            return None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM civilization_nodes WHERE id=$1 AND org_id=$2",
                node_id, org_id
            )
        if not row:
            return None
        return self._row_to_node(row)

    async def list_by_type(self, org_id: str, node_type: NodeType,
                           limit: int = 100) -> list[CivilizationNode]:
        if not self.pool:
            return []
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM civilization_nodes WHERE org_id=$1 AND node_type=$2 LIMIT $3",
                org_id, node_type.value, limit
            )
        return [self._row_to_node(r) for r in rows]

    async def list_all(self, org_id: str, limit: int = 500) -> list[CivilizationNode]:
        if not self.pool:
            return []
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM civilization_nodes WHERE org_id=$1 LIMIT $2",
                org_id, limit
            )
        return [self._row_to_node(r) for r in rows]

    async def search_by_embedding(self, org_id: str, embedding: list[float],
                                   top_k: int = 10,
                                   node_types: list[NodeType] | None = None) -> list[dict]:
        """Vector similarity search."""
        if not self.pool:
            return []
        type_filter = ""
        args = [org_id, embedding, top_k]
        if node_types:
            placeholders = ",".join(f"${i+4}" for i in range(len(node_types)))
            type_filter = f"AND node_type IN ({placeholders})"
            args.extend([t.value for t in node_types])
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(f"""
                SELECT *, 1 - (embedding <=> $2::vector) AS score
                FROM civilization_nodes
                WHERE org_id=$1 {type_filter}
                ORDER BY embedding <=> $2::vector
                LIMIT $3
            """, *args)
        return [dict(r) for r in rows]

    async def search_by_tags(self, org_id: str, tags: list[str],
                             limit: int = 50) -> list[CivilizationNode]:
        """Find nodes that have any of the specified tags."""
        if not self.pool:
            return []
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM civilization_nodes WHERE org_id=$1 AND tags && $2 LIMIT $3",
                org_id, tags, limit
            )
        return [self._row_to_node(r) for r in rows]

    async def search_by_owner(self, org_id: str, owner_id: str,
                              limit: int = 100) -> list[CivilizationNode]:
        """Find all nodes owned by a specific person."""
        if not self.pool:
            return []
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM civilization_nodes WHERE org_id=$1 AND owner_id=$2 LIMIT $3",
                org_id, owner_id, limit
            )
        return [self._row_to_node(r) for r in rows]

    async def count_by_type(self, org_id: str) -> dict[str, int]:
        if not self.pool:
            return {}
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT node_type, COUNT(*) as cnt FROM civilization_nodes WHERE org_id=$1 GROUP BY node_type",
                org_id
            )
        return {r["node_type"]: r["cnt"] for r in rows}

    async def delete(self, node_id: UUID, org_id: str) -> bool:
        if not self.pool:
            return True
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM civilization_nodes WHERE id=$1 AND org_id=$2",
                node_id, org_id
            )
        return result == "DELETE 1"

    async def update_status(self, node_id: UUID, org_id: str,
                            new_status: NodeStatus) -> bool:
        """Update a node's status."""
        if not self.pool:
            return True
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE civilization_nodes SET status=$3, updated_at=$4 WHERE id=$1 AND org_id=$2",
                node_id, org_id, new_status.value, datetime.utcnow()
            )
        return "UPDATE 1" in result

    async def update_embedding(self, node_id: UUID, org_id: str,
                               embedding: list[float]) -> bool:
        """Update a node's embedding vector."""
        if not self.pool:
            return True
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE civilization_nodes SET embedding=$3::vector, updated_at=$4 WHERE id=$1 AND org_id=$2",
                node_id, org_id, embedding, datetime.utcnow()
            )
        return "UPDATE 1" in result

    def _row_to_node(self, row) -> CivilizationNode:
        content = json.loads(row["content"]) if isinstance(row["content"], str) else row["content"]
        return CivilizationNode(**{**content, "id": row["id"], "org_id": row["org_id"]})
