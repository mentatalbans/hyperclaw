"""
HyperShield AuditLogger — immutable append-only audit log for all agent actions.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

log = logging.getLogger("hyperclaw.audit")


@dataclass
class AuditEntry:
    agent_id: str
    action: str
    resource: str
    allowed: bool
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class AuditLogger:
    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []

    def log(self, agent_id: str, action: str, resource: str, allowed: bool) -> None:
        entry = AuditEntry(agent_id=agent_id, action=action, resource=resource, allowed=allowed)
        self._entries.append(entry)
        log.info(f"AUDIT | agent={agent_id} action={action} resource={resource} allowed={allowed}")

    def entries(self) -> list[AuditEntry]:
        return list(self._entries)

    def denied_entries(self) -> list[AuditEntry]:
        return [e for e in self._entries if not e.allowed]
