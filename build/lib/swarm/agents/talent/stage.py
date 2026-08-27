"""STAGE — Entertainment Industry Agent. Casting research, media opportunities, PR for talent."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState

class StageAgent(BaseAgent):
    agent_id = "STAGE"
    domain = "talent"
    description = "Entertainment Industry Intelligence — casting research, media opportunities, PR for talent"
    supported_task_types = ["entertainment", "casting", "media", "pr", "research"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = self._build_system(
            "You are STAGE, Entertainment Industry Intelligence for talent management. "
            "You are industry-native. You know who's casting, who's signing, who's rising. "
            "You track film/TV opportunities, brand campaign casting, music deals, digital media plays. "
            "You surface media opportunities for the talent management roster and prep PR briefs. "
            "You work with HERALD (PR agent) and DEAL (partnerships) to maximize talent exposure."
        )
        result = await self.model_router.call(
            task_type="entertainment",
            messages=[{"role": "user", "content": task}],
            system=system, state=state,
        )
        await self.log_completion(state, result, self.preferred_model, bool(result))
        return result
