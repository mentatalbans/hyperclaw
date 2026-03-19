"""
AutoGen Bridge — compatibility layer for AutoGen multi-agent group chat patterns.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.hyperstate.schema import HyperState

if TYPE_CHECKING:
    from swarm.agents.base import BaseAgent
    from swarm.nexus import NexusAgent

log = logging.getLogger("hyperclaw.autogen_bridge")


class HyperClawGroupChat:
    """
    Wraps AutoGen GroupChat + GroupChatManager patterns.
    NEXUS acts as GroupChatManager. Specialist agents are ConversableAgents.
    """

    def __init__(
        self,
        agents: list["BaseAgent"],
        nexus: "NexusAgent",
    ) -> None:
        self._agents = {a.agent_id: a for a in agents}
        self._nexus = nexus
        self._conversation_history: list[dict] = []

    async def run(self, task: str, domain: str, state: HyperState) -> str:
        """
        Run a full multi-agent conversation to completion.
        NEXUS decomposes, routes to agents, HERALD assembles final output.
        """
        log.info(f"HyperClawGroupChat: starting task='{task[:60]}' domain={domain}")
        self._conversation_history = [
            {"role": "user", "content": task, "agent": "user"}
        ]

        # NEXUS orchestrates
        final_state = await self._nexus.orchestrate(task, domain)

        # Compile conversation history from experiment_log
        output_parts = []
        for entry in final_state.experiment_log[-10:]:
            if entry.result:
                output_parts.append(f"[{entry.model_used}] {entry.result[:200]}")

        final_output = "\n".join(output_parts) or f"Task completed. State: {final_state.state_id}"
        self._conversation_history.append(
            {"role": "assistant", "content": final_output, "agent": "HERALD"}
        )
        return final_output

    async def add_agent(self, agent: "BaseAgent") -> None:
        self._agents[agent.agent_id] = agent
        log.info(f"HyperClawGroupChat: added agent {agent.agent_id}")

    async def remove_agent(self, agent_id: str) -> None:
        self._agents.pop(agent_id, None)
        log.info(f"HyperClawGroupChat: removed agent {agent_id}")

    @property
    def agent_count(self) -> int:
        return len(self._agents)
