"""
HyperSwarm Bid Protocol — agents bid for tasks based on capability and availability.
Stub for v0.1.0-alpha.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class TaskBid:
    agent_id: str
    task_id: str
    confidence: float  # 0.0–1.0
    estimated_cost_usd: float
    estimated_latency_ms: float


class BidProtocol:
    """Collects and evaluates bids from agents for a given task."""

    def select_winner(self, bids: list[TaskBid]) -> TaskBid | None:
        """Select the winning bid: highest confidence within budget constraints."""
        if not bids:
            return None
        return max(bids, key=lambda b: b.confidence)
