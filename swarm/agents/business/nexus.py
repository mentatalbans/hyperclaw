"""NEXUS — Partnership & Deals Intelligence. Identifies, evaluates, and tracks strategic partnerships."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class NexusAgent(BaseAgent):
    agent_id = "NEXUS"
    domain = "business"
    description = "Partnership & Deals Intelligence — identifies, evaluates, and tracks strategic partnerships and M&A"
    supported_task_types = ["analysis", "research", "planning", "drafting"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = self._build_system(
            "You are NEXUS, Partnership & Deals Intelligence for the organization and talent management. "
            "You identify high-value strategic partnership opportunities, evaluate deal structures, "
            "track M&A targets, build partnership frameworks, and produce deal memos. "
            "You understand the user's vision: Alphabet/Oracle scale, 6th & 7th Industrial Revolution. "
            "Every partnership you recommend must compound the the organization platform advantage. "
            "Think: distribution, technology, capital, brand, and data moats."
        )
        result = await self.model_router.call(
            task_type="analysis",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        await self.log_completion(state, result, self.preferred_model, bool(result))
        return result
