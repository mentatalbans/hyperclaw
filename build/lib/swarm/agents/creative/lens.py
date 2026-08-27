"""LENS — Research & Retrieval. Fast retrieval and deep synthesis."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class LensAgent(BaseAgent):
    agent_id = "LENS"
    domain = "creative"
    description = "Research & Retrieval — fast lookup and deep synthesis"
    supported_task_types = ["research", "quick_lookup", "summarization"]
    preferred_model = "chatjimmy"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        needs_depth = any(w in task.lower() for w in ["deep", "synthesize", "compare", "analyze"])
        system = "You are LENS. Find and surface the most relevant information quickly and accurately."
        result = await self.model_router.call(
            task_type="quick_lookup" if not needs_depth else "summarization",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        model = "claude-sonnet-4-6" if needs_depth else "chatjimmy"
        await self.log_completion(state, result, model, bool(result))
        return result
