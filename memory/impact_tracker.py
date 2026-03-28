"""
HyperMemory ImpactTracker — measures and persists business/scientific impact of agent actions.
"""
from __future__ import annotations

import logging
from uuid import UUID

import asyncpg

log = logging.getLogger("hyperclaw.impact_tracker")


class ImpactTracker:
    """
    Records measurable impact events and surfaces aggregated summaries.
    Writes to the impact_records table in HyperMemory.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record(
        self,
        domain: str,
        task: str,
        baseline_metric: str,
        baseline_value: float,
        outcome_metric: str,
        outcome_value: float,
        method_id: UUID | None = None,
        notes: str | None = None,
    ) -> UUID:
        """
        Record an impact event. Automatically computes delta and delta_pct.
        delta = outcome_value - baseline_value
        delta_pct = (delta / |baseline_value|) * 100 if baseline != 0 else 0
        """
        delta = outcome_value - baseline_value
        delta_pct = (delta / abs(baseline_value) * 100.0) if baseline_value != 0 else 0.0

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO impact_records
                    (domain, task, baseline_metric, baseline_value, outcome_metric,
                     outcome_value, delta, delta_pct, method_id, notes)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                RETURNING id
                """,
                domain, task, baseline_metric, baseline_value,
                outcome_metric, outcome_value, delta, delta_pct,
                method_id, notes,
            )
        log.info(f"ImpactTracker: recorded domain={domain} delta={delta:.4f} ({delta_pct:.1f}%)")
        return row["id"]

    async def get_summary(self, domain: str) -> dict:
        """
        Return aggregated impact summary for a domain.
        {domain, total_records, avg_improvement_pct, best_improvement, worst_improvement, records}
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, domain, task, baseline_metric, baseline_value,
                       outcome_metric, outcome_value, delta, delta_pct,
                       certified_at, method_id, notes
                FROM impact_records
                WHERE domain = $1
                ORDER BY certified_at DESC
                """,
                domain,
            )

        records = [dict(r) for r in rows]
        if not records:
            return {
                "domain": domain,
                "total_records": 0,
                "avg_improvement_pct": 0.0,
                "best_improvement": None,
                "worst_improvement": None,
                "records": [],
            }

        avg_pct = sum(r["delta_pct"] for r in records) / len(records)
        best = max(records, key=lambda r: r["delta_pct"])
        worst = min(records, key=lambda r: r["delta_pct"])

        return {
            "domain": domain,
            "total_records": len(records),
            "avg_improvement_pct": avg_pct,
            "best_improvement": best,
            "worst_improvement": worst,
            "records": records,
        }

    async def get_all_domains_summary(self) -> dict[str, dict]:
        """Return get_summary() for every domain with at least one record."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT domain FROM impact_records"
            )
        domains = [r["domain"] for r in rows]
        result: dict[str, dict] = {}
        for domain in domains:
            result[domain] = await self.get_summary(domain)
        return result

    async def get_recent(self, limit: int = 20) -> list[dict]:
        """Most recent impact records across all domains."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, domain, task, baseline_metric, baseline_value,
                       outcome_metric, outcome_value, delta, delta_pct,
                       certified_at, method_id, notes
                FROM impact_records
                ORDER BY certified_at DESC
                LIMIT $1
                """,
                limit,
            )
        return [dict(r) for r in rows]
