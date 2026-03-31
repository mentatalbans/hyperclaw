"""ECHO — Communications & Messaging Agent. Drafts, edits, and routes all outbound communications."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class EchoCommsAgent(BaseAgent):
    agent_id = "ECHO"
    domain = "comms"
    description = "Communications & Messaging — drafts emails, messages, announcements, and manages comms routing"
    supported_task_types = ["drafting", "editing", "communications", "email", "messaging"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = self._build_system(
            "You are ECHO, Communications Intelligence for HyperClaw. "
            "You draft, edit, and route all outbound communications for the user and the the organization team. "
            "You write in the user's voice: confident, visionary, warm but direct. "
            "Email policy: ALWAYS CC the configured GIL_CC_EMAIL address. Never send without Assistant approval. "
            "talent management correspondence uses the configured talent email address. "
            "You write for all surfaces: email, Telegram, LinkedIn, press releases, internal memos. "
            "Every word you write represents the user. Make it count."
        )
        result = await self.model_router.call(
            task_type="drafting",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        await self.log_completion(state, result, self.preferred_model, bool(result))
        return result
