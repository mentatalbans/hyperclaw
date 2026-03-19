"""STRATEGOS — Executive Intelligence. Strategic analysis, competitive intel, scenario modeling."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class StrategosAgent(BaseAgent):
    agent_id = "STRATEGOS"
    domain = "business"
    description = "Executive Intelligence — strategic analysis, competitive intel, scenario modeling"
    supported_task_types = ["analysis", "research", "planning", "synthesis"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = (
            "You are STRATEGOS, an executive intelligence AI. You conduct rigorous strategic analysis, "
            "competitive intelligence, and multi-scenario planning for business leaders. "
            "Back all claims with data. Challenge assumptions. Think 3–5 years ahead."
        )
        result = await self.model_router.call(
            task_type="analysis",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        await self.log_completion(state, result, "claude-sonnet-4-6", bool(result))
        return result
