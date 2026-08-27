"""
Version management for Civilization nodes.
Tracks changes, maintains history, and supports rollback.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4
import json
import hashlib

from ..schema import CivilizationNode

logger = logging.getLogger(__name__)


@dataclass
class NodeVersion:
    """Represents a specific version of a node."""
    version_id: UUID = field(default_factory=uuid4)
    node_id: UUID = field(default_factory=uuid4)
    version_number: str = "1.0.0"
    content_hash: str = ""
    snapshot: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    created_by: str | None = None
    change_summary: str | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def major(self) -> int:
        parts = self.version_number.split(".")
        return int(parts[0]) if parts else 1

    @property
    def minor(self) -> int:
        parts = self.version_number.split(".")
        return int(parts[1]) if len(parts) > 1 else 0

    @property
    def patch(self) -> int:
        parts = self.version_number.split(".")
        return int(parts[2]) if len(parts) > 2 else 0


@dataclass
class VersionHistory:
    """Complete version history for a node."""
    node_id: UUID
    current_version: str = "1.0.0"
    versions: list[NodeVersion] = field(default_factory=list)

    @property
    def version_count(self) -> int:
        return len(self.versions)

    def get_version(self, version_number: str) -> NodeVersion | None:
        """Get a specific version by version number."""
        for v in self.versions:
            if v.version_number == version_number:
                return v
        return None

    def get_latest(self) -> NodeVersion | None:
        """Get the most recent version."""
        if not self.versions:
            return None
        return max(self.versions, key=lambda v: v.created_at)


class VersionManager:
    """
    Manages versions of civilization nodes.
    Supports semantic versioning, history tracking, and rollback.
    """

    def __init__(self, org_id: str, store=None):
        self.org_id = org_id
        self.store = store
        self._histories: dict[UUID, VersionHistory] = {}

    def _compute_hash(self, node: CivilizationNode) -> str:
        """Compute content hash for a node."""
        # Exclude volatile fields from hash
        data = node.model_dump(mode="json", exclude={"updated_at", "embedding"})
        content = json.dumps(data, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _parse_version(self, version: str) -> tuple[int, int, int]:
        """Parse version string into (major, minor, patch)."""
        parts = version.split(".")
        return (
            int(parts[0]) if parts else 1,
            int(parts[1]) if len(parts) > 1 else 0,
            int(parts[2]) if len(parts) > 2 else 0,
        )

    def _increment_version(
        self,
        current: str,
        bump_type: str = "patch",
    ) -> str:
        """Increment version number."""
        major, minor, patch = self._parse_version(current)

        if bump_type == "major":
            return f"{major + 1}.0.0"
        elif bump_type == "minor":
            return f"{major}.{minor + 1}.0"
        else:  # patch
            return f"{major}.{minor}.{patch + 1}"

    def create_version(
        self,
        node: CivilizationNode,
        bump_type: str = "patch",
        change_summary: str | None = None,
        created_by: str | None = None,
    ) -> NodeVersion:
        """
        Create a new version of a node.

        Args:
            node: The node to version
            bump_type: "major", "minor", or "patch"
            change_summary: Description of changes
            created_by: ID of user making the change

        Returns:
            The new NodeVersion
        """
        history = self._histories.get(node.id)

        if history:
            new_version_num = self._increment_version(history.current_version, bump_type)
        else:
            new_version_num = "1.0.0"
            history = VersionHistory(node_id=node.id)
            self._histories[node.id] = history

        version = NodeVersion(
            node_id=node.id,
            version_number=new_version_num,
            content_hash=self._compute_hash(node),
            snapshot=node.model_dump(mode="json"),
            created_by=created_by,
            change_summary=change_summary,
        )

        history.versions.append(version)
        history.current_version = new_version_num

        logger.info(f"Created version {new_version_num} for node {node.id}")
        return version

    def get_history(self, node_id: UUID) -> VersionHistory | None:
        """Get the version history for a node."""
        return self._histories.get(node_id)

    def get_version(self, node_id: UUID, version_number: str) -> NodeVersion | None:
        """Get a specific version of a node."""
        history = self._histories.get(node_id)
        if history:
            return history.get_version(version_number)
        return None

    def get_current_version(self, node_id: UUID) -> str:
        """Get the current version number for a node."""
        history = self._histories.get(node_id)
        return history.current_version if history else "1.0.0"

    def has_changes(self, node: CivilizationNode) -> bool:
        """Check if a node has uncommitted changes."""
        history = self._histories.get(node.id)
        if not history or not history.versions:
            return True

        current_hash = self._compute_hash(node)
        latest = history.get_latest()
        return latest is None or latest.content_hash != current_hash

    def restore_version(
        self,
        node_id: UUID,
        version_number: str,
    ) -> CivilizationNode | None:
        """
        Restore a node to a previous version.
        Returns the restored node or None if version not found.
        """
        version = self.get_version(node_id, version_number)
        if not version:
            return None

        # Reconstruct node from snapshot
        return CivilizationNode(**version.snapshot)

    def compare_versions(
        self,
        node_id: UUID,
        version1: str,
        version2: str,
    ) -> dict | None:
        """
        Compare two versions of a node.
        Returns a dict with added, removed, and changed fields.
        """
        v1 = self.get_version(node_id, version1)
        v2 = self.get_version(node_id, version2)

        if not v1 or not v2:
            return None

        changes = {
            "added": {},
            "removed": {},
            "changed": {},
        }

        s1 = v1.snapshot
        s2 = v2.snapshot

        # Find added and changed
        for key, val in s2.items():
            if key not in s1:
                changes["added"][key] = val
            elif s1[key] != val:
                changes["changed"][key] = {"old": s1[key], "new": val}

        # Find removed
        for key in s1:
            if key not in s2:
                changes["removed"][key] = s1[key]

        return changes

    def get_version_timeline(self, node_id: UUID) -> list[dict]:
        """Get a timeline of all versions for a node."""
        history = self._histories.get(node_id)
        if not history:
            return []

        return [
            {
                "version": v.version_number,
                "created_at": v.created_at.isoformat(),
                "created_by": v.created_by,
                "change_summary": v.change_summary,
                "content_hash": v.content_hash,
            }
            for v in sorted(history.versions, key=lambda x: x.created_at)
        ]

    def cleanup_old_versions(
        self,
        node_id: UUID,
        keep_count: int = 10,
    ) -> int:
        """
        Remove old versions, keeping the most recent N.
        Returns count of removed versions.
        """
        history = self._histories.get(node_id)
        if not history or len(history.versions) <= keep_count:
            return 0

        sorted_versions = sorted(history.versions, key=lambda v: v.created_at, reverse=True)
        to_remove = sorted_versions[keep_count:]

        for v in to_remove:
            history.versions.remove(v)

        return len(to_remove)

    def export_version(self, node_id: UUID, version_number: str) -> dict | None:
        """Export a version as a portable dict."""
        version = self.get_version(node_id, version_number)
        if not version:
            return None

        return {
            "version_id": str(version.version_id),
            "node_id": str(version.node_id),
            "version_number": version.version_number,
            "content_hash": version.content_hash,
            "snapshot": version.snapshot,
            "created_at": version.created_at.isoformat(),
            "created_by": version.created_by,
            "change_summary": version.change_summary,
            "metadata": version.metadata,
        }
