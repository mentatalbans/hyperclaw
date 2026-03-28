"""
Unified API for the Civilization Knowledge Layer.
Provides high-level operations for managing organizational knowledge.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
from uuid import UUID

from ..schema import (
    CivilizationNode, NodeType, NodeStatus,
    SOP, Role, Person, Checklist, Runbook, Workflow, OrgChart,
    JobDescription, ClientProfile, Policy, KnowledgeArticle,
)
from ..store import CivilizationStore
from ..ingestion.document_ingestor import DocumentIngestor
from ..ingestion.embedder import CivilizationEmbedder
from ..interview.interviewer import Interviewer, InterviewSession
from ..interview.gap_detector import GapDetector, GapReport
from ..graph.org_graph import OrgGraph
from ..graph.process_graph import ProcessGraph
from ..graph.knowledge_linker import KnowledgeLinker, KnowledgeLink
from ..versioning.version_manager import VersionManager, NodeVersion
from ..versioning.staleness_detector import StalenessDetector, StalenessReport
from ..retrieval.civilization_rag import CivilizationRAG, RAGResult, RAGConfig
from ..retrieval.context_injector import ContextInjector, InjectionConfig

logger = logging.getLogger(__name__)


@dataclass
class CivilizationStats:
    """Statistics about the civilization knowledge base."""
    total_nodes: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    by_status: dict[str, int] = field(default_factory=dict)
    stale_count: int = 0
    unowned_count: int = 0
    freshness_score: float = 0.0


class CivilizationAPI:
    """
    High-level API for the Civilization Knowledge Layer.
    Orchestrates all civilization components for a unified interface.
    """

    def __init__(
        self,
        org_id: str,
        store: CivilizationStore | None = None,
        embedder: CivilizationEmbedder | None = None,
    ):
        self.org_id = org_id
        self.store = store or CivilizationStore()
        self.embedder = embedder or CivilizationEmbedder()

        # Initialize components
        self.ingestor = DocumentIngestor(org_id, self.embedder, store=self.store)
        self.rag = CivilizationRAG(org_id, self.store, self.embedder)
        self.context_injector = ContextInjector()
        self.interviewer = Interviewer(org_id, self.store)
        self.gap_detector = GapDetector(org_id, self.store)
        self.version_manager = VersionManager(org_id, self.store)
        self.staleness_detector = StalenessDetector(org_id)
        self.knowledge_linker = KnowledgeLinker(org_id, self.store)

        self._nodes_cache: dict[UUID, CivilizationNode] = {}

    # ─── Node CRUD Operations ────────────────────────────────────────────────

    async def create_node(
        self,
        node: CivilizationNode,
        create_version: bool = True,
    ) -> CivilizationNode:
        """Create a new civilization node."""
        # Generate embedding if not present
        if not node.embedding:
            text = self._get_node_text(node)
            node.embedding = await self.embedder.embed(text)

        # Save to store
        saved = await self.store.save(node)

        # Create initial version
        if create_version:
            self.version_manager.create_version(
                saved,
                change_summary="Initial creation",
            )

        self._nodes_cache[saved.id] = saved
        return saved

    async def get_node(self, node_id: UUID) -> CivilizationNode | None:
        """Get a node by ID."""
        if node_id in self._nodes_cache:
            return self._nodes_cache[node_id]

        node = await self.store.get(node_id, self.org_id)
        if node:
            self._nodes_cache[node_id] = node
        return node

    async def update_node(
        self,
        node: CivilizationNode,
        change_summary: str | None = None,
        bump_type: str = "patch",
    ) -> CivilizationNode:
        """Update an existing node."""
        node.updated_at = datetime.utcnow()

        # Regenerate embedding
        text = self._get_node_text(node)
        node.embedding = await self.embedder.embed(text)

        # Create new version if changed
        if self.version_manager.has_changes(node):
            self.version_manager.create_version(
                node,
                bump_type=bump_type,
                change_summary=change_summary,
            )
            node.version = self.version_manager.get_current_version(node.id)

        # Save
        saved = await self.store.save(node)
        self._nodes_cache[saved.id] = saved
        return saved

    async def delete_node(self, node_id: UUID) -> bool:
        """Delete a node."""
        success = await self.store.delete(node_id, self.org_id)
        if success and node_id in self._nodes_cache:
            del self._nodes_cache[node_id]
        return success

    async def list_nodes(
        self,
        node_type: NodeType | None = None,
        limit: int = 100,
    ) -> list[CivilizationNode]:
        """List nodes, optionally filtered by type."""
        if node_type:
            return await self.store.list_by_type(self.org_id, node_type, limit)
        return await self.store.list_all(self.org_id, limit)

    # ─── Search & Retrieval ──────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        top_k: int = 5,
        node_types: list[NodeType] | None = None,
    ) -> RAGResult:
        """Search for relevant nodes."""
        config = RAGConfig(
            top_k=top_k,
            node_type_filters=node_types,
        )
        return await self.rag.query(query, config)

    async def get_context_for_task(
        self,
        task_description: str,
        max_tokens: int = 2000,
    ) -> str:
        """Get formatted context for an agent task."""
        result = await self.search(task_description, top_k=5)
        nodes = [rn.node for rn in result.retrieved_nodes]

        config = InjectionConfig(max_tokens=max_tokens)
        injection = self.context_injector.inject(nodes, config)
        return injection.context

    async def find_sop(self, task: str) -> list[SOP]:
        """Find SOPs relevant to a task."""
        result = await self.rag.get_sop_for_task(task)
        return [rn.node for rn in result.retrieved_nodes if isinstance(rn.node, SOP)]

    async def find_runbook(self, incident: str) -> list[Runbook]:
        """Find runbooks for an incident."""
        result = await self.rag.get_runbook_for_incident(incident)
        return [rn.node for rn in result.retrieved_nodes if isinstance(rn.node, Runbook)]

    # ─── Ingestion ───────────────────────────────────────────────────────────

    async def ingest_document(
        self,
        content: str | bytes,
        filename: str | None = None,
        source: str | None = None,
        source_url: str | None = None,
        node_type: NodeType | None = None,
    ) -> CivilizationNode | None:
        """Ingest a document into the knowledge base."""
        result = await self.ingestor.ingest(
            content=content,
            filename=filename,
            source=source,
            source_url=source_url,
            node_type_hint=node_type,
        )
        if result.success and result.node_id:
            return await self.get_node(result.node_id)
        return None

    # ─── Interviews ──────────────────────────────────────────────────────────

    def start_interview(
        self,
        node_type: NodeType,
        subject: str,
    ) -> InterviewSession:
        """Start a knowledge elicitation interview."""
        return self.interviewer.start_session(node_type, subject)

    def submit_interview_response(
        self,
        session_id: UUID,
        response: str,
    ) -> dict:
        """Submit a response to an interview question."""
        return self.interviewer.submit_response(session_id, response)

    async def complete_interview(
        self,
        session_id: UUID,
    ) -> CivilizationNode | None:
        """Complete an interview and create the node."""
        node = self.interviewer.compile_to_node(session_id)
        if node:
            return await self.create_node(node)
        return None

    # ─── Analysis & Health ───────────────────────────────────────────────────

    async def analyze_gaps(self) -> GapReport:
        """Analyze knowledge base for gaps."""
        nodes = await self.list_nodes(limit=1000)
        return await self.gap_detector.analyze(nodes)

    async def check_staleness(self) -> StalenessReport:
        """Check for stale content."""
        nodes = await self.list_nodes(limit=1000)
        return self.staleness_detector.check_nodes(nodes)

    async def get_stats(self) -> CivilizationStats:
        """Get knowledge base statistics."""
        stats = CivilizationStats()

        # Count by type
        stats.by_type = await self.store.count_by_type(self.org_id)
        stats.total_nodes = sum(stats.by_type.values())

        # Check staleness
        nodes = await self.list_nodes(limit=1000)
        staleness = self.staleness_detector.check_nodes(nodes)
        stats.stale_count = staleness.stale_count
        stats.freshness_score = staleness.freshness_score

        # Count unowned
        stats.unowned_count = sum(1 for n in nodes if not n.owner_id)

        # Count by status
        for node in nodes:
            status = node.status.value
            stats.by_status[status] = stats.by_status.get(status, 0) + 1

        return stats

    # ─── Graphs ──────────────────────────────────────────────────────────────

    async def build_org_graph(self) -> OrgGraph:
        """Build organizational graph from person nodes."""
        people = await self.list_nodes(NodeType.PERSON, limit=500)
        return OrgGraph.from_people(self.org_id, [p for p in people if isinstance(p, Person)])

    async def build_process_graph(self, sop_id: UUID) -> ProcessGraph | None:
        """Build process graph from an SOP."""
        node = await self.get_node(sop_id)
        if isinstance(node, SOP):
            return ProcessGraph.from_sop(node)
        return None

    async def detect_links(self) -> list[KnowledgeLink]:
        """Auto-detect links between nodes."""
        nodes = await self.list_nodes(limit=1000)
        links = self.knowledge_linker.detect_links(nodes)
        self.knowledge_linker.bulk_add_links(links)
        return links

    # ─── Versioning ──────────────────────────────────────────────────────────

    def get_node_history(self, node_id: UUID) -> list[dict]:
        """Get version history for a node."""
        return self.version_manager.get_version_timeline(node_id)

    async def restore_version(
        self,
        node_id: UUID,
        version: str,
    ) -> CivilizationNode | None:
        """Restore a node to a previous version."""
        node = self.version_manager.restore_version(node_id, version)
        if node:
            return await self.update_node(node, f"Restored to version {version}")
        return None

    # ─── Helpers ─────────────────────────────────────────────────────────────

    def _get_node_text(self, node: CivilizationNode) -> str:
        """Extract text for embedding from a node."""
        parts = [node.title]

        node_dict = node.model_dump()

        # Add type-specific content
        if node.node_type == NodeType.SOP:
            parts.append(node_dict.get("purpose", ""))
            parts.append(node_dict.get("scope", ""))
            for step in node_dict.get("steps", []):
                parts.append(step.get("title", ""))
                parts.append(step.get("description", ""))

        elif node.node_type == NodeType.ROLE:
            parts.extend(node_dict.get("responsibilities", []))
            parts.extend(node_dict.get("accountabilities", []))

        elif node.node_type == NodeType.JOB_DESCRIPTION:
            parts.append(node_dict.get("summary", ""))
            parts.extend(node_dict.get("responsibilities", []))

        elif node.node_type == NodeType.CHECKLIST:
            parts.append(node_dict.get("purpose", ""))
            for item in node_dict.get("items", []):
                parts.append(item.get("description", ""))

        elif node.node_type == NodeType.RUNBOOK:
            parts.append(node_dict.get("scenario", ""))
            for step in node_dict.get("steps", []):
                parts.append(step.get("action", ""))

        elif node.node_type == NodeType.POLICY:
            parts.append(node_dict.get("summary", ""))
            parts.append(node_dict.get("full_text", "")[:1000])

        elif node.node_type == NodeType.KNOWLEDGE_ARTICLE:
            parts.append(node_dict.get("summary", ""))
            parts.append(node_dict.get("content", "")[:1500])

        return " ".join(filter(None, parts))[:3000]

    def clear_cache(self) -> None:
        """Clear internal caches."""
        self._nodes_cache.clear()
        self.embedder.clear_cache()
