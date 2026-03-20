"""
Notion integration for syncing organizational knowledge.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from ..schema import CivilizationNode, NodeType, NodeStatus
from ..ingestion.document_ingestor import DocumentIngestor

logger = logging.getLogger(__name__)


@dataclass
class NotionConfig:
    """Configuration for Notion sync."""
    api_key: str = ""
    workspace_id: str | None = None
    database_ids: dict[NodeType, str] = field(default_factory=dict)
    sync_interval_minutes: int = 60
    auto_create_nodes: bool = True
    bidirectional: bool = False  # If True, also push changes back to Notion


@dataclass
class NotionPage:
    """Represents a Notion page."""
    page_id: str
    title: str
    url: str
    content: str
    properties: dict = field(default_factory=dict)
    last_edited: datetime | None = None
    parent_id: str | None = None


@dataclass
class SyncResult:
    """Result of a sync operation."""
    success: bool
    pages_synced: int = 0
    nodes_created: int = 0
    nodes_updated: int = 0
    errors: list[str] = field(default_factory=list)
    sync_time: datetime = field(default_factory=datetime.utcnow)


class NotionSync:
    """
    Synchronizes Notion workspace content with Civilization Knowledge Layer.
    """

    def __init__(
        self,
        org_id: str,
        config: NotionConfig,
        ingestor: DocumentIngestor | None = None,
    ):
        self.org_id = org_id
        self.config = config
        self.ingestor = ingestor
        self._last_sync: datetime | None = None
        self._sync_cursor: dict[str, str] = {}

    async def sync_database(
        self,
        database_id: str,
        node_type: NodeType | None = None,
    ) -> SyncResult:
        """
        Sync a Notion database to civilization nodes.

        Args:
            database_id: Notion database ID
            node_type: Optional node type to assign

        Returns:
            SyncResult with sync statistics
        """
        result = SyncResult(success=False)

        try:
            # Fetch pages from Notion
            pages = await self._fetch_database_pages(database_id)
            result.pages_synced = len(pages)

            # Process each page
            for page in pages:
                try:
                    await self._process_page(page, node_type, result)
                except Exception as e:
                    result.errors.append(f"Error processing page {page.page_id}: {e}")

            result.success = True
            self._last_sync = datetime.utcnow()

        except Exception as e:
            logger.exception("Notion sync failed")
            result.errors.append(str(e))

        return result

    async def sync_all_databases(self) -> SyncResult:
        """Sync all configured databases."""
        total_result = SyncResult(success=True)

        for node_type, database_id in self.config.database_ids.items():
            result = await self.sync_database(database_id, node_type)
            total_result.pages_synced += result.pages_synced
            total_result.nodes_created += result.nodes_created
            total_result.nodes_updated += result.nodes_updated
            total_result.errors.extend(result.errors)
            if not result.success:
                total_result.success = False

        return total_result

    async def sync_page(self, page_id: str) -> CivilizationNode | None:
        """Sync a single Notion page."""
        page = await self._fetch_page(page_id)
        if not page:
            return None

        result = SyncResult(success=False)
        await self._process_page(page, None, result)

        return None  # Would return the created/updated node

    async def _fetch_database_pages(self, database_id: str) -> list[NotionPage]:
        """
        Fetch all pages from a Notion database.
        This is a stub - actual implementation would use Notion API.
        """
        # In real implementation:
        # async with httpx.AsyncClient() as client:
        #     response = await client.post(
        #         f"https://api.notion.com/v1/databases/{database_id}/query",
        #         headers={
        #             "Authorization": f"Bearer {self.config.api_key}",
        #             "Notion-Version": "2022-06-28",
        #         },
        #         json={"page_size": 100},
        #     )
        #     ...

        logger.info(f"Would fetch pages from Notion database: {database_id}")
        return []

    async def _fetch_page(self, page_id: str) -> NotionPage | None:
        """
        Fetch a single Notion page with content.
        This is a stub - actual implementation would use Notion API.
        """
        logger.info(f"Would fetch Notion page: {page_id}")
        return None

    async def _process_page(
        self,
        page: NotionPage,
        node_type: NodeType | None,
        result: SyncResult,
    ) -> None:
        """Process a Notion page and create/update civilization node."""
        if not self.ingestor:
            logger.warning("No ingestor configured, skipping page processing")
            return

        # Determine node type from page properties or use provided type
        detected_type = node_type or self._detect_node_type(page)

        # Ingest the page content
        ingest_result = await self.ingestor.ingest(
            content=page.content,
            filename=f"{page.title}.md",
            source="notion",
            source_url=page.url,
            node_type_hint=detected_type,
        )

        if ingest_result.success:
            result.nodes_created += 1
        else:
            result.errors.extend(ingest_result.errors)

    def _detect_node_type(self, page: NotionPage) -> NodeType:
        """Detect node type from page properties."""
        # Check for type property
        type_prop = page.properties.get("Type", {}).get("select", {}).get("name", "")
        type_map = {
            "SOP": NodeType.SOP,
            "Procedure": NodeType.SOP,
            "Checklist": NodeType.CHECKLIST,
            "Runbook": NodeType.RUNBOOK,
            "Role": NodeType.ROLE,
            "Job Description": NodeType.JOB_DESCRIPTION,
            "Policy": NodeType.POLICY,
        }
        return type_map.get(type_prop, NodeType.KNOWLEDGE_ARTICLE)

    async def push_to_notion(
        self,
        node: CivilizationNode,
        database_id: str | None = None,
    ) -> str | None:
        """
        Push a civilization node back to Notion.
        Returns the Notion page ID if successful.
        """
        if not self.config.bidirectional:
            logger.warning("Bidirectional sync not enabled")
            return None

        db_id = database_id or self.config.database_ids.get(node.node_type)
        if not db_id:
            logger.warning(f"No database configured for node type: {node.node_type}")
            return None

        # In real implementation, would create/update Notion page
        logger.info(f"Would push node {node.id} to Notion database {db_id}")
        return None

    def get_sync_status(self) -> dict:
        """Get current sync status."""
        return {
            "last_sync": self._last_sync.isoformat() if self._last_sync else None,
            "configured_databases": list(self.config.database_ids.keys()),
            "sync_interval_minutes": self.config.sync_interval_minutes,
            "bidirectional": self.config.bidirectional,
        }
