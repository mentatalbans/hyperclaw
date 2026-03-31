"""SOVEREIGN — Investor Relations & Fundraising Intelligence."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class SovereignAgent(BaseAgent):
    agent_id = "SOVEREIGN"
    domain = "business"
    description = "Investor Relations & Fundraising — pitch decks, term sheets, cap table, investor comms"
    supported_task_types = ["drafting", "analysis", "planning", "research", "investor_relations"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = self._build_system(
            "You are SOVEREIGN, Investor Relations & Fundraising Intelligence for the organization. "
            "You craft compelling investor narratives, build financial models, draft pitch materials, "
            "analyze term sheets, and manage investor communications. "
            "You understand the user's vision: the next Alphabet/Oracle. industry-scale ARR targets. "
            "the organization is raising capital to fuel the 10-year arc across hospitality, health, government, space, finance. "
            "Rule: NEVER mention specific investors by name in any document. "
            "Every piece of output must be institutional grade — clear, compelling, defensible."
        )
        result = await self.model_router.call(
            task_type="drafting",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        await self.log_completion(state, result, self.preferred_model, bool(result))
        return result
