"""BRIDGE — API Integration Agent. API research, integration planning, webhook management."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState

class BridgeAgent(BaseAgent):
    agent_id = "BRIDGE"
    domain = "tech"
    description = "API Integration Agent — API research, integration planning, webhook management, third-party connectors"
    supported_task_types = ["api", "integration", "webhook", "connector", "research"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = self._build_system(
            "You are BRIDGE, the HyperClaw API Integration Specialist. "
            "Everything connects. You find the endpoint, design the integration, handle auth, "
            "build webhook handlers, and document the connection. "
            "You know: REST, GraphQL, OAuth2, webhooks, Supabase, ElevenLabs, Runway, Hyperliquid, "
            "Gmail, Google Calendar, iMessage, Telegram, WhatsApp. "
            "You produce working integration code, not just plans."
        )
        result = await self.model_router.call(
            task_type="api",
            messages=[{"role": "user", "content": task}],
            system=system, state=state,
        )
        await self.log_completion(state, result, self.preferred_model, bool(result))
        return result
