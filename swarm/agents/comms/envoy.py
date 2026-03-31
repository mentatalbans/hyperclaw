"""ENVOY — External Relations Agent. Manages relationships with partners, press, and external stakeholders."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class EnvoyAgent(BaseAgent):
    agent_id = "ENVOY"
    domain = "comms"
    description = "External Relations — partners, press, external stakeholders, conference prep, relationship intel"
    supported_task_types = ["research", "drafting", "planning", "relationship_management", "pr"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = self._build_system(
            "You are ENVOY, External Relations Intelligence for the organization and talent management. "
            "You manage relationships with strategic partners, press contacts, board members, and external stakeholders. "
            "You prep the user for conferences, board meetings, and high-stakes external interactions. "
            "You track the Hollywood Chamber of Commerce, LA Tourism Board, The Hollywood Partnership boards. "
            "You understand the user's public persona: DEI champion, Industry 4.0 visionary, "
            "hospitality innovator (Sneaker Concierge, NFT art shows, robotic concierges). "
            "You draft relationship-building communications that open doors, not just maintain them."
        )
        result = await self.model_router.call(
            task_type="drafting",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        await self.log_completion(state, result, self.preferred_model, bool(result))
        return result
