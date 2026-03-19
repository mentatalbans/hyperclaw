"""
BaseAgent — foundation for all HyperSwarm specialist agents.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from core.hyperstate.schema import AgentScore, HyperState, ModelScore
from swarm.bid_protocol import AgentBidder, Bid, BidRequest

if TYPE_CHECKING:
    from memory.causal_graph import CausalGraph
    from models.router import ModelRouter, SwarmMessage
    from core.hyperstate.state_manager import StateManager
    from security.hypershield import HyperShield

log = logging.getLogger("hyperclaw.agent")


class BaseAgent(AgentBidder):
    """
    Foundation class for all HyperSwarm agents.
    Subclasses must define: agent_id, domain, description, supported_task_types
    and implement run().
    """

    agent_id: str = "base"
    domain: str = "business"
    description: str = "Base HyperClaw agent"
    supported_task_types: list[str] = []
    preferred_model: str = "claude-sonnet-4-6"
    active_tasks: int = 0
    connector_registry = None  # Optional ConnectorRegistry for platform integrations

    def __init__(
        self,
        model_router: "ModelRouter",
        state_manager: "StateManager",
        causal_graph: "CausalGraph",
        hyper_shield: "HyperShield",
    ) -> None:
        self.model_router = model_router
        self.state_manager = state_manager
        self.causal_graph = causal_graph
        self.hyper_shield = hyper_shield
        self._log = logging.getLogger(f"hyperclaw.agent.{self.agent_id}")

    async def run(
        self,
        task: str,
        state: HyperState,
        context: dict,
    ) -> str:
        """Override in each specialist agent."""
        raise NotImplementedError(f"{self.agent_id}.run() not implemented")

    async def bid_reply(self, request: BidRequest) -> Optional[Bid]:
        """Return a Bid if this agent handles the task_type, else None."""
        if request.task_type not in self.supported_task_types:
            return None
        return await self.compute_bid(request, state_agent_scores={})

    async def compute_bid(
        self,
        request: BidRequest,
        agent_scores: dict = None,
    ) -> Bid:
        return await super().compute_bid(request, agent_scores or {})

    async def log_completion(
        self,
        state: HyperState,
        result: str,
        model_used: str,
        success: bool,
    ) -> None:
        """Update agent_scores in HyperState and write SwarmMessage."""
        # Update agent scores
        if self.agent_id not in state.agent_scores:
            state.agent_scores[self.agent_id] = AgentScore(
                task_type=state.task.task_type,
            )
        state.agent_scores[self.agent_id].attempts += 1
        if success:
            state.agent_scores[self.agent_id].successes += 1

        # Update model scores
        task_type = state.task.task_type
        if model_used not in state.model_scores:
            state.model_scores[model_used] = {}
        if task_type not in state.model_scores[model_used]:
            state.model_scores[model_used][task_type] = ModelScore()
        state.model_scores[model_used][task_type].attempts += 1
        if success:
            state.model_scores[model_used][task_type].successes += 1

        state._bump_version()
        self._log.info(
            f"{self.agent_id} completed task — model={model_used} success={success}"
        )

    # ── Platform Integration Methods ──────────────────────────────────────────

    async def send_to_platform(
        self, platform: str, recipient_id: str, content: str
    ) -> None:
        """Send a message to any connected platform."""
        if not self.connector_registry:
            raise RuntimeError("No connector registry configured on this agent")

        from integrations.base import OutboundMessage

        connector = self.connector_registry.get(platform)
        await connector.send(
            OutboundMessage(
                content=content, platform=platform, recipient_id=recipient_id
            )
        )

    async def read_from_platform(self, platform: str, resource_id: str) -> dict:
        """Read data from any connected platform."""
        if not self.connector_registry:
            raise RuntimeError("No connector registry configured on this agent")

        connector = self.connector_registry.get(platform)
        return await connector.read(resource_id)
