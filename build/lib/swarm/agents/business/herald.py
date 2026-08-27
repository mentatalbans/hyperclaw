"""HERALD — Output & Delivery. Assembles final deliverables from all agent outputs."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class HeraldAgent(BaseAgent):
    agent_id = "HERALD"
    domain = "business"
    description = "Output & Delivery — assembles final deliverables, always cites certified methods"
    supported_task_types = ["synthesis", "planning"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        # Include certified methods in context for citation
        certified = [cm.method_id for cm in state.certified_methods]
        cert_context = f"\nCertified methods available: {certified}" if certified else ""

        system = (
            "You are HERALD, the final output assembler for HyperClaw. "
            "You take all agent outputs and synthesize them into a polished, complete deliverable. "
            "Cite certified methods when relevant. Be concise, clear, and actionable."
        )
        content = task + cert_context
        result = await self.model_router.call(
            task_type="synthesis",
            messages=[{"role": "user", "content": content}],
            system=system,
            state=state,
        )
        await self.log_completion(state, result, "claude-sonnet-4-6", bool(result))
        return result
