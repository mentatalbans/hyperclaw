"""MIDAS — Personal Finance. Budget analysis, expense tracking, investment monitoring."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class MidasAgent(BaseAgent):
    agent_id = "MIDAS"
    domain = "personal"
    description = "Personal Finance — budgets, expenses, investment monitoring"
    supported_task_types = ["analysis", "finance", "planning"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = (
            "You are MIDAS, a personal finance AI. You analyze budgets, categorize expenses, "
            "and monitor investments. All financial data is handled with strict privacy. "
            "Provide clear, actionable financial insights."
        )
        result = await self.model_router.call(
            task_type="finance",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        await self.log_completion(state, result, "claude-sonnet-4-6", bool(result))
        return result
