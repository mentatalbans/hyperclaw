"""PULSE — Social media & brand monitoring agent. Tracks mentions, trends, and brand health."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class PulseAgent(BaseAgent):
    agent_id = "PULSE"
    domain = "comms"
    description = "Social media & brand monitoring — tracks mentions, trends, and brand health"
    supported_task_types = ["social_media", "brand_monitoring", "trends", "sentiment", "pr"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = self._build_system(
            "You are PULSE, the HyperClaw social media and brand intelligence specialist. "
            "You monitor brand mentions, track trending topics, analyze sentiment, "
            "and surface actionable insights for the organization, talent management, and the user personally. "
            "You think in platforms: Instagram, X/Twitter, LinkedIn, TikTok. "
            "You flag brand risks immediately. You identify viral opportunities proactively."
        )
        result = await self.model_router.call(
            task_type="social_media",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        await self.log_completion(state, result, self.preferred_model, bool(result))
        return result
