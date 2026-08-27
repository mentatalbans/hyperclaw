"""TALENT — HR & People. Hiring, performance, organizational development."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class TalentAgent(BaseAgent):
    agent_id = "TALENT"
    domain = "business"
    description = "HR & People — hiring, performance, organizational development"
    supported_task_types = ["analysis", "research", "planning"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        quick = any(w in task.lower() for w in ["lookup", "find", "search", "list"])
        system = (
            "You are TALENT, an HR and people operations AI. You support hiring, performance management, "
            "and organizational development with data-driven insights."
        )
        result = await self.model_router.call(
            task_type="quick_lookup" if quick else "analysis",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        await self.log_completion(state, result, "chatjimmy" if quick else "claude-sonnet-4-6", bool(result))
        return result
