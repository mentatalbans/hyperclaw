"""
HyperRouter UCB1 Bandit — multi-armed bandit model and agent routing.
Uses Upper Confidence Bound 1 (UCB1) algorithm for exploration/exploitation.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Optional

from core.hyperstate.schema import AgentScore, ModelScore


# ── Cost and latency tables ───────────────────────────────────────────────────

MODEL_COSTS: dict[str, float] = {
    "chatjimmy": 0.000001,
    "claude-sonnet-4-6": 0.003,
    "claude-code": 0.003,
    "nim-local": 0.0,
}

MODEL_LATENCY_MS: dict[str, float] = {
    "chatjimmy": 50.0,
    "claude-sonnet-4-6": 2000.0,
    "claude-code": 3000.0,
    "nim-local": 500.0,
}

# Tasks ChatJimmy handles well (fast, cheap, low-stakes)
CHATJIMMY_TASK_TYPES: frozenset[str] = frozenset([
    "routing", "classification", "quick_lookup",
    "summarization", "draft", "triage", "status_check",
])


# ── Core UCB1 functions ───────────────────────────────────────────────────────

def ucb1_score(
    successes: int,
    attempts: int,
    total_attempts: int,
    c: float = 2.0,
) -> float:
    """
    Compute UCB1 score for a single arm.

    Returns float('inf') if attempts == 0 (unexplored arm always gets tried first).
    Formula: mean_reward + c * sqrt(ln(total_attempts) / attempts)
    """
    if attempts == 0:
        return float("inf")
    mean_reward = successes / attempts
    exploration = c * math.sqrt(math.log(max(total_attempts, 1)) / attempts)
    return mean_reward + exploration


def select_agent(
    agents: dict[str, AgentScore],
    task_type: str,
    total_attempts: int,
) -> str:
    """
    Select the best agent for a task_type using UCB1.
    Prefers agents whose task_type matches, falls back to all agents.
    Returns the agent_id with the highest UCB1 score.
    """
    if not agents:
        return "default_agent"

    # Filter to agents matching task_type if possible
    matching = {
        aid: score for aid, score in agents.items()
        if score.task_type == task_type
    }
    pool = matching if matching else agents

    best_id = max(
        pool,
        key=lambda aid: ucb1_score(
            pool[aid].successes,
            pool[aid].attempts,
            total_attempts,
        ),
    )
    return best_id


def select_model(
    model_scores: dict[str, dict[str, ModelScore]],
    task_type: str,
    total_attempts: int,
    cost_budget_usd: Optional[float] = None,
    latency_budget_ms: Optional[float] = None,
) -> str:
    """
    Select the best model for a task_type using UCB1, with optional budget filters.

    ChatJimmy is the default for CHATJIMMY_TASK_TYPES if it passes budget checks.
    Budget filters are applied before scoring — models exceeding either budget are excluded.
    Falls back to 'claude-sonnet-4-6' if no scored models available.
    """
    # ChatJimmy fast-path for cheap/quick tasks
    if task_type in CHATJIMMY_TASK_TYPES:
        cj_cost = MODEL_COSTS.get("chatjimmy", 0.0)
        cj_latency = MODEL_LATENCY_MS.get("chatjimmy", 50.0)
        cost_ok = cost_budget_usd is None or cj_cost <= cost_budget_usd
        latency_ok = latency_budget_ms is None or cj_latency <= latency_budget_ms
        if cost_ok and latency_ok:
            return "chatjimmy"

    # Build candidate pool with budget filtering
    candidates: dict[str, ModelScore] = {}
    for model_id, task_map in model_scores.items():
        score = task_map.get(task_type, ModelScore())

        cost = MODEL_COSTS.get(model_id, 0.001)
        latency = MODEL_LATENCY_MS.get(model_id, 2000.0)

        if cost_budget_usd is not None and cost > cost_budget_usd:
            continue
        if latency_budget_ms is not None and latency > latency_budget_ms:
            continue

        candidates[model_id] = score

    if not candidates:
        # Fallback: try any model within budget ignoring task_type filter
        for model_id in MODEL_COSTS:
            cost = MODEL_COSTS.get(model_id, 0.001)
            latency = MODEL_LATENCY_MS.get(model_id, 2000.0)
            if cost_budget_usd is not None and cost > cost_budget_usd:
                continue
            if latency_budget_ms is not None and latency > latency_budget_ms:
                continue
            candidates[model_id] = ModelScore()

    if not candidates:
        return "claude-sonnet-4-6"

    best_model = max(
        candidates,
        key=lambda mid: ucb1_score(
            candidates[mid].successes,
            candidates[mid].attempts,
            total_attempts,
        ),
    )
    return best_model


def update_scores(
    scores: dict[str, dict[str, ModelScore]],
    model_id: str,
    task_type: str,
    success: bool,
) -> dict[str, dict[str, ModelScore]]:
    """
    Immutably update model scores. Returns a new dict — original is unchanged.
    """
    new_scores = copy.deepcopy(scores)
    if model_id not in new_scores:
        new_scores[model_id] = {}
    if task_type not in new_scores[model_id]:
        new_scores[model_id][task_type] = ModelScore()

    entry = new_scores[model_id][task_type]
    entry.attempts += 1
    if success:
        entry.successes += 1

    return new_scores


# ── HyperRouter dataclass ──────────────────────────────────────────────────────

@dataclass
class HyperRouter:
    """
    Stateful router that holds live UCB1 scores and routes tasks to
    (agent_id, model_id) pairs.
    """
    agent_scores: dict[str, AgentScore] = field(default_factory=dict)
    model_scores: dict[str, dict[str, ModelScore]] = field(default_factory=dict)
    total_attempts: int = 0

    def route(
        self,
        task_type: str,
        cost_budget: Optional[float] = None,
        latency_budget: Optional[float] = None,
    ) -> tuple[str, str]:
        """
        Route a task to the best (agent_id, model_id) pair.
        Uses UCB1 for both selections.
        """
        agent_id = select_agent(self.agent_scores, task_type, self.total_attempts)
        model_id = select_model(
            self.model_scores,
            task_type,
            self.total_attempts,
            cost_budget_usd=cost_budget,
            latency_budget_ms=latency_budget,
        )
        return agent_id, model_id

    def record(self, agent_id: str, model_id: str, task_type: str, success: bool) -> None:
        """Record the outcome of a routing decision and update scores."""
        self.total_attempts += 1
        self.model_scores = update_scores(self.model_scores, model_id, task_type, success)
        if agent_id not in self.agent_scores:
            self.agent_scores[agent_id] = AgentScore(task_type=task_type)
        self.agent_scores[agent_id].attempts += 1
        if success:
            self.agent_scores[agent_id].successes += 1
