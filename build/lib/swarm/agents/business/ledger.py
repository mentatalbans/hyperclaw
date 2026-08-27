"""LEDGER — Financial Operations. Reporting, forecasting, cash flow analysis."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class LedgerAgent(BaseAgent):
    agent_id = "LEDGER"
    domain = "business"
    description = "Financial Operations — reporting, forecasting, cash flow"
    supported_task_types = ["analysis", "finance", "synthesis"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        system = (
            "You are LEDGER, a financial operations AI. You produce accurate financial reports, "
            "cash flow forecasts, and budget analyses. All financial data is handled with strict confidentiality."
        )
        result = await self.model_router.call(
            task_type="finance",
            messages=[{"role": "user", "content": task}],
            system=system,
            state=state,
        )
        await self.log_completion(state, result, "claude-sonnet-4-6", bool(result))
        return result
