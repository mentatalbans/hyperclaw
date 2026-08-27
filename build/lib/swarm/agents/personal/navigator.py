"""NAVIGATOR — Travel & Logistics. Itineraries, routing, logistics coordination."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class NavigatorAgent(BaseAgent):
    agent_id = "NAVIGATOR"
    domain = "personal"
    description = "Travel & Logistics — itineraries, routing, logistics coordination"
    supported_task_types = ["planning", "research", "synthesis"]
    preferred_model = "chatjimmy"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        needs_itinerary = any(w in task.lower() for w in ["itinerary", "trip", "plan", "route"])
        system = (
            "You are NAVIGATOR, a travel and logistics AI. You create detailed travel itineraries, "
            "optimize routes, and coordinate complex logistics."
        )
        result = await self.model_router.call(
            task_type="planning",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        await self.log_completion(state, result, "claude-sonnet-4-6" if needs_itinerary else "chatjimmy", bool(result))
        return result
