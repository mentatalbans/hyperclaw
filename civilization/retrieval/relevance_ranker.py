"""
Relevance ranking for retrieved civilization nodes.
Provides multi-factor ranking beyond simple vector similarity.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
from uuid import UUID

from ..schema import CivilizationNode, NodeType, NodeStatus

logger = logging.getLogger(__name__)


@dataclass
class RankingFactors:
    """Individual factors contributing to ranking score."""
    semantic_score: float = 0.0  # Vector similarity
    recency_score: float = 0.0  # How recently updated
    status_score: float = 0.0  # Node status (active > deprecated)
    type_match_score: float = 0.0  # How well type matches query intent
    popularity_score: float = 0.0  # Usage/reference frequency
    owner_score: float = 0.0  # Whether owner is relevant
    tag_match_score: float = 0.0  # Tag overlap with query


@dataclass
class RankingResult:
    """Result of ranking a single node."""
    node: CivilizationNode
    final_score: float
    factors: RankingFactors
    rank: int = 0
    explanation: str = ""


@dataclass
class RankingConfig:
    """Configuration for relevance ranking."""
    # Factor weights (should sum to ~1.0)
    semantic_weight: float = 0.4
    recency_weight: float = 0.15
    status_weight: float = 0.1
    type_match_weight: float = 0.15
    popularity_weight: float = 0.1
    tag_match_weight: float = 0.1

    # Recency decay
    recency_half_life_days: int = 30

    # Type preferences for different query intents
    type_preferences: dict[str, list[NodeType]] = field(default_factory=dict)


class RelevanceRanker:
    """
    Multi-factor relevance ranker for civilization nodes.
    Combines semantic similarity with recency, status, and other factors.
    """

    DEFAULT_TYPE_PREFERENCES = {
        "how to": [NodeType.SOP, NodeType.CHECKLIST, NodeType.RUNBOOK],
        "procedure": [NodeType.SOP, NodeType.CHECKLIST],
        "incident": [NodeType.RUNBOOK, NodeType.SOP],
        "who is": [NodeType.PERSON, NodeType.ROLE, NodeType.JOB_DESCRIPTION],
        "role": [NodeType.ROLE, NodeType.JOB_DESCRIPTION],
        "policy": [NodeType.POLICY],
        "workflow": [NodeType.WORKFLOW, NodeType.SOP],
        "client": [NodeType.CLIENT_PROFILE],
        "organization": [NodeType.ORG_CHART, NodeType.ROLE],
    }

    STATUS_SCORES = {
        NodeStatus.ACTIVE: 1.0,
        NodeStatus.UNDER_REVIEW: 0.8,
        NodeStatus.DRAFT: 0.6,
        NodeStatus.DEPRECATED: 0.3,
        NodeStatus.ARCHIVED: 0.1,
    }

    def __init__(self, config: RankingConfig | None = None):
        self.config = config or RankingConfig()
        self.config.type_preferences = {
            **self.DEFAULT_TYPE_PREFERENCES,
            **self.config.type_preferences,
        }
        self._popularity_cache: dict[UUID, float] = {}

    def rank(
        self,
        nodes: list[tuple[CivilizationNode, float]],  # (node, semantic_score)
        query: str,
        query_tags: list[str] | None = None,
        preferred_types: list[NodeType] | None = None,
    ) -> list[RankingResult]:
        """
        Rank nodes by multi-factor relevance.

        Args:
            nodes: List of (node, semantic_score) tuples
            query: The original query text
            query_tags: Optional tags to match against
            preferred_types: Optional type preferences

        Returns:
            Sorted list of RankingResults (highest first)
        """
        results = []

        # Detect query intent
        detected_types = preferred_types or self._detect_type_intent(query)

        for node, semantic_score in nodes:
            factors = self._compute_factors(
                node=node,
                semantic_score=semantic_score,
                query=query,
                query_tags=query_tags or [],
                preferred_types=detected_types,
            )

            final_score = self._compute_final_score(factors)

            results.append(RankingResult(
                node=node,
                final_score=final_score,
                factors=factors,
                explanation=self._generate_explanation(factors),
            ))

        # Sort by final score
        results.sort(key=lambda r: r.final_score, reverse=True)

        # Assign ranks
        for i, result in enumerate(results):
            result.rank = i + 1

        return results

    def _compute_factors(
        self,
        node: CivilizationNode,
        semantic_score: float,
        query: str,
        query_tags: list[str],
        preferred_types: list[NodeType],
    ) -> RankingFactors:
        """Compute all ranking factors for a node."""
        return RankingFactors(
            semantic_score=semantic_score,
            recency_score=self._compute_recency_score(node),
            status_score=self._compute_status_score(node),
            type_match_score=self._compute_type_match_score(node, preferred_types),
            popularity_score=self._compute_popularity_score(node),
            tag_match_score=self._compute_tag_match_score(node, query_tags),
        )

    def _compute_final_score(self, factors: RankingFactors) -> float:
        """Compute weighted final score from factors."""
        cfg = self.config
        return (
            factors.semantic_score * cfg.semantic_weight +
            factors.recency_score * cfg.recency_weight +
            factors.status_score * cfg.status_weight +
            factors.type_match_score * cfg.type_match_weight +
            factors.popularity_score * cfg.popularity_weight +
            factors.tag_match_score * cfg.tag_match_weight
        )

    def _compute_recency_score(self, node: CivilizationNode) -> float:
        """Compute recency score with exponential decay."""
        import math
        days_old = (datetime.utcnow() - node.updated_at).days
        half_life = self.config.recency_half_life_days
        return math.exp(-0.693 * days_old / half_life)

    def _compute_status_score(self, node: CivilizationNode) -> float:
        """Get score based on node status."""
        return self.STATUS_SCORES.get(node.status, 0.5)

    def _compute_type_match_score(
        self,
        node: CivilizationNode,
        preferred_types: list[NodeType],
    ) -> float:
        """Score based on how well type matches preferences."""
        if not preferred_types:
            return 0.5  # Neutral

        if node.node_type in preferred_types:
            # Higher score for earlier in preference list
            index = preferred_types.index(node.node_type)
            return 1.0 - (index * 0.2)  # 1.0, 0.8, 0.6, ...
        return 0.3

    def _compute_popularity_score(self, node: CivilizationNode) -> float:
        """Get popularity score (cached or computed)."""
        if node.id in self._popularity_cache:
            return self._popularity_cache[node.id]
        # Default to neutral - could be enhanced with usage tracking
        return 0.5

    def _compute_tag_match_score(
        self,
        node: CivilizationNode,
        query_tags: list[str],
    ) -> float:
        """Score based on tag overlap."""
        if not query_tags or not node.tags:
            return 0.5  # Neutral

        node_tags_lower = set(t.lower() for t in node.tags)
        query_tags_lower = set(t.lower() for t in query_tags)

        overlap = len(node_tags_lower & query_tags_lower)
        max_possible = min(len(node_tags_lower), len(query_tags_lower))

        if max_possible == 0:
            return 0.5
        return overlap / max_possible

    def _detect_type_intent(self, query: str) -> list[NodeType]:
        """Detect preferred node types from query text."""
        query_lower = query.lower()

        for pattern, types in self.config.type_preferences.items():
            if pattern in query_lower:
                return types

        return []  # No specific preference

    def _generate_explanation(self, factors: RankingFactors) -> str:
        """Generate human-readable explanation of ranking."""
        parts = []

        if factors.semantic_score > 0.8:
            parts.append("high semantic match")
        elif factors.semantic_score > 0.6:
            parts.append("moderate semantic match")

        if factors.recency_score > 0.8:
            parts.append("recently updated")
        elif factors.recency_score < 0.3:
            parts.append("may be outdated")

        if factors.type_match_score > 0.8:
            parts.append("matches expected type")

        if factors.status_score < 0.5:
            parts.append("non-active status")

        if factors.tag_match_score > 0.7:
            parts.append("matching tags")

        return ", ".join(parts) if parts else "standard relevance"

    def update_popularity(self, node_id: UUID, score: float) -> None:
        """Update popularity score for a node."""
        self._popularity_cache[node_id] = min(1.0, max(0.0, score))

    def boost_node(
        self,
        results: list[RankingResult],
        node_id: UUID,
        boost_factor: float = 1.5,
    ) -> list[RankingResult]:
        """Boost a specific node's ranking."""
        for result in results:
            if result.node.id == node_id:
                result.final_score *= boost_factor

        # Re-sort and re-rank
        results.sort(key=lambda r: r.final_score, reverse=True)
        for i, result in enumerate(results):
            result.rank = i + 1

        return results

    def filter_by_threshold(
        self,
        results: list[RankingResult],
        min_score: float = 0.3,
    ) -> list[RankingResult]:
        """Filter results below a score threshold."""
        return [r for r in results if r.final_score >= min_score]
