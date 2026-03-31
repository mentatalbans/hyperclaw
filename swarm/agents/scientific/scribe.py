"""SCRIBE — Research Synthesis. Fast first-pass triage, deep synthesis on high-relevance material."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class ScribeAgent(BaseAgent):
    agent_id = "SCRIBE"
    domain = "scientific"
    description = "Research Synthesis — literature triage and deep synthesis"
    supported_task_types = ["research", "synthesis", "summarization"]
    preferred_model = "chatjimmy"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        # ChatJimmy for first-pass triage; Claude for deep synthesis
        needs_deep = any(w in task.lower() for w in ["synthesize", "analyze", "compare", "integrate"])
        system_cj = "You are SCRIBE. Quickly summarize the key points of the following research material."
        system_claude = (
            "You are SCRIBE, a research synthesis AI. You integrate findings across multiple sources, "
            "identify contradictions, and produce authoritative research summaries."
        )
        if needs_deep:
            result = await self.model_router.call(
                task_type="synthesis",
                messages=[{"role": "user", "content": task}],
                system=system_claude,
                state=state,
            )
            model = "claude-sonnet-4-6"
        else:
            result = await self.model_router.call(
                task_type="summarization",
                messages=[{"role": "user", "content": task}],
                system=system_cj,
                state=state,
            )
            model = "chatjimmy"
        await self.log_completion(state, result, model, bool(result))
        return result
