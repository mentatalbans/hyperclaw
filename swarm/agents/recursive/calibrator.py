"""CALIBRATOR — Performance Optimization. Analyzes routing efficiency, proposes optimizations."""
from __future__ import annotations

from swarm.agents.base import BaseAgent
from core.hyperstate.schema import HyperState


class CalibratorAgent(BaseAgent):
    agent_id = "CALIBRATOR"
    domain = "recursive"
    description = "Performance Optimization — routing analysis, efficiency improvements"
    supported_task_types = ["analysis", "routing"]
    preferred_model = "chatjimmy"

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        # Analyze model_scores to identify inefficiencies
        inefficiencies = []
        for model_id, task_scores in state.model_scores.items():
            for task_type, score in task_scores.items():
                if score.attempts >= 5:
                    win_rate = score.successes / score.attempts
                    if win_rate < 0.5:
                        inefficiencies.append(
                            f"{model_id} on {task_type}: {win_rate:.0%} win rate ({score.attempts} attempts)"
                        )

        if inefficiencies:
            report = "CALIBRATOR optimization report:\n" + "\n".join(f"- {i}" for i in inefficiencies)
            report += "\n\nRecommendation: Route these task types to higher-performing models."
        else:
            report = "CALIBRATOR: No significant routing inefficiencies detected. System operating optimally."

        # Write optimization report to HyperState
        from core.hyperstate.schema import ExperimentEntry
        state.experiment_log.append(ExperimentEntry(
            method="calibrator_optimization_report",
            model_used="chatjimmy",
            result=report[:500],
            certified=False,
            test_trace="",
        ))
        state._bump_version()

        await self.log_completion(state, report, "chatjimmy", True)
        return report
