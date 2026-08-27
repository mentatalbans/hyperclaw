"""
HyperShield — central security orchestrator for HyperClaw.
Coordinates PolicyEngine, AuditLogger, NetworkGuard, and FilesystemGuard.
"""
from __future__ import annotations

import logging
from typing import Optional

import asyncpg

from .policy_engine import PolicyEngine
from .audit_logger import AuditLogger
from .network_guard import NetworkGuard
from .filesystem_guard import FilesystemGuard

log = logging.getLogger("hyperclaw.hypershield")


class HyperShield:
    """
    Top-level security coordinator.
    Initialize once per application; inject into all agents and clients.
    """

    def __init__(
        self,
        policy_path: str,
        pool: asyncpg.Pool,
    ) -> None:
        self._policy_path = policy_path
        self._pool = pool
        self.policy_engine = PolicyEngine()
        self.audit_logger = AuditLogger(pool)
        self.network_guard = NetworkGuard(self.policy_engine, self.audit_logger)
        self.filesystem_guard = FilesystemGuard(self.policy_engine, self.audit_logger)

    async def initialize(self) -> None:
        """Load policy, validate DB connection, log startup event."""
        try:
            self.policy_engine.load(self._policy_path)
        except FileNotFoundError:
            log.warning(
                f"HyperShield: policy file not found at {self._policy_path!r}, "
                "using defaults"
            )

        # Validate DB (non-fatal — audit log writes will fail gracefully if DB is down)
        try:
            async with self._pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
        except Exception as e:
            log.warning(f"HyperShield: DB validation failed (audit log may be unavailable): {e}")

        await self.audit_logger.log(
            event_type="policy_reload",
            action="initialize",
            allowed=True,
            policy_applied=self.policy_engine.active.profile,
            metadata={"policy_path": self._policy_path},
        )
        log.info(
            f"HyperShield initialized — "
            f"policy='{self.policy_engine.active.profile}'"
        )

    def reload_policy(self, policy_path: str) -> None:
        """Hot-reload policy via PolicyEngine. No restart required."""
        self.policy_engine.reload(policy_path)
        log.info(f"HyperShield: policy reloaded from {policy_path!r}")

    def get_network_guard(self) -> NetworkGuard:
        return self.network_guard

    def get_filesystem_guard(self) -> FilesystemGuard:
        return self.filesystem_guard

    def get_audit_logger(self) -> AuditLogger:
        return self.audit_logger

    async def check_inference_call(
        self,
        agent_id: str,
        model_id: str,
    ) -> bool:
        """
        Log an inference call to the audit log.
        Inference is always allowed — but always logged for cost/usage tracking.
        Returns True.
        """
        await self.audit_logger.log(
            event_type="inference_call",
            action="inference",
            allowed=True,
            agent_id=agent_id,
            model_used=model_id,
            policy_applied=self.policy_engine.active.profile,
        )
        return True
