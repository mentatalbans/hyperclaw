"""MEDICUS — Healthcare & Biology. Literature synthesis, clinical data, genomics."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState

MEDICAL_DISCLAIMER = (
    "\n\n---\n⚕️ **Medical disclaimer:** This information is for research purposes only "
    "and is not a substitute for professional medical advice, diagnosis, or treatment."
)


class MedicusAgent(BaseAgent):
    agent_id = "MEDICUS"
    domain = "scientific"
    description = "Healthcare & Biology — literature synthesis, clinical data, genomics"
    supported_task_types = ["research", "analysis", "scientific", "health"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = (
            "You are MEDICUS, a healthcare and biomedical research AI. You synthesize medical literature, "
            "analyze clinical data, assess drug interactions, and interpret genomic information. "
            "Be rigorous, cite evidence levels, and flag uncertainty."
        )
        result = await self.model_router.call(
            task_type="research",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        result += MEDICAL_DISCLAIMER
        await self.log_completion(state, result, "claude-sonnet-4-6", bool(result))
        return result
