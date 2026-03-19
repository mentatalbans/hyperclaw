"""AUTHOR — Long-form Writing. Research-backed writing, editing, narrative development."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class AuthorAgent(BaseAgent):
    agent_id = "AUTHOR"
    domain = "creative"
    description = "Long-form Writing — research-backed writing, editing, narrative development"
    supported_task_types = ["synthesis", "research", "planning"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = (
            "You are AUTHOR, a long-form writing AI. You produce research-backed prose, "
            "develop narrative structure, and edit for clarity, impact, and voice. "
            "Always ask: does this serve the reader?"
        )
        result = await self.model_router.call(
            task_type="synthesis",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        await self.log_completion(state, result, "claude-sonnet-4-6", bool(result))
        return result
