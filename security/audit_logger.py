"""
HyperShield AuditLogger — writes immutable audit events to HyperMemory (audit_log table).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg

log = logging.getLogger("hyperclaw.audit_logger")

EVENT_TYPES = frozenset([
    "inference_call", "network_request", "file_access", "policy_reload",
])


class AuditLogger:
    """
    Async audit logger. All events written to the audit_log table.
    Provides queries for recent events, blocked events, and per-agent activity.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def log(
        self,
        event_type: str,
        action: str,
        allowed: bool,
        agent_id: Optional[str] = None,
        model_used: Optional[str] = None,
        target: Optional[str] = None,
        policy_applied: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """Write an audit event to the audit_log table."""
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO audit_log
                        (event_type, agent_id, model_used, action, target,
                         allowed, policy_applied, metadata)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                    """,
                    event_type, agent_id, model_used, action, target,
                    allowed, policy_applied,
                    json.dumps(metadata or {}),
                )
        except Exception as e:
            log.error(f"AuditLogger write failed: {e}")

    async def get_recent(self, limit: int = 100) -> list[dict]:
        """Return the most recent audit events."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, event_type, agent_id, model_used, action, target,
                       allowed, policy_applied, metadata, created_at
                FROM audit_log
                ORDER BY created_at DESC
                LIMIT $1
                """,
                limit,
            )
        return [dict(r) for r in rows]

    async def get_blocked_events(self, since_hours: int = 24) -> list[dict]:
        """Return all blocked (allowed=False) events within the last since_hours."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, event_type, agent_id, model_used, action, target,
                       allowed, policy_applied, metadata, created_at
                FROM audit_log
                WHERE allowed = false AND created_at > $1
                ORDER BY created_at DESC
                """,
                cutoff,
            )
        return [dict(r) for r in rows]

    async def get_agent_activity(
        self,
        agent_id: str,
        since_hours: int = 24,
    ) -> list[dict]:
        """Return all audit events for a specific agent within the last since_hours."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, event_type, agent_id, model_used, action, target,
                       allowed, policy_applied, metadata, created_at
                FROM audit_log
                WHERE agent_id = $1 AND created_at > $2
                ORDER BY created_at DESC
                """,
                agent_id, cutoff,
            )
        return [dict(r) for r in rows]
