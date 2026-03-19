"""
HyperShield — top-level security coordinator for HyperClaw.
Enforces policies, guards filesystem and network access, and coordinates audit logging.
"""
from __future__ import annotations
import logging
from .policy_engine import PolicyEngine
from .audit_logger import AuditLogger

log = logging.getLogger("hyperclaw.hypershield")


class HyperShield:
    """
    Central security coordinator. Wraps PolicyEngine, AuditLogger,
    NetworkGuard, and FilesystemGuard into a single interface.
    """

    def __init__(self, policy_name: str = "default") -> None:
        self.policy = PolicyEngine(policy_name)
        self.audit = AuditLogger()

    def check_action(self, agent_id: str, action: str, resource: str) -> bool:
        """
        Check if an agent is allowed to perform an action on a resource.
        Logs all checks to audit log.
        """
        allowed = self.policy.is_allowed(action, resource)
        self.audit.log(agent_id=agent_id, action=action, resource=resource, allowed=allowed)
        if not allowed:
            log.warning(f"HyperShield BLOCKED: agent={agent_id} action={action} resource={resource}")
        return allowed

    def require_claude_verification(self, model_id: str) -> bool:
        """ChatJimmy outputs always require Claude verification before certification."""
        return model_id == "chatjimmy"
