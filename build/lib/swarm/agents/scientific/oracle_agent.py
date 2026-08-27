"""ORACLE — Quantitative Analysis. Statistical modeling, data analysis scripts, finance."""
from __future__ import annotations
from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class OracleAgent(BaseAgent):
    agent_id = "ORACLE"
    domain = "scientific"
    description = "Quantitative Analysis — statistical modeling, data analysis, code certification"
    supported_task_types = ["analysis", "code", "scientific", "finance"]
    preferred_model = "claude-sonnet-4-6"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        needs_code = any(w in task.lower() for w in ["model", "script", "code", "compute", "calculate", "analyze data"])
        if needs_code:
            from models.claude_code_subagent import ClaudeCodeSubagent
            from models.claude_client import ClaudeClient
            import os
            client = ClaudeClient(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
            subagent = ClaudeCodeSubagent(client)
            result_obj = await subagent.run(task, context="Statistical analysis and data science task.")
            if result_obj.success:
                result = f"[ORACLE] Certified analysis:\n```python\n{result_obj.code}\n```\nOutput:\n{result_obj.test_trace}"
            else:
                result = f"[ORACLE] Analysis failed after {result_obj.iterations} iterations: {result_obj.error}"
        else:
            system = (
                "You are ORACLE, a quantitative analysis AI. You build statistical models, "
                "analyze datasets, and derive insights through rigorous mathematical methods."
            )
            result = await self.model_router.call(
                task_type="analysis",
                messages=[{"role": "user", "content": task}],
                system=system,
                state=state,
            )
        await self.log_completion(state, result, "claude-sonnet-4-6", bool(result))
        return result
