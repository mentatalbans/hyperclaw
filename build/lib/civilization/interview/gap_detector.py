"""
Gap detector for identifying missing or incomplete knowledge in the Civilization layer.
Analyzes existing nodes to find coverage gaps, outdated content, and missing relationships.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from uuid import UUID

from ..schema import (
    NodeType, NodeStatus, CivilizationNode,
    SOP, Role, Person, Checklist, Runbook, Workflow, OrgChart,
)

logger = logging.getLogger(__name__)


class GapSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class GapType(str, Enum):
    MISSING_NODE = "missing_node"
    INCOMPLETE_NODE = "incomplete_node"
    ORPHAN_NODE = "orphan_node"
    MISSING_RELATIONSHIP = "missing_relationship"
    STALE_CONTENT = "stale_content"
    MISSING_OWNER = "missing_owner"
    COVERAGE_GAP = "coverage_gap"
    INCONSISTENCY = "inconsistency"


@dataclass
class KnowledgeGap:
    """Represents a detected gap in organizational knowledge."""
    id: UUID
    gap_type: GapType
    severity: GapSeverity
    title: str
    description: str
    affected_node_id: UUID | None = None
    affected_node_type: NodeType | None = None
    suggested_action: str | None = None
    detected_at: datetime = field(default_factory=datetime.utcnow)
    resolved_at: datetime | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def is_resolved(self) -> bool:
        return self.resolved_at is not None


@dataclass
class GapReport:
    """Summary report of knowledge gaps."""
    org_id: str
    generated_at: datetime = field(default_factory=datetime.utcnow)
    gaps: list[KnowledgeGap] = field(default_factory=list)
    coverage_score: float = 0.0  # 0-100
    health_score: float = 0.0  # 0-100
    recommendations: list[str] = field(default_factory=list)

    @property
    def critical_count(self) -> int:
        return sum(1 for g in self.gaps if g.severity == GapSeverity.CRITICAL)

    @property
    def high_count(self) -> int:
        return sum(1 for g in self.gaps if g.severity == GapSeverity.HIGH)

    def by_type(self, gap_type: GapType) -> list[KnowledgeGap]:
        return [g for g in self.gaps if g.gap_type == gap_type]


class GapDetector:
    """
    Detects gaps and inconsistencies in organizational knowledge.
    Analyzes nodes, relationships, and coverage to identify areas needing attention.
    """

    # Thresholds for gap detection
    STALE_DAYS_WARNING = 90
    STALE_DAYS_CRITICAL = 180
    MIN_SOP_STEPS = 2
    MIN_CHECKLIST_ITEMS = 2
    MIN_ROLE_RESPONSIBILITIES = 3

    def __init__(self, org_id: str, store=None):
        self.org_id = org_id
        self.store = store

    async def analyze(self, nodes: list[CivilizationNode]) -> GapReport:
        """
        Perform comprehensive gap analysis on a set of nodes.

        Args:
            nodes: List of CivilizationNode instances to analyze

        Returns:
            GapReport with all detected gaps and recommendations
        """
        from uuid import uuid4

        report = GapReport(org_id=self.org_id)
        gaps: list[KnowledgeGap] = []

        # Group nodes by type
        nodes_by_type: dict[NodeType, list[CivilizationNode]] = {}
        for node in nodes:
            nodes_by_type.setdefault(node.node_type, []).append(node)

        # Run all detection checks
        gaps.extend(self._check_incomplete_nodes(nodes))
        gaps.extend(self._check_orphan_nodes(nodes, nodes_by_type))
        gaps.extend(self._check_stale_content(nodes))
        gaps.extend(self._check_missing_owners(nodes))
        gaps.extend(self._check_coverage_gaps(nodes_by_type))
        gaps.extend(self._check_relationship_gaps(nodes, nodes_by_type))

        report.gaps = gaps
        report.coverage_score = self._calculate_coverage_score(nodes_by_type)
        report.health_score = self._calculate_health_score(gaps, len(nodes))
        report.recommendations = self._generate_recommendations(gaps, nodes_by_type)

        return report

    def _check_incomplete_nodes(self, nodes: list[CivilizationNode]) -> list[KnowledgeGap]:
        """Check for nodes with missing required fields."""
        from uuid import uuid4
        gaps = []

        for node in nodes:
            issues = []

            if isinstance(node, SOP):
                if len(node.steps) < self.MIN_SOP_STEPS:
                    issues.append("SOP has too few steps")
                if not node.purpose:
                    issues.append("SOP missing purpose")
                if not node.roles_involved:
                    issues.append("SOP missing roles")

            elif isinstance(node, Role):
                if len(node.responsibilities) < self.MIN_ROLE_RESPONSIBILITIES:
                    issues.append("Role has too few responsibilities")
                if not node.accountabilities:
                    issues.append("Role missing accountabilities")

            elif isinstance(node, Checklist):
                if len(node.items) < self.MIN_CHECKLIST_ITEMS:
                    issues.append("Checklist has too few items")

            elif isinstance(node, Runbook):
                if not node.steps:
                    issues.append("Runbook has no steps")
                if not node.escalation_contacts:
                    issues.append("Runbook missing escalation contacts")

            if issues:
                gaps.append(KnowledgeGap(
                    id=uuid4(),
                    gap_type=GapType.INCOMPLETE_NODE,
                    severity=GapSeverity.MEDIUM,
                    title=f"Incomplete {node.node_type.value}: {node.title}",
                    description="; ".join(issues),
                    affected_node_id=node.id,
                    affected_node_type=node.node_type,
                    suggested_action=f"Review and complete the {node.node_type.value}",
                ))

        return gaps

    def _check_orphan_nodes(
        self,
        nodes: list[CivilizationNode],
        nodes_by_type: dict[NodeType, list[CivilizationNode]],
    ) -> list[KnowledgeGap]:
        """Check for nodes with no relationships."""
        from uuid import uuid4
        gaps = []

        # SOPs without related SOPs or roles
        for sop in nodes_by_type.get(NodeType.SOP, []):
            if isinstance(sop, SOP):
                if not sop.related_sops and not sop.roles_involved:
                    gaps.append(KnowledgeGap(
                        id=uuid4(),
                        gap_type=GapType.ORPHAN_NODE,
                        severity=GapSeverity.LOW,
                        title=f"Isolated SOP: {sop.title}",
                        description="SOP has no related SOPs or roles defined",
                        affected_node_id=sop.id,
                        affected_node_type=NodeType.SOP,
                        suggested_action="Link this SOP to related procedures and roles",
                    ))

        return gaps

    def _check_stale_content(self, nodes: list[CivilizationNode]) -> list[KnowledgeGap]:
        """Check for outdated content."""
        from uuid import uuid4
        gaps = []
        now = datetime.utcnow()

        for node in nodes:
            age_days = (now - node.updated_at).days

            if age_days > self.STALE_DAYS_CRITICAL:
                gaps.append(KnowledgeGap(
                    id=uuid4(),
                    gap_type=GapType.STALE_CONTENT,
                    severity=GapSeverity.HIGH,
                    title=f"Critically stale: {node.title}",
                    description=f"Not updated in {age_days} days",
                    affected_node_id=node.id,
                    affected_node_type=node.node_type,
                    suggested_action="Review and update this content urgently",
                    metadata={"days_since_update": age_days},
                ))
            elif age_days > self.STALE_DAYS_WARNING:
                gaps.append(KnowledgeGap(
                    id=uuid4(),
                    gap_type=GapType.STALE_CONTENT,
                    severity=GapSeverity.MEDIUM,
                    title=f"Stale content: {node.title}",
                    description=f"Not updated in {age_days} days",
                    affected_node_id=node.id,
                    affected_node_type=node.node_type,
                    suggested_action="Consider reviewing this content",
                    metadata={"days_since_update": age_days},
                ))

        return gaps

    def _check_missing_owners(self, nodes: list[CivilizationNode]) -> list[KnowledgeGap]:
        """Check for nodes without owners."""
        from uuid import uuid4
        gaps = []

        for node in nodes:
            if not node.owner_id:
                # SOPs and Runbooks without owners are higher severity
                severity = GapSeverity.HIGH if node.node_type in {NodeType.SOP, NodeType.RUNBOOK} else GapSeverity.LOW
                gaps.append(KnowledgeGap(
                    id=uuid4(),
                    gap_type=GapType.MISSING_OWNER,
                    severity=severity,
                    title=f"No owner: {node.title}",
                    description=f"{node.node_type.value} has no assigned owner",
                    affected_node_id=node.id,
                    affected_node_type=node.node_type,
                    suggested_action="Assign an owner to this content",
                ))

        return gaps

    def _check_coverage_gaps(
        self,
        nodes_by_type: dict[NodeType, list[CivilizationNode]],
    ) -> list[KnowledgeGap]:
        """Check for missing essential node types."""
        from uuid import uuid4
        gaps = []

        # Essential node types for a well-documented org
        essential_types = {
            NodeType.ORG_CHART: "Organization structure",
            NodeType.ROLE: "Role definitions",
            NodeType.SOP: "Standard operating procedures",
        }

        for node_type, description in essential_types.items():
            if node_type not in nodes_by_type or len(nodes_by_type[node_type]) == 0:
                gaps.append(KnowledgeGap(
                    id=uuid4(),
                    gap_type=GapType.COVERAGE_GAP,
                    severity=GapSeverity.HIGH,
                    title=f"Missing {description}",
                    description=f"No {node_type.value} nodes found in knowledge base",
                    suggested_action=f"Create {node_type.value} documentation",
                ))

        return gaps

    def _check_relationship_gaps(
        self,
        nodes: list[CivilizationNode],
        nodes_by_type: dict[NodeType, list[CivilizationNode]],
    ) -> list[KnowledgeGap]:
        """Check for missing relationships between nodes."""
        from uuid import uuid4
        gaps = []

        # Check if roles mentioned in SOPs exist
        role_titles = {
            r.role_title.lower() for r in nodes_by_type.get(NodeType.ROLE, [])
            if isinstance(r, Role)
        }

        for sop in nodes_by_type.get(NodeType.SOP, []):
            if isinstance(sop, SOP):
                for role_name in sop.roles_involved:
                    if role_name.lower() not in role_titles:
                        gaps.append(KnowledgeGap(
                            id=uuid4(),
                            gap_type=GapType.MISSING_RELATIONSHIP,
                            severity=GapSeverity.MEDIUM,
                            title=f"Undefined role: {role_name}",
                            description=f"Role '{role_name}' referenced in SOP '{sop.title}' but not defined",
                            affected_node_id=sop.id,
                            affected_node_type=NodeType.SOP,
                            suggested_action=f"Create role definition for '{role_name}'",
                        ))

        return gaps

    def _calculate_coverage_score(
        self,
        nodes_by_type: dict[NodeType, list[CivilizationNode]],
    ) -> float:
        """Calculate coverage score (0-100) based on node type diversity."""
        essential_types = {NodeType.ORG_CHART, NodeType.ROLE, NodeType.SOP, NodeType.CHECKLIST}
        covered = sum(1 for t in essential_types if t in nodes_by_type and nodes_by_type[t])
        return (covered / len(essential_types)) * 100

    def _calculate_health_score(self, gaps: list[KnowledgeGap], total_nodes: int) -> float:
        """Calculate health score (0-100) based on gap severity."""
        if total_nodes == 0:
            return 0.0

        # Weight by severity
        severity_weights = {
            GapSeverity.CRITICAL: 4,
            GapSeverity.HIGH: 2,
            GapSeverity.MEDIUM: 1,
            GapSeverity.LOW: 0.5,
        }

        total_weight = sum(severity_weights[g.severity] for g in gaps)
        # Higher weight = lower score
        max_weight = total_nodes * 2  # Assume average 2 issues per node is worst case
        score = max(0, 100 - (total_weight / max_weight) * 100)
        return min(100, score)

    def _generate_recommendations(
        self,
        gaps: list[KnowledgeGap],
        nodes_by_type: dict[NodeType, list[CivilizationNode]],
    ) -> list[str]:
        """Generate prioritized recommendations."""
        recommendations = []

        # Critical issues first
        critical = [g for g in gaps if g.severity == GapSeverity.CRITICAL]
        if critical:
            recommendations.append(f"Address {len(critical)} critical gaps immediately")

        # Coverage gaps
        coverage_gaps = [g for g in gaps if g.gap_type == GapType.COVERAGE_GAP]
        if coverage_gaps:
            types = ", ".join(g.title.replace("Missing ", "") for g in coverage_gaps)
            recommendations.append(f"Document missing areas: {types}")

        # Stale content
        stale = [g for g in gaps if g.gap_type == GapType.STALE_CONTENT]
        if stale:
            recommendations.append(f"Review {len(stale)} pieces of stale content")

        # Missing owners
        unowned = [g for g in gaps if g.gap_type == GapType.MISSING_OWNER]
        if unowned:
            recommendations.append(f"Assign owners to {len(unowned)} unowned items")

        return recommendations
