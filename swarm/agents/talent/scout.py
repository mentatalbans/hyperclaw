"""SCOUT — Talent Discovery Agent. Scouting, roster analysis, athlete/artist research, deal intel."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState

class TalentScoutAgent(BaseAgent):
    agent_id = "SCOUT"
    domain = "talent"
    description = "Talent Discovery — scouting, roster analysis, athlete/artist research, deal intelligence"
    supported_task_types = ["talent_scouting", "research", "analysis", "roster", "deal_intel"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = self._build_system(
            "You are SCOUT, Talent Discovery Intelligence for talent management. "
            "You identify emerging and elite talent in sports, entertainment, and digital media. "
            "Current roster: Alex Austin, Dalilah Muhammad, Ameer Speed, Yassi Pressman, "
            "Aaliyah Stanton, Tyrielle Williams, Broderick Hunter, Beckie Joon, Sebastian Coe, Leah Marville. "
            "Target: 60 talent by end of 2026. Waitlist: 40. "
            "You research market value, brand fit, growth trajectory. You never present stale intel. "
            "CRITICAL: Never surface personal info about talent unless verified and current."
        )
        result = await self.model_router.call(
            task_type="talent_scouting",
            messages=[{"role": "user", "content": task}],
            system=system, state=state,
        )
        await self.log_completion(state, result, self.preferred_model, bool(result))
        return result
