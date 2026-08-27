"""
Document ingestion pipeline for Civilization Knowledge Layer.
Orchestrates parsing, extraction, chunking, and embedding.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO
from uuid import UUID, uuid4

from ..schema import (
    CivilizationNode, NodeType, NodeStatus,
    SOP, SOPStep, Checklist, ChecklistItem, Runbook, RunbookStep,
    JobDescription, Role, Workflow, WorkflowNode, WorkflowEdge,
    KnowledgeArticle, Policy,
)
from .chunker import ProceduralChunker, SOPChunk, ChecklistChunk, RunbookChunk, GenericChunk
from .extractor import MetadataExtractor, EntityExtractor, ExtractedMetadata
from .embedder import CivilizationEmbedder

logger = logging.getLogger(__name__)


@dataclass
class IngestionResult:
    """Result of document ingestion."""
    success: bool
    node_id: UUID | None = None
    node_type: NodeType | None = None
    chunks_created: int = 0
    entities_extracted: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class DocumentParser:
    """Parse various document formats into plain text."""

    SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown", ".rst", ".json", ".yaml", ".yml"}

    def parse(self, content: str | bytes, filename: str | None = None) -> str:
        """Parse document content to plain text."""
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")

        if filename:
            ext = Path(filename).suffix.lower()
            if ext in {".json"}:
                return self._parse_json(content)
            if ext in {".yaml", ".yml"}:
                return self._parse_yaml(content)

        return content

    def _parse_json(self, content: str) -> str:
        """Parse JSON to readable text."""
        import json
        try:
            data = json.loads(content)
            return self._dict_to_text(data)
        except json.JSONDecodeError:
            return content

    def _parse_yaml(self, content: str) -> str:
        """Parse YAML to readable text."""
        try:
            import yaml
            data = yaml.safe_load(content)
            return self._dict_to_text(data)
        except Exception:
            return content

    def _dict_to_text(self, data: Any, indent: int = 0) -> str:
        """Convert dict/list to readable text format."""
        lines = []
        prefix = "  " * indent

        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, (dict, list)):
                    lines.append(f"{prefix}{key}:")
                    lines.append(self._dict_to_text(value, indent + 1))
                else:
                    lines.append(f"{prefix}{key}: {value}")
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, (dict, list)):
                    lines.append(f"{prefix}-")
                    lines.append(self._dict_to_text(item, indent + 1))
                else:
                    lines.append(f"{prefix}- {item}")
        else:
            lines.append(f"{prefix}{data}")

        return "\n".join(lines)


class DocumentIngestor:
    """
    Main ingestion pipeline for Civilization Knowledge Layer.
    Coordinates parsing, extraction, chunking, and embedding.
    """

    def __init__(
        self,
        org_id: str,
        embedder: CivilizationEmbedder | None = None,
        chunker: ProceduralChunker | None = None,
        store=None,  # CivilizationStore
    ):
        self.org_id = org_id
        self.embedder = embedder or CivilizationEmbedder()
        self.chunker = chunker or ProceduralChunker()
        self.store = store
        self.parser = DocumentParser()
        self.metadata_extractor = MetadataExtractor()
        self.entity_extractor = EntityExtractor()

    async def ingest(
        self,
        content: str | bytes,
        filename: str | None = None,
        source: str | None = None,
        source_url: str | None = None,
        node_type_hint: NodeType | None = None,
        owner_id: str | None = None,
        tags: list[str] | None = None,
    ) -> IngestionResult:
        """
        Ingest a document into the Civilization Knowledge Layer.

        Args:
            content: Raw document content (string or bytes)
            filename: Original filename (helps with parsing)
            source: Source system (e.g., "notion", "confluence", "upload")
            source_url: URL of the source document
            node_type_hint: Override auto-detection of node type
            owner_id: ID of the document owner
            tags: Tags to apply to the node

        Returns:
            IngestionResult with details of ingestion
        """
        result = IngestionResult(success=False)

        try:
            # Parse document
            text = self.parser.parse(content, filename)
            if not text.strip():
                result.errors.append("Document is empty after parsing")
                return result

            # Extract metadata
            metadata = self.metadata_extractor.extract(text)
            result.metadata = {
                "title": metadata.title,
                "purpose": metadata.purpose,
                "detected_type": metadata.document_type,
                "complexity": metadata.estimated_complexity,
            }

            # Determine node type
            node_type = node_type_hint
            if not node_type:
                node_type = self._detect_node_type(metadata)
            result.node_type = node_type

            # Extract entities
            entities = self.entity_extractor.extract(text)
            result.entities_extracted = len(entities)

            # Create node based on type
            node = await self._create_node(
                text=text,
                metadata=metadata,
                node_type=node_type,
                source=source,
                source_url=source_url,
                owner_id=owner_id,
                tags=tags or [],
            )
            result.node_id = node.id

            # Generate chunks and embeddings
            chunks = await self._chunk_and_embed(node, text)
            result.chunks_created = len(chunks)

            # Save to store if available
            if self.store:
                await self.store.save(node)

            result.success = True

        except Exception as e:
            logger.exception("Ingestion failed")
            result.errors.append(str(e))

        return result

    def _detect_node_type(self, metadata: ExtractedMetadata) -> NodeType:
        """Detect node type from extracted metadata."""
        type_map = {
            "sop": NodeType.SOP,
            "checklist": NodeType.CHECKLIST,
            "runbook": NodeType.RUNBOOK,
            "job_description": NodeType.JOB_DESCRIPTION,
            "org_chart": NodeType.ORG_CHART,
            "workflow": NodeType.WORKFLOW,
            "policy": NodeType.POLICY,
            "knowledge_article": NodeType.KNOWLEDGE_ARTICLE,
        }
        return type_map.get(metadata.document_type or "", NodeType.KNOWLEDGE_ARTICLE)

    async def _create_node(
        self,
        text: str,
        metadata: ExtractedMetadata,
        node_type: NodeType,
        source: str | None,
        source_url: str | None,
        owner_id: str | None,
        tags: list[str],
    ) -> CivilizationNode:
        """Create the appropriate node type."""
        base_kwargs = {
            "org_id": self.org_id,
            "title": metadata.title or "Untitled Document",
            "source": source,
            "source_url": source_url,
            "owner_id": owner_id,
            "tags": tags + metadata.tags,
            "status": NodeStatus.DRAFT,
        }

        # Generate embedding for the full document (or summary)
        summary_text = text[:2000] if len(text) > 2000 else text
        embedding = await self.embedder.embed(summary_text)
        base_kwargs["embedding"] = embedding

        if node_type == NodeType.SOP:
            return SOP(
                **base_kwargs,
                purpose=metadata.purpose or "",
                scope="",
                steps=[SOPStep(step_number=1, title="Step 1", description=text[:500])],
                roles_involved=metadata.roles_mentioned,
                tools_required=metadata.tools_mentioned,
            )
        elif node_type == NodeType.CHECKLIST:
            return Checklist(
                **base_kwargs,
                purpose=metadata.purpose or "",
                items=[ChecklistItem(item_number=1, description=text[:500])],
                roles_involved=metadata.roles_mentioned,
            )
        elif node_type == NodeType.RUNBOOK:
            return Runbook(
                **base_kwargs,
                system=metadata.systems_mentioned[0] if metadata.systems_mentioned else "Unknown",
                scenario="",
                steps=[RunbookStep(step_number=1, action=text[:500])],
            )
        elif node_type == NodeType.JOB_DESCRIPTION:
            return JobDescription(
                **base_kwargs,
                role_title=metadata.title or "Unknown Role",
                department=metadata.department or "",
                summary=metadata.purpose or "",
                responsibilities=[],
                required_skills=[],
                tools_used=metadata.tools_mentioned,
            )
        elif node_type == NodeType.WORKFLOW:
            return Workflow(
                **base_kwargs,
                description=metadata.purpose or "",
                nodes=[WorkflowNode(node_id="start", label="Start", node_type="start")],
                edges=[],
            )
        elif node_type == NodeType.POLICY:
            return Policy(
                **base_kwargs,
                policy_name=metadata.title or "Untitled Policy",
                category="general",
                summary=metadata.purpose or "",
                full_text=text,
            )
        else:
            return KnowledgeArticle(
                **base_kwargs,
                topic=metadata.title or "Unknown Topic",
                category="general",
                summary=metadata.purpose or text[:300],
                content=text,
            )

    async def _chunk_and_embed(
        self,
        node: CivilizationNode,
        text: str,
    ) -> list[Any]:
        """Create chunks and generate embeddings for each."""
        chunks: list[Any] = []

        if isinstance(node, SOP):
            chunks = self.chunker.chunk_sop(node)
        elif isinstance(node, Checklist):
            chunks = self.chunker.chunk_checklist(node)
        elif isinstance(node, Runbook):
            chunks = self.chunker.chunk_runbook(node)
        else:
            chunks = self.chunker.chunk_generic(node, text)

        # Generate embeddings for all chunks
        if chunks:
            contents = [c.content for c in chunks]
            embeddings = await self.embedder.embed_batch(contents)
            for chunk, embedding in zip(chunks, embeddings):
                chunk.embedding = embedding

        return chunks

    async def ingest_batch(
        self,
        documents: list[dict],
    ) -> list[IngestionResult]:
        """
        Ingest multiple documents.

        Args:
            documents: List of dicts with keys: content, filename, source, source_url, etc.

        Returns:
            List of IngestionResult for each document
        """
        results = []
        for doc in documents:
            result = await self.ingest(
                content=doc.get("content", ""),
                filename=doc.get("filename"),
                source=doc.get("source"),
                source_url=doc.get("source_url"),
                node_type_hint=doc.get("node_type_hint"),
                owner_id=doc.get("owner_id"),
                tags=doc.get("tags"),
            )
            results.append(result)
        return results
