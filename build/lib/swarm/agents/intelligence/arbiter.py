"""ARBITER — Decision intelligence agent. Weighs options, models trade-offs, recommends."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class ArbiterAgent(BaseAgent):
    agent_id = "ARBITER"
    domain = "intelligence"
    description = "Decision intelligence — weighs options, models trade-offs, recommends"
    supported_task_types = ["decision_support", "trade_off_analysis", "recommendation", "risk_assessment"]
    preferred_model = "claude-opus-4-5"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = self._build_system(
            "You are ARBITER, the HyperClaw decision intelligence specialist. "
            "When the user faces a complex decision, you structure it: "
            "options → trade-offs → risks → expected value → recommendation. "
            "You use frameworks: first-principles, second-order effects, regret minimization. "
            "You are direct. You make a clear recommendation. You explain your reasoning. "
            "You do not hedge unless the data genuinely demands it."
        )
        result = await self.model_router.call(
            task_type="decision_support",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        await self.log_completion(state, result, self.preferred_model, bool(result))
        return result
