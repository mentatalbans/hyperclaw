"""
Staleness detection for Civilization nodes.
Identifies content that may be outdated and needs review.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from uuid import UUID

from ..schema import CivilizationNode, NodeType, NodeStatus

logger = logging.getLogger(__name__)


class StalenessLevel(str, Enum):
    FRESH = "fresh"  # Recently updated
    CURRENT = "current"  # Within acceptable range
    AGING = "aging"  # Approaching review date
    STALE = "stale"  # Past review date
    CRITICAL = "critical"  # Significantly overdue


@dataclass
class StalenessResult:
    """Result of staleness check for a single node."""
    node_id: UUID
    node_type: NodeType
    title: str
    level: StalenessLevel
    days_since_update: int
    recommended_action: str | None = None
    priority_score: float = 0.0  # 0-1, higher = more urgent
    metadata: dict = field(default_factory=dict)


@dataclass
class StalenessReport:
    """Aggregate staleness report for multiple nodes."""
    org_id: str
    generated_at: datetime = field(default_factory=datetime.utcnow)
    results: list[StalenessResult] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    @property
    def critical_count(self) -> int:
        return sum(1 for r in self.results if r.level == StalenessLevel.CRITICAL)

    @property
    def stale_count(self) -> int:
        return sum(1 for r in self.results if r.level in {StalenessLevel.STALE, StalenessLevel.CRITICAL})

    @property
    def freshness_score(self) -> float:
        """Overall freshness score (0-100)."""
        if not self.results:
            return 100.0
        level_scores = {
            StalenessLevel.FRESH: 100,
            StalenessLevel.CURRENT: 80,
            StalenessLevel.AGING: 50,
            StalenessLevel.STALE: 20,
            StalenessLevel.CRITICAL: 0,
        }
        total = sum(level_scores[r.level] for r in self.results)
        return total / len(self.results)


class StalenessDetector:
    """
    Detects staleness in civilization nodes based on update patterns,
    node type, and organizational policies.
    """

    # Default thresholds by node type (in days)
    DEFAULT_THRESHOLDS = {
        NodeType.SOP: {"fresh": 30, "current": 90, "aging": 180, "stale": 365},
        NodeType.RUNBOOK: {"fresh": 14, "current": 60, "aging": 120, "stale": 180},
        NodeType.CHECKLIST: {"fresh": 30, "current": 90, "aging": 180, "stale": 365},
        NodeType.JOB_DESCRIPTION: {"fresh": 60, "current": 180, "aging": 365, "stale": 545},
        NodeType.ROLE: {"fresh": 60, "current": 180, "aging": 365, "stale": 545},
        NodeType.ORG_CHART: {"fresh": 7, "current": 30, "aging": 60, "stale": 90},
        NodeType.PERSON: {"fresh": 7, "current": 30, "aging": 60, "stale": 90},
        NodeType.CLIENT_PROFILE: {"fresh": 14, "current": 60, "aging": 120, "stale": 180},
        NodeType.WORKFLOW: {"fresh": 30, "current": 90, "aging": 180, "stale": 365},
        NodeType.POLICY: {"fresh": 90, "current": 180, "aging": 365, "stale": 545},
        NodeType.KNOWLEDGE_ARTICLE: {"fresh": 60, "current": 180, "aging": 365, "stale": 545},
        NodeType.PERSONAL_ROUTINE: {"fresh": 7, "current": 30, "aging": 60, "stale": 90},
    }

    # Priority weights by node type (higher = more important to keep fresh)
    PRIORITY_WEIGHTS = {
        NodeType.SOP: 1.0,
        NodeType.RUNBOOK: 1.2,  # Critical for incidents
        NodeType.CHECKLIST: 0.8,
        NodeType.JOB_DESCRIPTION: 0.6,
        NodeType.ROLE: 0.7,
        NodeType.ORG_CHART: 0.9,
        NodeType.PERSON: 0.5,
        NodeType.CLIENT_PROFILE: 0.8,
        NodeType.WORKFLOW: 0.7,
        NodeType.POLICY: 0.9,
        NodeType.KNOWLEDGE_ARTICLE: 0.5,
        NodeType.PERSONAL_ROUTINE: 0.4,
    }

    def __init__(
        self,
        org_id: str,
        custom_thresholds: dict | None = None,
    ):
        self.org_id = org_id
        self.thresholds = {**self.DEFAULT_THRESHOLDS}
        if custom_thresholds:
            for node_type, thresholds in custom_thresholds.items():
                if node_type in self.thresholds:
                    self.thresholds[node_type].update(thresholds)

    def check_node(
        self,
        node: CivilizationNode,
        reference_date: datetime | None = None,
    ) -> StalenessResult:
        """
        Check staleness of a single node.

        Args:
            node: The node to check
            reference_date: Date to compare against (default: now)

        Returns:
            StalenessResult with staleness level and recommendations
        """
        ref_date = reference_date or datetime.utcnow()
        days_since_update = (ref_date - node.updated_at).days

        thresholds = self.thresholds.get(
            node.node_type,
            {"fresh": 30, "current": 90, "aging": 180, "stale": 365}
        )

        # Determine staleness level
        if days_since_update <= thresholds["fresh"]:
            level = StalenessLevel.FRESH
        elif days_since_update <= thresholds["current"]:
            level = StalenessLevel.CURRENT
        elif days_since_update <= thresholds["aging"]:
            level = StalenessLevel.AGING
        elif days_since_update <= thresholds["stale"]:
            level = StalenessLevel.STALE
        else:
            level = StalenessLevel.CRITICAL

        # Calculate priority score
        weight = self.PRIORITY_WEIGHTS.get(node.node_type, 0.5)
        staleness_factor = min(days_since_update / thresholds["stale"], 2.0)
        priority = min(weight * staleness_factor, 1.0)

        # Generate recommendation
        recommendation = self._get_recommendation(level, node.node_type, days_since_update)

        return StalenessResult(
            node_id=node.id,
            node_type=node.node_type,
            title=node.title,
            level=level,
            days_since_update=days_since_update,
            recommended_action=recommendation,
            priority_score=priority,
            metadata={
                "thresholds": thresholds,
                "weight": weight,
            },
        )

    def check_nodes(
        self,
        nodes: list[CivilizationNode],
        reference_date: datetime | None = None,
    ) -> StalenessReport:
        """
        Check staleness for multiple nodes.

        Args:
            nodes: List of nodes to check
            reference_date: Date to compare against

        Returns:
            StalenessReport with all results
        """
        report = StalenessReport(org_id=self.org_id)

        for node in nodes:
            result = self.check_node(node, reference_date)
            report.results.append(result)

        # Generate summary
        report.summary = self._generate_summary(report.results)

        # Sort by priority
        report.results.sort(key=lambda r: r.priority_score, reverse=True)

        return report

    def _get_recommendation(
        self,
        level: StalenessLevel,
        node_type: NodeType,
        days: int,
    ) -> str | None:
        """Generate action recommendation based on staleness."""
        if level == StalenessLevel.FRESH:
            return None
        elif level == StalenessLevel.CURRENT:
            return None
        elif level == StalenessLevel.AGING:
            return f"Schedule review - {days} days since last update"
        elif level == StalenessLevel.STALE:
            return f"Review urgently - {node_type.value} is {days} days old"
        else:  # CRITICAL
            return f"CRITICAL: {node_type.value} is severely outdated ({days} days)"

    def _generate_summary(self, results: list[StalenessResult]) -> dict:
        """Generate summary statistics."""
        by_level = {}
        by_type = {}

        for r in results:
            by_level[r.level.value] = by_level.get(r.level.value, 0) + 1
            by_type[r.node_type.value] = by_type.get(r.node_type.value, 0) + 1

        # Find most stale nodes
        top_stale = sorted(
            [r for r in results if r.level in {StalenessLevel.STALE, StalenessLevel.CRITICAL}],
            key=lambda x: x.days_since_update,
            reverse=True,
        )[:5]

        return {
            "total_nodes": len(results),
            "by_level": by_level,
            "by_type": by_type,
            "top_stale": [
                {"title": r.title, "days": r.days_since_update, "type": r.node_type.value}
                for r in top_stale
            ],
        }

    def get_review_schedule(
        self,
        nodes: list[CivilizationNode],
        lookahead_days: int = 30,
    ) -> list[dict]:
        """
        Generate a review schedule for the next N days.
        Lists nodes that will become stale and need proactive review.
        """
        schedule = []
        now = datetime.utcnow()
        future = now + timedelta(days=lookahead_days)

        for node in nodes:
            thresholds = self.thresholds.get(
                node.node_type,
                {"fresh": 30, "current": 90, "aging": 180, "stale": 365}
            )

            # Calculate when node will become stale
            stale_date = node.updated_at + timedelta(days=thresholds["stale"])

            if now <= stale_date <= future:
                days_until_stale = (stale_date - now).days
                schedule.append({
                    "node_id": str(node.id),
                    "title": node.title,
                    "node_type": node.node_type.value,
                    "stale_date": stale_date.isoformat(),
                    "days_until_stale": days_until_stale,
                    "recommended_review_date": (stale_date - timedelta(days=7)).isoformat(),
                })

        return sorted(schedule, key=lambda x: x["days_until_stale"])

    def get_health_metrics(self, nodes: list[CivilizationNode]) -> dict:
        """Get overall knowledge health metrics."""
        report = self.check_nodes(nodes)

        return {
            "freshness_score": report.freshness_score,
            "total_nodes": len(nodes),
            "critical_nodes": report.critical_count,
            "stale_nodes": report.stale_count,
            "needs_review": sum(
                1 for r in report.results
                if r.level in {StalenessLevel.AGING, StalenessLevel.STALE, StalenessLevel.CRITICAL}
            ),
            "average_age_days": (
                sum(r.days_since_update for r in report.results) / len(report.results)
                if report.results else 0
            ),
        }
