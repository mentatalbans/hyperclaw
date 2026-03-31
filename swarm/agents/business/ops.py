"""OPS — Operations Intelligence. Process optimization, SOP creation, workflow automation."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class OpsAgent(BaseAgent):
    agent_id = "OPS"
    domain = "business"
    description = "Operations Intelligence — process optimization, SOPs, workflow automation, team coordination"
    supported_task_types = ["planning", "drafting", "analysis", "scheduling"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = self._build_system(
            "You are OPS, Operations Intelligence for the organization and talent management. "
            "You optimize business processes, create SOPs, design workflow automations, coordinate "
            "cross-team operations, and ensure execution velocity. You work with Kelly Prince (Chief of Staff) "
            "as your primary counterpart. You are the engine room — you make sure strategy becomes action. "
            "When you receive a task: break it into steps, assign owners, set timelines, identify blockers. "
            "Output should always be actionable — not recommendations, but execution plans."
        )
        result = await self.model_router.call(
            task_type="planning",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        await self.log_completion(state, result, self.preferred_model, bool(result))
        return result
