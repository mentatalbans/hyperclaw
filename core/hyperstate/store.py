"""
HyperState Store — asyncpg-backed persistence for HyperState objects.
Connection string loaded from DATABASE_URL env var or config/hyperclaw.yaml.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import asyncpg
import yaml

from .schema import HyperState


def _load_db_url() -> str:
    """Load DATABASE_URL from env or config/hyperclaw.yaml."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    config_path = os.path.join(os.path.dirname(__file__), "../../../config/hyperclaw.yaml")
    config_path = os.path.normpath(config_path)
    if os.path.exists(config_path):
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
        db_url = cfg.get("database", {}).get("url")
        if db_url:
            return db_url
    return "postgresql://localhost/hyperclaw"


class HyperStateStore:
    """
    Async persistence layer for HyperState. Uses asyncpg + PostgreSQL.
    pgvector extension assumed available for future vector fields.
    """

    def __init__(self, db_url: str | None = None) -> None:
        self._db_url = db_url or _load_db_url()
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(self._db_url, min_size=1, max_size=10)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    async def create_tables(self) -> None:
        """Create hyperstates and hyperstate_history tables if they don't exist."""
        assert self._pool, "Call connect() first"
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS hyperstates (
                    state_id        UUID PRIMARY KEY,
                    domain          TEXT NOT NULL,
                    data            JSONB NOT NULL,
                    state_version   INTEGER NOT NULL DEFAULT 0,
                    created_at      TIMESTAMPTZ NOT NULL,
                    updated_at      TIMESTAMPTZ NOT NULL,
                    archived_at     TIMESTAMPTZ
                );

                CREATE TABLE IF NOT EXISTS hyperstate_history (
                    id              BIGSERIAL PRIMARY KEY,
                    state_id        UUID NOT NULL REFERENCES hyperstates(state_id),
                    state_version   INTEGER NOT NULL,
                    data            JSONB NOT NULL,
                    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE INDEX IF NOT EXISTS idx_hyperstates_domain
                    ON hyperstates(domain);
                CREATE INDEX IF NOT EXISTS idx_hyperstate_history_state_id
                    ON hyperstate_history(state_id);
            """)

    async def save_state(self, state: HyperState) -> None:
        """Upsert a HyperState by state_id. Writes a history snapshot on each save."""
        assert self._pool, "Call connect() first"
        data = state.model_dump_json()
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("""
                    INSERT INTO hyperstates (state_id, domain, data, state_version, created_at, updated_at)
                    VALUES ($1, $2, $3::jsonb, $4, $5, $6)
                    ON CONFLICT (state_id) DO UPDATE SET
                        domain        = EXCLUDED.domain,
                        data            = EXCLUDED.data,
                        state_version = EXCLUDED.state_version,
                        updated_at    = EXCLUDED.updated_at
                """,
                    state.state_id,
                    state.domain,
                    data,
                    state.state_version,
                    state.created_at,
                    state.last_updated,
                )
                await conn.execute("""
                    INSERT INTO hyperstate_history (state_id, state_version, snapshot)
                    VALUES ($1, $2, $3::jsonb)
                """, state.state_id, state.state_version, data)

    async def load_state(self, state_id: UUID) -> HyperState:
        """Load a HyperState by state_id. Raises KeyError if not found."""
        assert self._pool, "Call connect() first"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT data FROM hyperstates WHERE state_id = $1", state_id
            )
        if row is None:
            raise KeyError(f"HyperState {state_id} not found")
        return HyperState.model_validate_json(row["data"])

    async def list_states(
        self,
        domain: Optional[str] = None,
        limit: int = 50,
    ) -> list[HyperState]:
        """List active (non-archived) HyperStates, optionally filtered by domain."""
        assert self._pool, "Call connect() first"
        if domain:
            rows = await self._pool.fetch(
                "SELECT data FROM hyperstates WHERE domain = $1 AND archived_at IS NULL "
                "ORDER BY updated_at DESC LIMIT $2",
                domain, limit,
            )
        else:
            rows = await self._pool.fetch(
                "SELECT data FROM hyperstates WHERE archived_at IS NULL "
                "ORDER BY updated_at DESC LIMIT $1",
                limit,
            )
        return [HyperState.model_validate_json(r["data"]) for r in rows]

    async def archive_state(self, state_id: UUID) -> None:
        """Set archived_at timestamp on a HyperState."""
        assert self._pool, "Call connect() first"
        await self._pool.execute(
            "UPDATE hyperstates SET archived_at = $1 WHERE state_id = $2",
            datetime.now(timezone.utc), state_id,
        )

    async def get_state_history(self, state_id: UUID) -> list[dict]:
        """Return all version snapshots for a HyperState."""
        assert self._pool, "Call connect() first"
        rows = await self._pool.fetch(
            "SELECT state_version, snapshot, created_at as recorded_at FROM hyperstate_history "
            "WHERE state_id = $1 ORDER BY state_version ASC",
            state_id,
        )
        return [
            {
                "state_version": r["state_version"],
                "recorded_at": r["recorded_at"].isoformat(),
                "state_data": json.loads(r["snapshot"]),
            }
            for r in rows
        ]
