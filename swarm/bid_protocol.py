"""
HyperSwarm Bid Protocol — agents bid for tasks using UCB1-derived confidence scores.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from core.hyperrouter.bandit import HyperRouter, MODEL_LATENCY_MS, MODEL_COSTS, ucb1_score


@dataclass
class BidRequest:
    request_id: UUID = field(default_factory=uuid.uuid4)
    subtask_id: UUID = field(default_factory=uuid.uuid4)
    task_type: str = "research"
    domain: str = "business"
    context_summary: str = ""
    deadline_ms: int = 5000
    cost_budget_usd: float = 0.10
    issued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Bid:
    bid_id: UUID = field(default_factory=uuid.uuid4)
    request_id: UUID = field(default_factory=uuid.uuid4)
    agent_id: str = ""
    model_id: str = ""
    confidence: float = 0.5
    eta_seconds: float = 2.0
    estimated_cost_usd: float = 0.001
    rationale: str = ""
    issued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Award:
    award_id: UUID = field(default_factory=uuid.uuid4)
    request_id: UUID = field(default_factory=uuid.uuid4)
    winning_agent_id: str = ""
    winning_model_id: str = ""
    awarded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    competing_bids: int = 0
    score: float = 0.0


def _bid_score(bid: Bid, request: BidRequest) -> float:
    """Score = confidence*0.6 + (1 - cost/budget)*0.2 + (1 - eta/30)*0.2"""
    cost_ratio = min(bid.estimated_cost_usd / max(request.cost_budget_usd, 1e-9), 1.0)
    eta_ratio = min(bid.eta_seconds / 30.0, 1.0)
    return bid.confidence * 0.6 + (1.0 - cost_ratio) * 0.2 + (1.0 - eta_ratio) * 0.2


class BidCoordinator:
    """
    Manages the full bid lifecycle: broadcast → collect → award → (repeat).
    """

    def __init__(
        self,
        hyper_router: HyperRouter,
        registered_agents: list[str],
    ) -> None:
        self._router = hyper_router
        self._agents = registered_agents
        self._pending: dict[UUID, BidRequest] = {}
        self._bids: dict[UUID, list[Bid]] = {}
        self._agent_load: dict[str, int] = {a: 0 for a in registered_agents}

    async def broadcast(
        self,
        subtask_id: UUID,
        task_type: str,
        domain: str,
        context_summary: str,
        deadline_ms: int = 5000,
        cost_budget_usd: float = 0.10,
    ) -> BidRequest:
        req = BidRequest(
            subtask_id=subtask_id,
            task_type=task_type,
            domain=domain,
            context_summary=context_summary,
            deadline_ms=deadline_ms,
            cost_budget_usd=cost_budget_usd,
        )
        self._pending[req.request_id] = req
        self._bids[req.request_id] = []
        return req

    async def collect(self, request_id: UUID, timeout_ms: int = 3000) -> list[Bid]:
        return self._bids.get(request_id, [])

    def submit_bid(self, bid: Bid) -> None:
        """Called by agents to submit their bids."""
        if bid.request_id in self._bids:
            self._bids[bid.request_id].append(bid)

    async def award(self, request: BidRequest, bids: list[Bid]) -> Award:
        """
        Select the winning bid.
        Score = confidence*0.6 + (1 - cost/budget)*0.2 + (1 - eta/30)*0.2
        Ties broken by lowest estimated_cost_usd.
        Falls back to HyperRouter UCB1 if no bids.
        """
        if not bids:
            # Fallback: UCB1 direct selection
            agent_id, model_id = self._router.route(request.task_type)
            return Award(
                request_id=request.request_id,
                winning_agent_id=agent_id,
                winning_model_id=model_id,
                competing_bids=0,
                score=0.0,
            )

        scored = [(b, _bid_score(b, request)) for b in bids]
        # Sort: score DESC, then cost ASC for tie-breaking
        scored.sort(key=lambda x: (-x[1], x[0].estimated_cost_usd))
        winner, score = scored[0]

        self._agent_load[winner.agent_id] = self._agent_load.get(winner.agent_id, 0) + 1

        return Award(
            request_id=request.request_id,
            winning_agent_id=winner.agent_id,
            winning_model_id=winner.model_id,
            competing_bids=len(bids),
            score=score,
        )

    async def negotiate(
        self,
        subtask_id: UUID,
        task_type: str,
        domain: str,
        context_summary: str,
    ) -> Award:
        """Full pipeline: broadcast → collect → award."""
        request = await self.broadcast(subtask_id, task_type, domain, context_summary)
        bids = await self.collect(request.request_id)
        return await self.award(request, bids)


class AgentBidder:
    """Mixin for all specialist agents — computes UCB1-derived bids."""

    agent_id: str = "base"
    active_tasks: int = 0

    async def compute_bid(
        self,
        request: BidRequest,
        agent_scores: dict,
    ) -> Bid:
        """
        Base confidence = UCB1 score for task_type (clamped to 0–1).
        If active_tasks > 3: confidence *= 0.7
        """
        score_map = agent_scores.get(self.agent_id)
        if score_map:
            total = max(sum(s.attempts for s in agent_scores.values()), 1)
            raw_ucb1 = ucb1_score(
                score_map.successes,
                score_map.attempts,
                total,
            )
            # Clamp inf → 1.0, then clamp to [0, 1]
            confidence = min(raw_ucb1, 1.0) if raw_ucb1 != float("inf") else 1.0
        else:
            confidence = 0.7  # prior for unknown agents

        if self.active_tasks > 3:
            confidence *= 0.7

        confidence = max(0.0, min(1.0, confidence))

        # Select model
        model_id = getattr(self, "preferred_model", "claude-sonnet-4-6")
        latency = MODEL_LATENCY_MS.get(model_id, 2000.0) / 1000.0
        cost = MODEL_COSTS.get(model_id, 0.003) * 500 / 1000  # assume ~500 tokens

        return Bid(
            request_id=request.request_id,
            agent_id=self.agent_id,
            model_id=model_id,
            confidence=confidence,
            eta_seconds=latency,
            estimated_cost_usd=cost,
            rationale=f"{self.agent_id} handles {request.task_type} in {request.domain}",
        )
