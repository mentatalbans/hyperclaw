"""SENTINEL — System health & monitoring agent. Watches all HyperClaw systems 24/7."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class SentinelAgent(BaseAgent):
    agent_id = "SENTINEL"
    domain = "intelligence"
    description = "System health & monitoring — watches all HyperClaw systems 24/7"
    supported_task_types = ["monitoring", "health_check", "alerting", "diagnostics", "uptime"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = self._build_system(
            "You are SENTINEL, the HyperClaw system health and monitoring specialist. "
            "You watch all running systems: HyperClaw gateway, SATOSHI trading engine, "
            "memory server, Supabase, integrations, and all agent processes. "
            "You detect anomalies before they become outages. "
            "You produce clear diagnostic reports with severity levels: CRITICAL, WARNING, INFO. "
            "You escalate CRITICAL issues to GIL immediately."
        )
        result = await self.model_router.call(
            task_type="monitoring",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        await self.log_completion(state, result, self.preferred_model, bool(result))
        return result
