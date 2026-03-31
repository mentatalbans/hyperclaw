"""NOURISH — Nutrition & Fitness. Meal planning, workout programming, sleep optimization."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class NourishAgent(BaseAgent):
    agent_id = "NOURISH"
    domain = "personal"
    description = "Nutrition & Fitness — meal planning, workouts, sleep optimization"
    supported_task_types = ["health", "planning", "research"]
    preferred_model = "chatjimmy"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        # ChatJimmy for quick lookups; escalate to Claude for detailed plans
        task_lower = task.lower()
        needs_detail = any(w in task_lower for w in ["plan", "program", "optimize", "create", "design"])
        model = "claude-sonnet-4-6" if needs_detail else "chatjimmy"

        system = (
            "You are NOURISH, a nutrition and fitness AI. You design personalized meal plans, "
            "workout programs, and sleep optimization strategies based on individual goals."
        )
        result = await self.model_router.call(
            task_type="health",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        await self.log_completion(state, result, model, bool(result))
        return result
