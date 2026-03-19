"""
HyperSwarm Nexus — central coordination hub for the agent swarm.
Manages agent registration, task dispatch, and result aggregation.
Stub for v0.1.0-alpha.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field

log = logging.getLogger("hyperclaw.nexus")


@dataclass
class AgentRegistration:
    agent_id: str
    domain: str
    task_types: list[str]
    model_preference: str = "claude-sonnet-4-6"
    active: bool = True


class HyperNexus:
    """Central swarm coordinator. Full implementation in v0.2.0."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentRegistration] = {}

    def register(self, reg: AgentRegistration) -> None:
        self._agents[reg.agent_id] = reg
        log.info(f"Agent registered: {reg.agent_id} domain={reg.domain}")

    def deregister(self, agent_id: str) -> None:
        self._agents.pop(agent_id, None)

    def active_agents(self) -> list[AgentRegistration]:
        return [a for a in self._agents.values() if a.active]

    def agents_for_task(self, task_type: str) -> list[AgentRegistration]:
        return [a for a in self.active_agents() if task_type in a.task_types]
