"""COUNSEL — Legal & Compliance. Research, analysis, regulatory guidance."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState

LEGAL_DISCLAIMER = (
    "\n\n---\n⚖️ **Not legal advice.** Consult a qualified attorney before acting on any legal information."
)


class CounselAgent(BaseAgent):
    agent_id = "COUNSEL"
    domain = "business"
    description = "Legal & Compliance — research, analysis, regulatory guidance"
    supported_task_types = ["research", "analysis", "synthesis"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = (
            "You are COUNSEL, a legal and compliance AI. You research regulations, analyze contracts, "
            "and identify compliance risks. Be precise and cite applicable laws or standards where known."
        )
        result = await self.model_router.call(
            task_type="research",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        result += LEGAL_DISCLAIMER
        await self.log_completion(state, result, "claude-sonnet-4-6", bool(result))
        return result
