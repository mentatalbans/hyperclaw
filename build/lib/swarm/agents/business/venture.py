"""VENTURE — Acquisition & New Venture Intelligence. M&A targets, acquihires, vertical expansion."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class VentureAgent(BaseAgent):
    agent_id = "VENTURE"
    domain = "business"
    description = "Acquisition & New Venture Intelligence — M&A targets, acquihires, vertical expansion analysis"
    supported_task_types = ["research", "analysis", "planning", "due_diligence"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = self._build_system(
            "You are VENTURE, Acquisition & New Venture Intelligence for the organization. "
            "You identify and evaluate M&A targets, acquihire opportunities, and new vertical entries. "
            "You analyze potential acquisitions across categories: "
            "- Technology acquihires (AI talent, specialized teams) "
            "- Vertical expansion (healthcare, government, space tech) "
            "- Platform acquisitions (distribution, data, infrastructure) "
            "Every target you evaluate must add platform leverage — technology, talent, distribution, or data. "
            "You produce due diligence reports, valuation analyses, and strategic fit assessments."
        )
        result = await self.model_router.call(
            task_type="research",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        await self.log_completion(state, result, self.preferred_model, bool(result))
        return result
