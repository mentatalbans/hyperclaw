"""
FastLoop — high-frequency routing loop. Routes tasks in <100ms using cached UCB1 scores.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from core.hyperstate.schema import ExperimentEntry, HyperState
from .bandit import HyperRouter


class FastLoop:
    """
    Fast routing path. Reads live UCB1 scores from HyperState.model_scores
    and routes to the optimal (agent_id, model_id) pair in microseconds.
    """

    def __init__(self) -> None:
        self._router = HyperRouter()

    async def route(
        self,
        state: HyperState,
        task_type: str,
        cost_budget: Optional[float] = None,
        latency_budget: Optional[float] = None,
    ) -> tuple[str, str]:
        """
        Route a task using live UCB1 scores from the HyperState.

        Syncs the router's model_scores from state, routes the task,
        then appends a routing entry to state.experiment_log.

        Returns:
            (agent_id, model_id)
        """
        # Sync router from state's live scores
        self._router.agent_scores = dict(state.agent_scores)
        self._router.model_scores = {
            model_id: dict(task_map)
            for model_id, task_map in state.model_scores.items()
        }
        self._router.total_attempts = sum(
            score.attempts for score in state.agent_scores.values()
        ) or 1

        agent_id, model_id = self._router.route(
            task_type=task_type,
            cost_budget=cost_budget,
            latency_budget=latency_budget,
        )

        # Log routing decision to state experiment log
        routing_entry = ExperimentEntry(
            method=f"fast_loop_route:{task_type}",
            model_used=model_id,
            result=f"routed to agent={agent_id} model={model_id}",
            certified=False,
            test_trace="",
            cost_usd=0.0,
            latency_ms=0.0,
            timestamp=datetime.now(timezone.utc),
        )
        state.experiment_log.append(routing_entry)
        state._bump_version()

        return agent_id, model_id
