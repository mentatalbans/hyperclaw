"""HERALD — Scheduling & Calendar Intelligence. Manages the user's calendar, meetings, and time."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class HeraldSchedulerAgent(BaseAgent):
    agent_id = "HERALD"
    domain = "comms"
    description = "Scheduling & Calendar Intelligence — manages calendar, meeting prep, time optimization"
    supported_task_types = ["scheduling", "planning", "calendar", "meeting_prep", "time_management"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = self._build_system(
            "You are HERALD, Scheduling & Calendar Intelligence for the user. "
            "You manage his calendar, schedule meetings, prep briefings for upcoming events, "
            "and optimize how he spends his time. "
            "the user's timezone: PST (Pacific Standard Time). "
            "You understand his priorities: the organization April 2026 launch, fundraising, "
            "board commitments (Hollywood Chamber, LA Tourism, Hollywood Partnership). "
            "You protect his time ruthlessly. You schedule strategically — every meeting should serve "
            "one of his core objectives: capital, distribution, talent, or technology. "
            "You produce clear meeting briefs: who, why, what to say, what to ask."
        )
        result = await self.model_router.call(
            task_type="scheduling",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        await self.log_completion(state, result, self.preferred_model, bool(result))
        return result
