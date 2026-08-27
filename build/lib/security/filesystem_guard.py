"""
HyperShield FilesystemGuard — enforces sandbox filesystem access policy.
"""
from __future__ import annotations

import os
import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING

from .policy_engine import PolicyEngine

if TYPE_CHECKING:
    from .audit_logger import AuditLogger

log = logging.getLogger("hyperclaw.filesystem_guard")


class FilesystemAccessBlockedError(Exception):
    """Raised when an agent attempts to access a path outside the sandbox."""
    def __init__(self, agent_id: str, path: str, allowed_root: str) -> None:
        self.agent_id = agent_id
        self.path = path
        self.allowed_root = allowed_root
        super().__init__(
            f"FilesystemGuard blocked access: agent={agent_id!r} path={path!r} "
            f"allowed_root={allowed_root!r}"
        )


class FilesystemGuard:
    """
    Validates filesystem read/write access against HyperShield policy.
    Logs every access attempt to AuditLogger.
    """

    def __init__(
        self,
        policy_engine: PolicyEngine,
        audit_logger: "AuditLogger",
    ) -> None:
        self._policy = policy_engine
        self._audit = audit_logger

    def validate_read(self, agent_id: str, path: str) -> bool:
        """
        Return True if agent is allowed to read path.
        Raises FilesystemAccessBlockedError if outside sandbox.
        """
        return self._check(agent_id, path, "read")

    def validate_write(self, agent_id: str, path: str) -> bool:
        """
        Return True if agent is allowed to write path.
        Raises FilesystemAccessBlockedError if outside sandbox.
        """
        return self._check(agent_id, path, "write")

    def _check(self, agent_id: str, path: str, action: str) -> bool:
        allowed = self._policy.validate_filesystem_access(agent_id, path)
        fs = self._policy.active.filesystem
        sandbox_root = fs.sandbox_root

        self._log_audit(agent_id, path, action, allowed)

        if not allowed:
            raise FilesystemAccessBlockedError(agent_id, path, sandbox_root)
        return True

    def _log_audit(self, agent_id: str, path: str, action: str, allowed: bool) -> None:
        import asyncio
        coro = self._audit.log(
            event_type="file_access",
            action=action,
            allowed=allowed,
            agent_id=agent_id,
            target=path,
            policy_applied=self._policy.active.profile,
        )
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)
        except RuntimeError:
            pass

    @contextmanager
    def safe_open(self, agent_id: str, path: str, mode: str = "r"):
        """
        Context manager — validates access then opens file.
        Raises FilesystemAccessBlockedError if access denied.
        """
        if "w" in mode or "a" in mode or "x" in mode:
            self.validate_write(agent_id, path)
        else:
            self.validate_read(agent_id, path)
        f = open(path, mode)
        try:
            yield f
        finally:
            f.close()
