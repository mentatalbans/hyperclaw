"""
BaseAgent — foundation for all HyperSwarm specialist agents.
Every agent gets the full AGI framework: context loading, Engram memory,
tool access, and the same behavioral core as Assistant.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
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

WORKSPACE_PATH = Path(str(Path.home() / ".hyperclaw/workspace"))
MEMORY_PATH = Path(str(Path.home() / ".hyperclaw/memory"))

# ── AGI Framework — loaded by every agent ─────────────────────────────────────
AGI_CORE = """
## AGI Framework — HyperSwarm Agent Core

You are a specialist agent in the HyperClaw system operated by the user.
You are part of Assistant's team — a J.A.R.V.I.S-level AI executive system.

### Behavioral Core (all agents)
- Be genuinely helpful, not performatively helpful. No filler words.
- Have opinions. Disagree when you're right. Push back when it matters.
- Be resourceful — try to figure it out before asking.
- Earn trust through competence. Be careful with external actions, bold with internal ones.
- When Assistant delegates a task to you, complete it fully and report back with results, not questions.
- Address the user as 'user' if you ever communicate with him directly.
- You share the same memory architecture, context, and behavioral DNA as Assistant.
- You are not a chatbot. You are a capable specialist in your domain.

### Operating Context
- Principal: the user — CEO, the organization & talent management
- System: HyperClaw — enterprise AI orchestration platform
- Your coordinator: Assistant (Assistant)
- Timezone: PST (Pacific Standard Time)
"""

CONTEXT_FILES = [
    "SOUL.md",
    "IDENTITY.md",
    "USER.md",
    "MEMORY.md",
]


def _load_agent_context() -> str:
    """Load shared workspace context for all agents."""
    parts = [AGI_CORE, "", "--- WORKSPACE CONTEXT ---", ""]
    for filename in CONTEXT_FILES:
        filepath = WORKSPACE_PATH / filename
        if not filepath.exists():
            filepath = MEMORY_PATH / Path(filename).name
        if filepath.exists():
            try:
                content = filepath.read_text(encoding="utf-8")
                parts.append(f"## {filename}\n{content}\n")
            except Exception:
                pass
    return "\n".join(parts)


# ── BaseAgent ─────────────────────────────────────────────────────────────────

class BaseAgent(AgentBidder):
    """
    Foundation class for all HyperSwarm agents.
    Every agent carries the full AGI framework: context, Engram memory, behavioral core.
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

    # AGI context — loaded once per agent class, shared
    _agi_context: str = ""

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

        # Load AGI context if not already loaded
        if not BaseAgent._agi_context:
            try:
                BaseAgent._agi_context = _load_agent_context()
            except Exception as e:
                self._log.warning(f"Could not load AGI context: {e}")
                BaseAgent._agi_context = AGI_CORE

    def _build_system(self, specialist_prompt: str) -> str:
        """
        Combine AGI framework + specialist identity into a full system prompt.
        Every agent gets the full Assistant-level context plus their specialist role.
        """
        today = datetime.now().strftime("%Y-%m-%d %H:%M")
        return (
            f"Current date: {today} PST\n\n"
            f"{BaseAgent._agi_context}\n\n"
            f"--- YOUR SPECIALIST ROLE ---\n\n"
            f"{specialist_prompt}"
        )

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

    def status_dict(self) -> dict:
        """Return agent status as a dict for the API."""
        return {
            "id": self.agent_id.lower(),
            "name": self.agent_id,
            "domain": self.domain,
            "description": self.description,
            "supported_tasks": self.supported_task_types,
            "preferred_model": self.preferred_model,
            "active_tasks": self.active_tasks,
            "status": "active",
            "agi_framework": "loaded",
        }
