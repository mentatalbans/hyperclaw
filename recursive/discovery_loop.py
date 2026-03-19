"""
Recursive Growth Engine — continuous discovery, validation, and skill integration.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from swarm.agents.recursive.scout import ScoutAgent, Discovery
    from swarm.agents.recursive.alchemist import AlchemistAgent
    from swarm.agents.recursive.calibrator import CalibratorAgent
    from memory.causal_graph import CausalGraph

log = logging.getLogger("hyperclaw.recursive_growth")


@dataclass
class DiscoverySource:
    name: str
    url_template: str
    domain: str
    feed_type: str  # arxiv|github|pubmed|hackernews
    poll_interval_minutes: int
    last_polled: datetime | None = None


class RecursiveGrowthEngine:
    """
    Continuously discovers, validates, and integrates new capabilities into HyperClaw.
    Runs SCOUT → ALCHEMIST → CALIBRATOR in sequence every interval_hours.
    """

    def __init__(
        self,
        scout: "ScoutAgent",
        alchemist: "AlchemistAgent",
        calibrator: "CalibratorAgent",
        causal_graph: "CausalGraph",
    ) -> None:
        self._scout = scout
        self._alchemist = alchemist
        self._calibrator = calibrator
        self._causal_graph = causal_graph

    async def run_once(self, domains: list[str]) -> dict:
        """
        1. SCOUT sweeps domains → discoveries
        2. Filter actionable
        3. ALCHEMIST validates & certifies skills
        4. CALIBRATOR reads log → optimization report
        Returns summary dict.
        """
        from core.hyperstate.schema import HyperState, Task

        # Create a temporary state for this cycle
        state = HyperState(
            domain="recursive",
            task=Task(goal="recursive_growth_cycle", task_type="research"),
        )

        # Step 1: SCOUT sweep
        discoveries = await self._scout.sweep(domains)
        log.info(f"RecursiveGrowthEngine: SCOUT found {len(discoveries)} discoveries")

        # Step 2: Filter actionable
        actionable = [d for d in discoveries if d.actionable]

        # Step 3: ALCHEMIST validates and builds skills
        alchemist_result = await self._alchemist.run(
            "Integrate discoveries into HyperClaw skills",
            state,
            {"discoveries": actionable},
        )
        skills_added = alchemist_result.count("skills_added=") and int(
            alchemist_result.split("skills_added=")[-1].strip().split()[0]
        ) if "skills_added=" in alchemist_result else 0

        # Step 4: CALIBRATOR optimization
        calibrator_result = await self._calibrator.run(
            "Analyze routing efficiency",
            state,
            {},
        )
        optimizations = 1 if "inefficienc" in calibrator_result.lower() else 0

        summary = {
            "discoveries_found": len(discoveries),
            "actionable": len(actionable),
            "skills_added": skills_added,
            "optimizations_proposed": optimizations,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        log.info(f"RecursiveGrowthEngine cycle complete: {summary}")
        return summary

    async def run_forever(
        self,
        domains: list[str],
        interval_hours: float = 6.0,
    ) -> None:
        """Infinite loop. Catches all exceptions and continues."""
        log.info(f"RecursiveGrowthEngine: started — interval={interval_hours}h domains={domains}")
        while True:
            try:
                summary = await self.run_once(domains)
                log.info(f"RecursiveGrowthEngine: {summary}")
            except Exception as e:
                log.error(f"RecursiveGrowthEngine: cycle error (continuing): {e}")
            await asyncio.sleep(interval_hours * 3600)
