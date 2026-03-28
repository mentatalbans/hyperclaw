"""
RAG (Retrieval Augmented Generation) for Civilization Knowledge Layer.
Retrieves and formats organizational knowledge for agent consumption.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import UUID

from ..schema import CivilizationNode, NodeType
from ..store import CivilizationStore
from ..ingestion.embedder import CivilizationEmbedder

logger = logging.getLogger(__name__)


@dataclass
class RAGConfig:
    """Configuration for RAG retrieval."""
    top_k: int = 5
    min_relevance_score: float = 0.5
    include_metadata: bool = True
    max_context_tokens: int = 4000
    node_type_filters: list[NodeType] | None = None
    tag_filters: list[str] | None = None
    rerank: bool = True
    expand_related: bool = True


@dataclass
class RetrievedNode:
    """A node retrieved by RAG with relevance metadata."""
    node: CivilizationNode
    score: float
    source: str = "vector"  # "vector", "keyword", "related"
    highlights: list[str] = field(default_factory=list)


@dataclass
class RAGResult:
    """Result of a RAG query."""
    query: str
    retrieved_nodes: list[RetrievedNode] = field(default_factory=list)
    context: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def node_count(self) -> int:
        return len(self.retrieved_nodes)

    @property
    def has_results(self) -> bool:
        return len(self.retrieved_nodes) > 0

    @property
    def top_node(self) -> CivilizationNode | None:
        if self.retrieved_nodes:
            return self.retrieved_nodes[0].node
        return None


class CivilizationRAG:
    """
    RAG system for retrieving and formatting organizational knowledge.
    Supports vector search, keyword fallback, and context expansion.
    """

    def __init__(
        self,
        org_id: str,
        store: CivilizationStore | None = None,
        embedder: CivilizationEmbedder | None = None,
        config: RAGConfig | None = None,
    ):
        self.org_id = org_id
        self.store = store
        self.embedder = embedder or CivilizationEmbedder()
        self.config = config or RAGConfig()
        self._cache: dict[str, list[RetrievedNode]] = {}

    async def query(
        self,
        query: str,
        config_override: RAGConfig | None = None,
    ) -> RAGResult:
        """
        Query the knowledge base and retrieve relevant nodes.

        Args:
            query: The search query
            config_override: Optional config to override defaults

        Returns:
            RAGResult with retrieved nodes and formatted context
        """
        config = config_override or self.config
        result = RAGResult(query=query)

        # Generate query embedding
        query_embedding = await self.embedder.embed(query)

        # Vector search
        if self.store:
            vector_results = await self.store.search_by_embedding(
                org_id=self.org_id,
                embedding=query_embedding,
                top_k=config.top_k * 2,  # Over-fetch for reranking
                node_types=config.node_type_filters,
            )

            for row in vector_results:
                score = row.get("score", 0.0)
                if score >= config.min_relevance_score:
                    node = self.store._row_to_node(row)
                    result.retrieved_nodes.append(RetrievedNode(
                        node=node,
                        score=score,
                        source="vector",
                    ))

        # Sort by score
        result.retrieved_nodes.sort(key=lambda x: x.score, reverse=True)

        # Truncate to top_k
        result.retrieved_nodes = result.retrieved_nodes[:config.top_k]

        # Expand with related nodes if enabled
        if config.expand_related and result.retrieved_nodes:
            await self._expand_related(result, config)

        # Generate context string
        result.context = self._format_context(result.retrieved_nodes, config)

        result.metadata = {
            "total_retrieved": len(result.retrieved_nodes),
            "config": {
                "top_k": config.top_k,
                "min_score": config.min_relevance_score,
            },
        }

        return result

    async def query_by_type(
        self,
        query: str,
        node_type: NodeType,
        top_k: int = 5,
    ) -> RAGResult:
        """Query for a specific node type."""
        config = RAGConfig(
            top_k=top_k,
            node_type_filters=[node_type],
        )
        return await self.query(query, config)

    async def get_sop_for_task(self, task_description: str) -> RAGResult:
        """Find relevant SOPs for a task."""
        return await self.query_by_type(
            query=f"SOP procedure for: {task_description}",
            node_type=NodeType.SOP,
            top_k=3,
        )

    async def get_role_info(self, role_query: str) -> RAGResult:
        """Find role definitions."""
        config = RAGConfig(
            top_k=3,
            node_type_filters=[NodeType.ROLE, NodeType.JOB_DESCRIPTION],
        )
        return await self.query(role_query, config)

    async def get_runbook_for_incident(self, incident_description: str) -> RAGResult:
        """Find relevant runbooks for an incident."""
        return await self.query_by_type(
            query=f"Runbook for incident: {incident_description}",
            node_type=NodeType.RUNBOOK,
            top_k=3,
        )

    async def _expand_related(self, result: RAGResult, config: RAGConfig) -> None:
        """Expand results with related nodes."""
        if not self.store:
            return

        # Get IDs of related nodes from top results
        related_ids: set[UUID] = set()
        for rn in result.retrieved_nodes[:3]:
            # Check for related_sops, related_roles, etc.
            node_dict = rn.node.model_dump()
            for key, value in node_dict.items():
                if key.startswith("related_") and isinstance(value, list):
                    for item in value:
                        if isinstance(item, (UUID, str)):
                            try:
                                related_ids.add(UUID(str(item)))
                            except ValueError:
                                pass

        # Fetch and add related nodes
        existing_ids = {rn.node.id for rn in result.retrieved_nodes}
        for rel_id in related_ids:
            if rel_id not in existing_ids and len(result.retrieved_nodes) < config.top_k + 2:
                related = await self.store.get(rel_id, self.org_id)
                if related:
                    result.retrieved_nodes.append(RetrievedNode(
                        node=related,
                        score=0.5,  # Default score for related
                        source="related",
                    ))

    def _format_context(
        self,
        nodes: list[RetrievedNode],
        config: RAGConfig,
    ) -> str:
        """Format retrieved nodes into context string for LLM."""
        if not nodes:
            return ""

        parts = []
        token_estimate = 0
        max_tokens = config.max_context_tokens

        for i, rn in enumerate(nodes, 1):
            node = rn.node
            section = self._format_node(node, i, config.include_metadata)

            # Rough token estimate (4 chars per token)
            section_tokens = len(section) // 4
            if token_estimate + section_tokens > max_tokens:
                break

            parts.append(section)
            token_estimate += section_tokens

        return "\n\n---\n\n".join(parts)

    def _format_node(
        self,
        node: CivilizationNode,
        index: int,
        include_metadata: bool,
    ) -> str:
        """Format a single node for context."""
        lines = [f"[{index}] {node.node_type.value.upper()}: {node.title}"]

        if include_metadata:
            if node.owner_id:
                lines.append(f"Owner: {node.owner_id}")
            if node.tags:
                lines.append(f"Tags: {', '.join(node.tags)}")

        # Type-specific formatting
        node_dict = node.model_dump()

        if node.node_type == NodeType.SOP:
            if "purpose" in node_dict:
                lines.append(f"Purpose: {node_dict['purpose']}")
            if "steps" in node_dict:
                lines.append("Steps:")
                for step in node_dict["steps"][:5]:
                    lines.append(f"  {step.get('step_number', '?')}. {step.get('title', 'Untitled')}")
                    if step.get("description"):
                        lines.append(f"     {step['description'][:150]}...")

        elif node.node_type == NodeType.ROLE:
            if "role_title" in node_dict:
                lines.append(f"Title: {node_dict['role_title']}")
            if "responsibilities" in node_dict:
                lines.append("Responsibilities:")
                for r in node_dict["responsibilities"][:5]:
                    lines.append(f"  - {r}")

        elif node.node_type == NodeType.RUNBOOK:
            if "scenario" in node_dict:
                lines.append(f"Scenario: {node_dict['scenario']}")
            if "steps" in node_dict:
                lines.append("Steps:")
                for step in node_dict["steps"][:5]:
                    lines.append(f"  {step.get('step_number', '?')}. {step.get('action', 'Action')}")

        elif node.node_type == NodeType.CHECKLIST:
            if "items" in node_dict:
                lines.append("Items:")
                for item in node_dict["items"][:8]:
                    lines.append(f"  [ ] {item.get('description', '')}")

        return "\n".join(lines)

    async def hybrid_search(
        self,
        query: str,
        keyword_weight: float = 0.3,
    ) -> RAGResult:
        """
        Perform hybrid search combining vector and keyword matching.
        """
        # Vector search
        vector_result = await self.query(query)

        # For keyword search, we'd need text search capability in the store
        # This is a placeholder that could be enhanced
        result = RAGResult(
            query=query,
            retrieved_nodes=vector_result.retrieved_nodes,
            metadata={"search_type": "hybrid"},
        )

        result.context = self._format_context(result.retrieved_nodes, self.config)
        return result

    def clear_cache(self) -> None:
        """Clear the query cache."""
        self._cache.clear()
