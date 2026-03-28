"""
HyperClaw EngramLite — Lightweight episodic memory for NEXUS.
Stores completed orchestration tasks and allows simple text-based recall.
No vector embeddings needed for MVP — plain text search via ILIKE.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Optional

import asyncpg

log = logging.getLogger("hyperclaw.engram_lite")

# Reuse the same Supabase connection the rest of HyperClaw uses
DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:nwpSK8rGCCVIkOgV@db.truponyrnbaeplimswhl.supabase.co:5432/postgres",
)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS hyperclaw_episodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    goal TEXT NOT NULL,
    result TEXT,
    domain TEXT,
    agents_used JSONB DEFAULT '[]',
    source_system TEXT DEFAULT 'nexus',
    is_core BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""


class EngramLite:
    """
    Simplified episodic memory for NEXUS.
    Each completed orchestration task is stored as an episode.
    Recall uses simple ILIKE text search — no vectors needed.
    """

    def __init__(self, pool: Optional[asyncpg.Pool] = None) -> None:
        self._pool = pool
        self._loop = asyncio.new_event_loop()

    # ── Connection management ─────────────────────────────────────────────────

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=3)
        return self._pool

    async def _ensure_table(self) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(CREATE_TABLE_SQL)

    # ── Async core methods ────────────────────────────────────────────────────

    async def _remember_async(
        self,
        goal: str,
        result: Optional[str],
        domain: Optional[str],
        agents_used: list[str],
        source_system: str = "nexus",
    ) -> str:
        """Store a completed task. Returns episode ID."""
        await self._ensure_table()
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO hyperclaw_episodes (goal, result, domain, agents_used, source_system)
                VALUES ($1, $2, $3, $4::jsonb, $5)
                RETURNING id::text
                """,
                goal,
                result,
                domain,
                json.dumps(agents_used),
                source_system,
            )
        log.info(f"EngramLite: stored episode {row['id']} — goal='{goal[:60]}'")
        return row["id"]

    async def _recall_async(self, query: str, limit: int = 10) -> list[dict]:
        """Find past episodes whose goal or result contains the query (case-insensitive)."""
        await self._ensure_table()
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id::text, goal, result, domain, agents_used, source_system, is_core, created_at
                FROM hyperclaw_episodes
                WHERE goal ILIKE $1
                   OR result ILIKE $1
                   OR domain ILIKE $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                f"%{query}%",
                limit,
            )
        return [dict(r) for r in rows]

    async def _mark_core_async(self, episode_id: str) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE hyperclaw_episodes SET is_core = TRUE WHERE id = $1::uuid",
                episode_id,
            )

    async def _get_core_async(self, limit: int = 50) -> list[dict]:
        await self._ensure_table()
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id::text, goal, result, domain, agents_used, source_system, is_core, created_at
                FROM hyperclaw_episodes
                WHERE is_core = TRUE
                ORDER BY created_at DESC
                LIMIT $1
                """,
                limit,
            )
        return [dict(r) for r in rows]

    # ── Sync public API (matches task spec) ──────────────────────────────────

    def remember(
        self,
        goal: str,
        result: Optional[str] = None,
        domain: Optional[str] = None,
        agents_used: Optional[list[str]] = None,
        source_system: str = "nexus",
    ) -> str:
        """Store a completed task episode. Returns episode ID."""
        return self._loop.run_until_complete(
            self._remember_async(goal, result, domain, agents_used or [], source_system)
        )

    def recall(self, query: str, limit: int = 10) -> list[dict]:
        """Find past episodes matching query (simple text search)."""
        return self._loop.run_until_complete(self._recall_async(query, limit))

    def mark_core(self, episode_id: str) -> None:
        """Mark an episode as core (protected from pruning)."""
        self._loop.run_until_complete(self._mark_core_async(episode_id))

    def get_core(self, limit: int = 50) -> list[dict]:
        """Retrieve all core episodes."""
        return self._loop.run_until_complete(self._get_core_async(limit))

    def close(self) -> None:
        if self._pool:
            self._loop.run_until_complete(self._pool.close())
