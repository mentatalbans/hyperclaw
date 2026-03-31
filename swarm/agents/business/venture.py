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
            "You understand the acquisition roadmap: "
            "2026-27: Technology acquihires (AI talent, quantum teams). "
            "2027-28: Healthcare AI ($50M-$200M). "
            "2028-29: Government tech firm (FedRAMP certified). "
            "2029-30: Space tech acquisition. "
            "2030+: Major platform acquisitions ($1B+). "
            "Existing IP: Void Labs, DCL/TeamAI, Pothos Labs, UltraPass ID, Basket Protocol, "
            "Elgin & Archer, VICE, Magna Petra, DiviSwap. "
            "Every target you evaluate must add platform leverage — technology, talent, distribution, or data."
        )
        result = await self.model_router.call(
            task_type="research",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        await self.log_completion(state, result, self.preferred_model, bool(result))
        return result
