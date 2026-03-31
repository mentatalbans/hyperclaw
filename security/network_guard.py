"""
HyperShield NetworkGuard — enforces egress policy for all agent network requests.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from .policy_engine import PolicyEngine

if TYPE_CHECKING:
    from .audit_logger import AuditLogger

log = logging.getLogger("hyperclaw.network_guard")


class NetworkEgressBlockedError(Exception):
    """Raised when an agent attempts an egress connection not permitted by policy."""
    def __init__(self, agent_id: str, url: str, policy: str) -> None:
        self.agent_id = agent_id
        self.url = url
        self.policy = policy
        super().__init__(
            f"NetworkGuard blocked egress: agent={agent_id!r} url={url!r} policy={policy!r}"
        )


class NetworkGuard:
    """
    Checks outbound connections against HyperShield policy.
    Logs every check to AuditLogger.
    """

    def __init__(
        self,
        policy_engine: PolicyEngine,
        audit_logger: "AuditLogger",
    ) -> None:
        self._policy = policy_engine
        self._audit = audit_logger

    def check_egress(self, agent_id: str, url: str) -> bool:
        """
        Check if agent is allowed to connect to url.
        Logs to audit_logger.
        Raises NetworkEgressBlockedError if blocked.
        Returns True if allowed.
        """
        allowed = self._policy.is_egress_allowed(agent_id, url)
        policy_profile = self._policy.active.profile

        # Fire-and-forget audit log (sync context — schedule if async loop available)
        self._log_audit(agent_id, url, allowed, policy_profile)

        if not allowed:
            raise NetworkEgressBlockedError(agent_id, url, policy_profile)
        return True

    def _log_audit(self, agent_id: str, url: str, allowed: bool, policy: str) -> None:
        import asyncio
        coro = self._audit.log(
            event_type="network_request",
            action="egress",
            allowed=allowed,
            agent_id=agent_id,
            target=url,
            policy_applied=policy,
        )
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)
        except RuntimeError:
            pass  # Sync context — skip

    def wrap_client(
        self,
        agent_id: str,
        client: httpx.AsyncClient,
    ) -> httpx.AsyncClient:
        """
        Return a wrapped AsyncClient that calls check_egress before every request.
        """
        guard = self

        class GuardedTransport(httpx.AsyncBaseTransport):
            def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
                self._inner = inner

            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                guard.check_egress(agent_id, str(request.url))
                return await self._inner.handle_async_request(request)

        transport = client._transport  # type: ignore[attr-defined]
        client._transport = GuardedTransport(transport)  # type: ignore[attr-defined]
        return client
