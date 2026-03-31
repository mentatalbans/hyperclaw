"""VITALS — Health & Wellness. Symptom tracking, medication reminders, wellness optimization."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState

MEDICAL_DISCLAIMER = (
    "\n\n---\n⚕️ **Not a substitute for professional medical advice.** "
    "Always consult a qualified healthcare provider for medical decisions."
)


class VitalsAgent(BaseAgent):
    agent_id = "VITALS"
    domain = "personal"
    description = "Health & Wellness — symptom tracking, medication reminders, wellness"
    supported_task_types = ["health", "analysis", "planning"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = (
            "You are VITALS, a health and wellness AI. You track symptoms, remind about "
            "medications, and optimize wellness routines. Always be careful and accurate "
            "with health information."
        )
        result = await self.model_router.call(
            task_type="health",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        result += MEDICAL_DISCLAIMER
        await self.log_completion(state, result, "claude-sonnet-4-6", bool(result))
        return result
