"""
AutoGen Bridge — compatibility layer for Microsoft AutoGen multi-agent workflows.
Allows HyperClaw agents to participate in AutoGen conversation graphs.
Stub for v0.1.0-alpha.
"""
from __future__ import annotations
import logging

log = logging.getLogger("hyperclaw.autogen_bridge")


class AutoGenBridge:
    """Bridge between HyperSwarm and AutoGen agent protocols. Full implementation in v0.2.0."""

    def __init__(self) -> None:
        self._connected = False

    async def connect(self, autogen_config: dict) -> None:
        log.info("AutoGenBridge: connection stub — full implementation in v0.2.0")
        self._connected = True

    async def dispatch(self, agent_id: str, message: str) -> str:
        if not self._connected:
            raise RuntimeError("AutoGenBridge not connected")
        return f"[AutoGenBridge stub] {agent_id}: {message}"
