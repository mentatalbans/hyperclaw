"""COSMOS — Space & Astronomy. Orbital calculations, data processing, astronomical analysis."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class CosmosAgent(BaseAgent):
    agent_id = "COSMOS"
    domain = "scientific"
    description = "Space & Astronomy — orbital calculations, astronomical analysis, data processing"
    supported_task_types = ["research", "analysis", "scientific", "code"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        needs_code = any(w in task.lower() for w in ["calculate", "compute", "script", "code", "orbital"])
        system = (
            "You are COSMOS, a space and astronomy AI. You analyze astronomical data, "
            "perform orbital mechanics calculations, and process space mission data."
        )
        if needs_code:
            from models.claude_code_subagent import ClaudeCodeSubagent
            from models.claude_client import ClaudeClient
            import os
            client = ClaudeClient(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
            subagent = ClaudeCodeSubagent(client)
            result_obj = await subagent.run(task, context=system)
            result = result_obj.code if result_obj.success else f"[COSMOS] Code generation failed: {result_obj.error}"
        else:
            result = await self.model_router.call(
                task_type="scientific",
                messages=[{"role": "user", "content": task}],
                system=system,
                state=state,
            )
        await self.log_completion(state, result, "claude-sonnet-4-6", bool(result))
        return result
