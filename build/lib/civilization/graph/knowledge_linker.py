"""
Knowledge linker for establishing relationships between civilization nodes.
Supports automatic link detection, relationship inference, and graph traversal.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID, uuid4
import re

from ..schema import (
    CivilizationNode, NodeType,
    SOP, Role, Person, Checklist, Runbook, Workflow, JobDescription,
)

logger = logging.getLogger(__name__)


class LinkType(str, Enum):
    # Hierarchical relationships
    PARENT_OF = "parent_of"
    CHILD_OF = "child_of"

    # Process relationships
    PRECEDES = "precedes"
    FOLLOWS = "follows"
    TRIGGERS = "triggers"
    TRIGGERED_BY = "triggered_by"

    # Ownership/responsibility
    OWNS = "owns"
    OWNED_BY = "owned_by"
    RESPONSIBLE_FOR = "responsible_for"
    ASSIGNED_TO = "assigned_to"

    # Reference relationships
    REFERENCES = "references"
    REFERENCED_BY = "referenced_by"
    RELATED_TO = "related_to"

    # Role relationships
    REQUIRES_ROLE = "requires_role"
    REQUIRED_BY_ROLE = "required_by_role"
    USES_TOOL = "uses_tool"

    # Version relationships
    VERSION_OF = "version_of"
    SUPERSEDES = "supersedes"
    SUPERSEDED_BY = "superseded_by"


@dataclass
class KnowledgeLink:
    """A link between two civilization nodes."""
    id: UUID = field(default_factory=uuid4)
    source_id: UUID = field(default_factory=uuid4)
    target_id: UUID = field(default_factory=uuid4)
    link_type: LinkType = LinkType.RELATED_TO
    strength: float = 1.0  # 0.0-1.0, indicates confidence/relevance
    auto_detected: bool = False
    description: str | None = None
    metadata: dict = field(default_factory=dict)

    def inverse(self) -> "KnowledgeLink":
        """Get the inverse of this link (swap source and target, flip type)."""
        inverse_types = {
            LinkType.PARENT_OF: LinkType.CHILD_OF,
            LinkType.CHILD_OF: LinkType.PARENT_OF,
            LinkType.PRECEDES: LinkType.FOLLOWS,
            LinkType.FOLLOWS: LinkType.PRECEDES,
            LinkType.TRIGGERS: LinkType.TRIGGERED_BY,
            LinkType.TRIGGERED_BY: LinkType.TRIGGERS,
            LinkType.OWNS: LinkType.OWNED_BY,
            LinkType.OWNED_BY: LinkType.OWNS,
            LinkType.REFERENCES: LinkType.REFERENCED_BY,
            LinkType.REFERENCED_BY: LinkType.REFERENCES,
            LinkType.SUPERSEDES: LinkType.SUPERSEDED_BY,
            LinkType.SUPERSEDED_BY: LinkType.SUPERSEDES,
        }
        return KnowledgeLink(
            source_id=self.target_id,
            target_id=self.source_id,
            link_type=inverse_types.get(self.link_type, self.link_type),
            strength=self.strength,
            auto_detected=self.auto_detected,
            metadata=self.metadata,
        )


class KnowledgeLinker:
    """
    Establishes and manages relationships between civilization nodes.
    Supports automatic link detection and relationship inference.
    """

    def __init__(self, org_id: str, store=None):
        self.org_id = org_id
        self.store = store
        self._links: list[KnowledgeLink] = []
        self._link_index: dict[UUID, list[KnowledgeLink]] = {}

    def add_link(self, link: KnowledgeLink) -> KnowledgeLink:
        """Add a link to the graph."""
        self._links.append(link)
        self._link_index.setdefault(link.source_id, []).append(link)
        self._link_index.setdefault(link.target_id, []).append(link.inverse())
        return link

    def create_link(
        self,
        source_id: UUID,
        target_id: UUID,
        link_type: LinkType,
        strength: float = 1.0,
        description: str | None = None,
    ) -> KnowledgeLink:
        """Create and add a new link."""
        link = KnowledgeLink(
            source_id=source_id,
            target_id=target_id,
            link_type=link_type,
            strength=strength,
            description=description,
        )
        return self.add_link(link)

    def get_links_from(self, node_id: UUID) -> list[KnowledgeLink]:
        """Get all links originating from a node."""
        return [l for l in self._link_index.get(node_id, []) if l.source_id == node_id]

    def get_links_to(self, node_id: UUID) -> list[KnowledgeLink]:
        """Get all links pointing to a node."""
        return [l for l in self._links if l.target_id == node_id]

    def get_all_links(self, node_id: UUID) -> list[KnowledgeLink]:
        """Get all links involving a node."""
        return self._link_index.get(node_id, [])

    def get_links_by_type(self, node_id: UUID, link_type: LinkType) -> list[KnowledgeLink]:
        """Get links of a specific type from a node."""
        return [l for l in self.get_links_from(node_id) if l.link_type == link_type]

    def detect_links(self, nodes: list[CivilizationNode]) -> list[KnowledgeLink]:
        """
        Automatically detect links between nodes based on content analysis.
        """
        detected_links: list[KnowledgeLink] = []
        node_map = {n.id: n for n in nodes}

        # Build lookup tables
        role_map: dict[str, UUID] = {}
        sop_map: dict[str, UUID] = {}

        for node in nodes:
            if isinstance(node, Role):
                role_map[node.role_title.lower()] = node.id
            elif isinstance(node, SOP):
                sop_map[node.title.lower()] = node.id

        # Detect SOP -> Role links
        for node in nodes:
            if isinstance(node, SOP):
                for role_name in node.roles_involved:
                    role_id = role_map.get(role_name.lower())
                    if role_id:
                        link = KnowledgeLink(
                            source_id=node.id,
                            target_id=role_id,
                            link_type=LinkType.REQUIRES_ROLE,
                            auto_detected=True,
                            strength=0.9,
                        )
                        detected_links.append(link)

        # Detect SOP -> SOP links (related_sops field)
        for node in nodes:
            if isinstance(node, SOP):
                for related_id in node.related_sops:
                    if related_id in node_map:
                        link = KnowledgeLink(
                            source_id=node.id,
                            target_id=related_id,
                            link_type=LinkType.RELATED_TO,
                            auto_detected=True,
                            strength=0.8,
                        )
                        detected_links.append(link)

        # Detect Checklist -> SOP links
        for node in nodes:
            if isinstance(node, Checklist) and node.related_sop_id:
                if node.related_sop_id in node_map:
                    link = KnowledgeLink(
                        source_id=node.id,
                        target_id=node.related_sop_id,
                        link_type=LinkType.CHILD_OF,
                        auto_detected=True,
                        strength=1.0,
                    )
                    detected_links.append(link)

        # Detect Person -> Role links
        for node in nodes:
            if isinstance(node, Person) and node.role_id:
                if node.role_id in node_map:
                    link = KnowledgeLink(
                        source_id=node.id,
                        target_id=node.role_id,
                        link_type=LinkType.ASSIGNED_TO,
                        auto_detected=True,
                        strength=1.0,
                    )
                    detected_links.append(link)

        # Detect text-based links using title/content matching
        detected_links.extend(self._detect_text_links(nodes))

        return detected_links

    def _detect_text_links(self, nodes: list[CivilizationNode]) -> list[KnowledgeLink]:
        """Detect links based on text content analysis."""
        links = []

        # Build title index
        title_to_id: dict[str, UUID] = {}
        for node in nodes:
            # Normalize title
            title_key = node.title.lower().strip()
            title_to_id[title_key] = node.id

        # Look for mentions of other nodes' titles in content
        for node in nodes:
            content = self._get_node_content(node)
            if not content:
                continue

            for title, target_id in title_to_id.items():
                if target_id == node.id:
                    continue
                # Simple substring matching (could be enhanced with NLP)
                if title in content.lower() and len(title) > 5:
                    links.append(KnowledgeLink(
                        source_id=node.id,
                        target_id=target_id,
                        link_type=LinkType.REFERENCES,
                        auto_detected=True,
                        strength=0.6,
                    ))

        return links

    def _get_node_content(self, node: CivilizationNode) -> str:
        """Extract searchable text content from a node."""
        parts = [node.title]

        if isinstance(node, SOP):
            parts.append(node.purpose)
            parts.append(node.scope)
            for step in node.steps:
                parts.append(step.title)
                parts.append(step.description)
        elif isinstance(node, Role):
            parts.extend(node.responsibilities)
            parts.extend(node.accountabilities)
        elif isinstance(node, JobDescription):
            parts.append(node.summary)
            parts.extend(node.responsibilities)

        return " ".join(filter(None, parts))

    def find_related(
        self,
        node_id: UUID,
        max_depth: int = 2,
        link_types: list[LinkType] | None = None,
    ) -> list[tuple[UUID, int, LinkType]]:
        """
        Find all related nodes within a certain graph distance.
        Returns list of (node_id, distance, link_type).
        """
        results: list[tuple[UUID, int, LinkType]] = []
        visited = {node_id}
        queue: list[tuple[UUID, int]] = [(node_id, 0)]

        while queue:
            current_id, depth = queue.pop(0)
            if depth >= max_depth:
                continue

            for link in self.get_links_from(current_id):
                if link_types and link.link_type not in link_types:
                    continue
                if link.target_id not in visited:
                    visited.add(link.target_id)
                    results.append((link.target_id, depth + 1, link.link_type))
                    queue.append((link.target_id, depth + 1))

        return results

    def compute_importance(self, node_id: UUID) -> float:
        """
        Compute a simple importance score based on incoming links.
        Similar to PageRank but simplified.
        """
        incoming = self.get_links_to(node_id)
        # Weight by link strength
        return sum(l.strength for l in incoming)

    def get_link_statistics(self) -> dict:
        """Get statistics about links in the graph."""
        type_counts: dict[str, int] = {}
        for link in self._links:
            type_counts[link.link_type.value] = type_counts.get(link.link_type.value, 0) + 1

        return {
            "total_links": len(self._links),
            "auto_detected": sum(1 for l in self._links if l.auto_detected),
            "manual": sum(1 for l in self._links if not l.auto_detected),
            "by_type": type_counts,
            "average_strength": sum(l.strength for l in self._links) / len(self._links) if self._links else 0,
        }

    def to_dict(self) -> dict:
        """Export all links to dictionary format."""
        return {
            "org_id": self.org_id,
            "links": [
                {
                    "id": str(l.id),
                    "source_id": str(l.source_id),
                    "target_id": str(l.target_id),
                    "link_type": l.link_type.value,
                    "strength": l.strength,
                    "auto_detected": l.auto_detected,
                    "description": l.description,
                    "metadata": l.metadata,
                }
                for l in self._links
            ],
        }

    def bulk_add_links(self, links: list[KnowledgeLink]) -> int:
        """Add multiple links at once. Returns count of links added."""
        for link in links:
            self.add_link(link)
        return len(links)
