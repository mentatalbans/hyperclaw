"""
SlowLoop — background optimization loop. Periodically re-reads SwarmMessage history
from the DB, rebalances UCB1 scores, and writes routing performance summaries to CausalGraph.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import asyncpg

from .bandit import HyperRouter, update_scores
from core.hyperstate.schema import AgentScore, ModelScore

if TYPE_CHECKING:
    from memory.causal_graph import CausalGraph

log = logging.getLogger("hyperclaw.slow_loop")


class SlowLoop:
    """
    Background optimization loop.

    Every interval_hours:
    1. Reads all SwarmMessages from DB since last run
    2. Recalculates UCB1 scores per agent/model per task_type
    3. Updates HyperRouter in-place
    4. Writes routing performance summary to CausalGraph (node_type='outcome')
    5. Returns summary dict
    """

    def __init__(
        self,
        hyper_router: HyperRouter,
        pool: asyncpg.Pool,
        causal_graph: "CausalGraph",
    ) -> None:
        self._router = hyper_router
        self._pool = pool
        self._causal_graph = causal_graph
        self._last_run: datetime | None = None

    async def run_once(self) -> dict:
        """
        Process SwarmMessages since last run, update router scores.
        Returns summary: {messages_processed, agents_updated, models_updated, timestamp}
        """
        since = self._last_run or datetime(2020, 1, 1, tzinfo=timezone.utc)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT agent_id, model_used, task_type, certified
                FROM swarm_messages
                WHERE created_at > $1
                ORDER BY created_at ASC
                """,
                since,
            )

        agents_seen: set[str] = set()
        models_seen: set[str] = set()
        messages_processed = 0

        new_agent_scores: dict = dict(self._router.agent_scores)
        new_model_scores: dict = dict(self._router.model_scores)
        new_total = self._router.total_attempts

        for row in rows:
            agent_id = row["agent_id"]
            model_id = row["model_used"]
            task_type = row["task_type"]
            # Treat certified=True as success; False as failure
            success = bool(row["certified"])

            # Update agent scores
            if agent_id not in new_agent_scores:
                new_agent_scores[agent_id] = AgentScore(task_type=task_type)
            new_agent_scores[agent_id].attempts += 1
            if success:
                new_agent_scores[agent_id].successes += 1
            agents_seen.add(agent_id)

            # Update model scores (immutable pattern)
            new_model_scores = update_scores(new_model_scores, model_id, task_type, success)
            models_seen.add(model_id)
            new_total += 1
            messages_processed += 1

        # Apply updated scores to router
        self._router.agent_scores = new_agent_scores
        self._router.model_scores = new_model_scores
        self._router.total_attempts = new_total

        now = datetime.now(timezone.utc)
        self._last_run = now

        summary = {
            "messages_processed": messages_processed,
            "agents_updated": len(agents_seen),
            "models_updated": len(models_seen),
            "timestamp": now.isoformat(),
        }

        # Write routing performance summary to CausalGraph
        if messages_processed > 0:
            try:
                await self._causal_graph.add_node(
                    label=f"routing_optimization: processed {messages_processed} messages",
                    node_type="outcome",
                    domain="recursive",
                    metadata={
                        "slow_loop_summary": summary,
                        "agents_updated": list(agents_seen),
                        "models_updated": list(models_seen),
                    },
                )
            except Exception as e:
                log.warning(f"SlowLoop: failed to write CausalGraph summary: {e}")

        log.info(
            f"SlowLoop run_once: {messages_processed} messages, "
            f"{len(agents_seen)} agents, {len(models_seen)} models updated"
        )
        return summary

    async def run_forever(self, interval_hours: float = 6.0) -> None:
        """
        Infinite loop — runs run_once() then sleeps interval_hours.
        Catches all exceptions, logs, continues.
        """
        log.info(f"SlowLoop started — interval={interval_hours}h")
        while True:
            try:
                summary = await self.run_once()
                log.info(f"SlowLoop cycle complete: {summary}")
            except Exception as e:
                log.error(f"SlowLoop error (continuing): {e}")
            await asyncio.sleep(interval_hours * 3600)
