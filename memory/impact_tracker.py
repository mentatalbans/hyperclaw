"""
HyperMemory ImpactTracker — tracks business/scientific impact of agent actions.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class ImpactEvent:
    agent_id: str
    action: str
    domain: str
    impact_score: float  # 0.0 = no impact, 1.0 = max impact
    description: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ImpactTracker:
    """Records and queries impact events across agents and domains."""

    def __init__(self) -> None:
        self._events: list[ImpactEvent] = []

    def record(self, event: ImpactEvent) -> None:
        self._events.append(event)

    def total_impact(self, agent_id: str | None = None) -> float:
        events = self._events
        if agent_id:
            events = [e for e in events if e.agent_id == agent_id]
        return sum(e.impact_score for e in events)

    def top_events(self, n: int = 5) -> list[ImpactEvent]:
        return sorted(self._events, key=lambda e: e.impact_score, reverse=True)[:n]
