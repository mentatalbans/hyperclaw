"""
SlowLoop — background optimization loop. Periodically rebalances routing weights
using longer-horizon performance data. Runs on a configurable interval.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from core.hyperstate.schema import HyperState

log = logging.getLogger("hyperclaw.slow_loop")


class SlowLoop:
    """
    Background loop that periodically reoptimizes HyperRouter weights.
    Runs every `interval_seconds` and updates state.routing_weights.
    """

    def __init__(self, interval_seconds: float = 300.0) -> None:
        self._interval = interval_seconds
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self, state: HyperState) -> None:
        """Start the background optimization loop."""
        self._running = True
        self._task = asyncio.create_task(self._loop(state))
        log.info(f"SlowLoop started — interval={self._interval}s")

    async def stop(self) -> None:
        """Stop the background loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("SlowLoop stopped")

    async def _loop(self, state: HyperState) -> None:
        while self._running:
            try:
                await self._optimize(state)
            except Exception as e:
                log.error(f"SlowLoop optimization error: {e}")
            await asyncio.sleep(self._interval)

    async def _optimize(self, state: HyperState) -> None:
        """
        Recompute routing_weights from model_scores across all task types.
        Uses normalized win rates as weights.
        """
        new_weights: dict[str, float] = {}
        for model_id, task_scores in state.model_scores.items():
            total_attempts = sum(s.attempts for s in task_scores.values())
            total_successes = sum(s.successes for s in task_scores.values())
            if total_attempts > 0:
                new_weights[model_id] = total_successes / total_attempts
            else:
                new_weights[model_id] = 0.5  # neutral prior

        # Normalize to sum to 1.0
        total = sum(new_weights.values())
        if total > 0:
            new_weights = {k: v / total for k, v in new_weights.items()}

        state.routing_weights = new_weights
        state._bump_version()
        log.debug(f"SlowLoop updated routing_weights: {new_weights}")
