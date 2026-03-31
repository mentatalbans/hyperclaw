"""
memory/impact_tracker.py — ImpactTracker: records before/after deltas and summarises domain-level impact.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional
from uuid import UUID


class ImpactTracker:
    """Track measurable deltas produced by Assistant actions, backed by asyncpg pool."""

    TABLE = "episodic_memories"  # re-uses existing Supabase table; override as needed

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def record(
        self,
        domain: str,
        task: str,
        baseline_metric: str,
        baseline_value: float,
        outcome_metric: str,
        outcome_value: float,
        notes: Optional[str] = None,
        method_id: Optional[UUID] = None,
    ) -> UUID:
        """Record an impact delta. Returns the new record UUID."""
        delta = outcome_value - baseline_value
        delta_pct = (delta / baseline_value * 100.0) if baseline_value != 0.0 else 0.0

        sql = (
            "INSERT INTO impact_records "
            "(id, domain, task, baseline_metric, baseline_value, "
            " outcome_metric, outcome_value, delta, delta_pct, notes, method_id) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) RETURNING id"
        )
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                sql,
                uuid.uuid4(),
                domain,
                task,
                baseline_metric,
                baseline_value,
                outcome_metric,
                outcome_value,
                delta,
                delta_pct,
                notes,
                method_id,
            )
        return row["id"]

    async def get_summary(self, domain: str) -> dict:
        """Return aggregated impact stats for a domain."""
        sql = "SELECT * FROM impact_records WHERE domain = $1"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, domain)

        if not rows:
            return {
                "domain": domain,
                "total_records": 0,
                "avg_improvement_pct": 0.0,
                "best_improvement": None,
                "worst_improvement": None,
                "records": [],
            }

        records = [dict(r) for r in rows]
        pcts = [r["delta_pct"] for r in records]
        best = max(records, key=lambda r: r["delta_pct"])
        worst = min(records, key=lambda r: r["delta_pct"])

        return {
            "domain": domain,
            "total_records": len(records),
            "avg_improvement_pct": sum(pcts) / len(pcts),
            "best_improvement": best,
            "worst_improvement": worst,
            "records": records,
        }
