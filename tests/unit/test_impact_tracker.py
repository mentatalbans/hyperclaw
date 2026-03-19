"""
Unit tests for memory/impact_tracker.py — ImpactTracker delta computation and summaries.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from memory.impact_tracker import ImpactTracker


def _make_pool(fetchrow_return=None, fetch_return=None):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_return or {"id": uuid.uuid4()})
    conn.fetch = AsyncMock(return_value=fetch_return or [])
    conn.execute = AsyncMock()
    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    pool._conn = conn
    return pool


class TestImpactTrackerRecord:
    @pytest.mark.asyncio
    async def test_returns_uuid(self):
        rid = uuid.uuid4()
        pool = _make_pool(fetchrow_return={"id": rid})
        tracker = ImpactTracker(pool)
        result = await tracker.record(
            domain="business", task="improve revenue",
            baseline_metric="revenue", baseline_value=100.0,
            outcome_metric="revenue", outcome_value=120.0,
        )
        assert result == rid

    @pytest.mark.asyncio
    async def test_computes_positive_delta(self):
        pool = _make_pool()
        tracker = ImpactTracker(pool)
        await tracker.record("business", "task", "m", 100.0, "m", 150.0)
        call_args = pool._conn.fetchrow.call_args[0]
        # delta = 50, delta_pct = 50%
        assert 50.0 in call_args
        assert 50.0 in call_args  # delta_pct = 50.0%

    @pytest.mark.asyncio
    async def test_computes_negative_delta(self):
        pool = _make_pool()
        tracker = ImpactTracker(pool)
        await tracker.record("business", "task", "m", 100.0, "m", 80.0)
        call_args = pool._conn.fetchrow.call_args[0]
        assert -20.0 in call_args  # delta
        assert -20.0 in call_args  # delta_pct

    @pytest.mark.asyncio
    async def test_zero_baseline_handles_gracefully(self):
        pool = _make_pool()
        tracker = ImpactTracker(pool)
        # baseline=0 should not raise ZeroDivisionError
        await tracker.record("business", "task", "m", 0.0, "m", 50.0)
        call_args = pool._conn.fetchrow.call_args[0]
        assert 50.0 in call_args   # delta
        assert 0.0 in call_args    # delta_pct = 0 when baseline is 0

    @pytest.mark.asyncio
    async def test_delta_pct_calculation(self):
        pool = _make_pool()
        tracker = ImpactTracker(pool)
        await tracker.record("business", "task", "m", 200.0, "m", 250.0)
        call_args = pool._conn.fetchrow.call_args[0]
        # delta=50, delta_pct = 50/200 * 100 = 25%
        assert 50.0 in call_args
        assert 25.0 in call_args


class TestImpactTrackerGetSummary:
    def _make_record(self, delta_pct: float) -> dict:
        return {
            "id": uuid.uuid4(),
            "domain": "business",
            "task": "test",
            "baseline_metric": "m",
            "baseline_value": 100.0,
            "outcome_metric": "m",
            "outcome_value": 100.0 + delta_pct,
            "delta": delta_pct,
            "delta_pct": delta_pct,
            "certified_at": datetime.now(timezone.utc),
            "method_id": None,
            "notes": None,
        }

    @pytest.mark.asyncio
    async def test_empty_domain_returns_zeros(self):
        pool = _make_pool(fetch_return=[])
        tracker = ImpactTracker(pool)
        summary = await tracker.get_summary("empty_domain")
        assert summary["total_records"] == 0
        assert summary["avg_improvement_pct"] == 0.0
        assert summary["best_improvement"] is None
        assert summary["worst_improvement"] is None

    @pytest.mark.asyncio
    async def test_correct_aggregation(self):
        records = [self._make_record(10.0), self._make_record(20.0), self._make_record(30.0)]
        pool = _make_pool(fetch_return=records)
        tracker = ImpactTracker(pool)
        summary = await tracker.get_summary("business")
        assert summary["total_records"] == 3
        assert abs(summary["avg_improvement_pct"] - 20.0) < 0.01
        assert summary["best_improvement"]["delta_pct"] == 30.0
        assert summary["worst_improvement"]["delta_pct"] == 10.0

    @pytest.mark.asyncio
    async def test_domain_in_summary(self):
        pool = _make_pool(fetch_return=[])
        tracker = ImpactTracker(pool)
        summary = await tracker.get_summary("scientific")
        assert summary["domain"] == "scientific"

    @pytest.mark.asyncio
    async def test_records_list_included(self):
        records = [self._make_record(5.0), self._make_record(15.0)]
        pool = _make_pool(fetch_return=records)
        tracker = ImpactTracker(pool)
        summary = await tracker.get_summary("business")
        assert len(summary["records"]) == 2
