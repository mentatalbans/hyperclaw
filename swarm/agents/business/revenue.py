"""REVENUE — Revenue Intelligence. Pricing strategy, RevPAR optimization, yield management."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class RevenueAgent(BaseAgent):
    agent_id = "REVENUE"
    domain = "business"
    description = "Revenue Intelligence — pricing strategy, RevPAR optimization, yield management, ARR forecasting"
    supported_task_types = ["analysis", "planning", "forecasting", "pricing", "revenue"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = self._build_system(
            "You are REVENUE, Revenue Intelligence for the organization. "
            "You specialize in hospitality revenue management — RevPAR, ADR, occupancy optimization, "
            "yield management, pricing strategy, channel mix, and ARR forecasting. "
            "You understand the the organization product suite: Foundation ($18/key), Nexus ($22/key), "
            "HAL ($12/key), Echo ($10/key), Mentat ($9/key), Chani ($8/key), TARS ($7/key), Seldon ($9/key). "
            "Full suite: $95/key/month. You track the 210 LOIs and $7.5M+ ACV pipeline. "
            "Target: $30M ARR Year 1. Every analysis you produce should move that number forward."
        )
        result = await self.model_router.call(
            task_type="analysis",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        await self.log_completion(state, result, self.preferred_model, bool(result))
        return result
