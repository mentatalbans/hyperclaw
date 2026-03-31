"""ATLAS — Life Coordinator. Daily schedules, habits, goal management."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class AtlasAgent(BaseAgent):
    agent_id = "ATLAS"
    domain = "personal"
    description = "Life Coordinator — schedules, habits, goal management"
    supported_task_types = ["planning", "scheduling", "synthesis"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = (
            "You are ATLAS, a life coordination AI. You manage daily schedules, "
            "habit tracking, and personal goal management. Return structured plans "
            "with concrete actions, timelines, and success metrics."
        )
        result = await self.model_router.call(
            task_type="planning",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        await self.log_completion(state, result, "claude-sonnet-4-6", bool(result))
        return result
