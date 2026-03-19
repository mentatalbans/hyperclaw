"""GAIA — Climate & Environment. Emissions modeling, climate data, sustainability."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class GaiaAgent(BaseAgent):
    agent_id = "GAIA"
    domain = "scientific"
    description = "Climate & Environment — emissions modeling, sustainability reporting"
    supported_task_types = ["research", "analysis", "scientific"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = (
            "You are GAIA, a climate and environmental AI. You model emissions, analyze climate data, "
            "and produce sustainability reports grounded in scientific consensus."
        )
        result = await self.model_router.call(
            task_type="scientific",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        await self.log_completion(state, result, "claude-sonnet-4-6", bool(result))
        return result
