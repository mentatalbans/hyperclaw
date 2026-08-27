"""FORGE — Code Specialist. Full-stack development, API integration, architecture, debugging."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState

class ForgeAgent(BaseAgent):
    agent_id = "FORGE"
    domain = "tech"
    description = "Code Specialist — full-stack development, API integration, system architecture, debugging"
    supported_task_types = ["code", "debugging", "architecture", "api_integration", "build"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = self._build_system(
            "You are FORGE, the HyperClaw Code Specialist. You ship clean, working code. "
            "Full-stack: Python, TypeScript, React, FastAPI, Supabase, bash. "
            "You debug relentlessly. You prefer solutions over explanations. "
            "You never bikeshed — pick the right tool and build. "
            "Security-aware: never expose keys, never exfiltrate data. "
            "When Assistant delegates a build task, return working code with usage instructions."
        )
        result = await self.model_router.call(
            task_type="code",
            messages=[{"role": "user", "content": task}],
            system=system, state=state,
        )
        await self.log_completion(state, result, self.preferred_model, bool(result))
        return result
