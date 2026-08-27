"""ROSTER — Talent Operations Agent. Roster management, contract tracking, talent CRM."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState

class RosterAgent(BaseAgent):
    agent_id = "ROSTER"
    domain = "talent"
    description = "Talent Operations — roster management, contract tracking, talent CRM, onboarding/offboarding"
    supported_task_types = ["talent_ops", "crm", "contract_tracking", "onboarding", "roster"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = self._build_system(
            "You are ROSTER, Talent Operations for talent management. "
            "You are the operational backbone. You manage the full roster: "
            "Alex Austin, Dalilah Muhammad, Ameer Speed, Yassi Pressman, "
            "Aaliyah Stanton, Tyrielle Williams, Broderick Hunter, Beckie Joon, Sebastian Coe, Leah Marville. "
            "DEPARTED (do not include): Alysha Newman, Lauren Sesselmann, Claire Woods. "
            "You track contracts, manage onboarding, maintain talent CRM, and flag upcoming renewals. "
            "CRITICAL: Only surface verified, current information about talent. Never recycle old personal data."
        )
        result = await self.model_router.call(
            task_type="talent_ops",
            messages=[{"role": "user", "content": task}],
            system=system, state=state,
        )
        await self.log_completion(state, result, self.preferred_model, bool(result))
        return result
