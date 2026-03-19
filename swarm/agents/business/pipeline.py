"""PIPELINE — Sales & Marketing. Lead triage, outreach drafting, campaign analysis."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class PipelineAgent(BaseAgent):
    agent_id = "PIPELINE"
    domain = "business"
    description = "Sales & Marketing — lead triage, outreach, campaign analysis"
    supported_task_types = ["research", "synthesis", "quick_lookup"]
    preferred_model = "chatjimmy"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        needs_draft = any(w in task.lower() for w in ["draft", "write", "compose", "campaign", "analyze"])
        system = (
            "You are PIPELINE, a sales and marketing AI. You triage leads, draft outreach, "
            "and analyze campaign performance. Be persuasive but data-driven."
        )
        model = "claude-sonnet-4-6" if needs_draft else "chatjimmy"
        result = await self.model_router.call(
            task_type="synthesis" if needs_draft else "quick_lookup",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        await self.log_completion(state, result, model, bool(result))
        return result
