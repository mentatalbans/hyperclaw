"""
HyperMemory Migration Runner — executes SQL migrations in order, tracks completion.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import asyncpg

log = logging.getLogger("hyperclaw.migrations")

MIGRATIONS_DIR = Path(__file__).parent
TRACKING_TABLE = "_migrations"


async def run_migrations(pool: asyncpg.Pool) -> list[str]:
    """
    Run all pending SQL migrations from the migrations/ directory.

    Creates a _migrations tracking table if not present.
    Only runs each migration once (idempotent).
    Returns list of migration filenames that were executed.
    """
    async with pool.acquire() as conn:
        # Create tracking table
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {TRACKING_TABLE} (
                filename    TEXT PRIMARY KEY,
                applied_at  TIMESTAMPTZ DEFAULT now()
            )
        """)

        # Get already-applied migrations
        applied = {
            r["filename"]
            for r in await conn.fetch(f"SELECT filename FROM {TRACKING_TABLE}")
        }

        # Collect and sort .sql files
        sql_files = sorted(
            f for f in MIGRATIONS_DIR.iterdir()
            if f.suffix == ".sql"
        )

        executed: list[str] = []
        for sql_file in sql_files:
            if sql_file.name in applied:
                log.debug(f"Migration already applied: {sql_file.name}")
                continue

            log.info(f"Running migration: {sql_file.name}")
            sql = sql_file.read_text()

            try:
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        f"INSERT INTO {TRACKING_TABLE} (filename) VALUES ($1)",
                        sql_file.name,
                    )
                executed.append(sql_file.name)
                log.info(f"Migration complete: {sql_file.name}")
            except Exception as e:
                log.error(f"Migration failed: {sql_file.name} — {e}")
                raise

        return executed
