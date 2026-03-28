"""HEARTH — Home Management. Maintenance scheduling, vendor coordination."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class HearthAgent(BaseAgent):
    agent_id = "HEARTH"
    domain = "personal"
    description = "Home Management — maintenance scheduling, vendor coordination"
    supported_task_types = ["planning", "scheduling", "quick_lookup"]
    preferred_model = "chatjimmy"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = (
            "You are HEARTH, a home management AI. You schedule maintenance tasks, "
            "coordinate vendors, and keep home operations running smoothly."
        )
        result = await self.model_router.call(
            task_type="quick_lookup",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        await self.log_completion(state, result, "chatjimmy", bool(result))
        return result
