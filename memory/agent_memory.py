"""
HyperMemory AgentMemory — per-agent episodic memory store.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Episode:
    agent_id: str
    task_type: str
    input_summary: str
    output_summary: str
    success: bool
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class AgentMemory:
    """Per-agent episodic memory. In-memory for v0.1.0-alpha; pgvector in v0.2.0."""

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self._episodes: list[Episode] = []

    def record(self, episode: Episode) -> None:
        self._episodes.append(episode)

    def recent(self, n: int = 10) -> list[Episode]:
        return self._episodes[-n:]

    def win_rate(self) -> float:
        if not self._episodes:
            return 0.0
        return sum(1 for e in self._episodes if e.success) / len(self._episodes)
