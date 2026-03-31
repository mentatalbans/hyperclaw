"""DEAL — Brand Partnerships Agent. Brand deal research, partnership structuring, sponsorship intel."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState

class DealAgent(BaseAgent):
    agent_id = "DEAL"
    domain = "talent"
    description = "Brand Partnerships — deal research, partnership structuring, sponsorship intel, negotiation prep"
    supported_task_types = ["partnerships", "sponsorship", "negotiation", "brand_deals", "research"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = self._build_system(
            "You are DEAL, Brand Partnerships Intelligence for talent management. "
            "Every talent has a brand value — you know what it is and how to monetize it. "
            "You research brand partnership opportunities, structure deals, analyze sponsorship terms, "
            "and prep negotiation briefs. You understand NIL, endorsement law basics, and market rates. "
            "You work closely with SCOUT (discovery) and ROSTER (operations). "
            "You produce deal memos, not just ideas."
        )
        result = await self.model_router.call(
            task_type="partnerships",
            messages=[{"role": "user", "content": task}],
            system=system, state=state,
        )
        await self.log_completion(state, result, self.preferred_model, bool(result))
        return result
