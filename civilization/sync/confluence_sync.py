"""
Confluence integration for syncing organizational knowledge.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from ..schema import CivilizationNode, NodeType
from ..ingestion.document_ingestor import DocumentIngestor

logger = logging.getLogger(__name__)


@dataclass
class ConfluenceConfig:
    """Configuration for Confluence sync."""
    base_url: str = ""  # e.g., "https://your-domain.atlassian.net/wiki"
    username: str = ""
    api_token: str = ""
    space_keys: list[str] = field(default_factory=list)
    space_type_mapping: dict[str, NodeType] = field(default_factory=dict)
    sync_interval_minutes: int = 120
    include_attachments: bool = False
    label_filters: list[str] = field(default_factory=list)


@dataclass
class ConfluencePage:
    """Represents a Confluence page."""
    page_id: str
    title: str
    space_key: str
    web_url: str
    body_html: str
    body_storage: str = ""  # Confluence storage format
    labels: list[str] = field(default_factory=list)
    version: int = 1
    last_modified: datetime | None = None
    parent_id: str | None = None
    ancestors: list[str] = field(default_factory=list)


@dataclass
class ConfluenceSyncResult:
    """Result of a Confluence sync operation."""
    success: bool
    pages_synced: int = 0
    pages_skipped: int = 0
    nodes_created: int = 0
    nodes_updated: int = 0
    errors: list[str] = field(default_factory=list)
    sync_time: datetime = field(default_factory=datetime.utcnow)


class ConfluenceSync:
    """
    Synchronizes Confluence spaces/pages with Civilization Knowledge Layer.
    """

    LABEL_TYPE_MAPPING = {
        "sop": NodeType.SOP,
        "procedure": NodeType.SOP,
        "checklist": NodeType.CHECKLIST,
        "runbook": NodeType.RUNBOOK,
        "playbook": NodeType.RUNBOOK,
        "role": NodeType.ROLE,
        "job-description": NodeType.JOB_DESCRIPTION,
        "policy": NodeType.POLICY,
        "workflow": NodeType.WORKFLOW,
    }

    def __init__(
        self,
        org_id: str,
        config: ConfluenceConfig,
        ingestor: DocumentIngestor | None = None,
    ):
        self.org_id = org_id
        self.config = config
        self.ingestor = ingestor
        self._last_sync: datetime | None = None
        self._page_cache: dict[str, int] = {}  # page_id -> version

    async def sync_space(
        self,
        space_key: str,
        node_type: NodeType | None = None,
    ) -> ConfluenceSyncResult:
        """
        Sync a Confluence space to civilization nodes.

        Args:
            space_key: Confluence space key
            node_type: Optional default node type

        Returns:
            ConfluenceSyncResult with sync statistics
        """
        result = ConfluenceSyncResult(success=False)

        try:
            # Fetch pages from space
            pages = await self._list_space_pages(space_key)

            for page in pages:
                try:
                    # Skip if not modified
                    if self._should_skip_page(page):
                        result.pages_skipped += 1
                        continue

                    # Apply label filters
                    if self.config.label_filters:
                        if not any(l in page.labels for l in self.config.label_filters):
                            result.pages_skipped += 1
                            continue

                    # Process page
                    await self._process_page(page, node_type, result)
                    result.pages_synced += 1

                except Exception as e:
                    result.errors.append(f"Error processing page {page.title}: {e}")

            result.success = True
            self._last_sync = datetime.utcnow()

        except Exception as e:
            logger.exception("Confluence sync failed")
            result.errors.append(str(e))

        return result

    async def sync_all_spaces(self) -> ConfluenceSyncResult:
        """Sync all configured spaces."""
        total_result = ConfluenceSyncResult(success=True)

        for space_key in self.config.space_keys:
            node_type = self.config.space_type_mapping.get(space_key)
            result = await self.sync_space(space_key, node_type)

            total_result.pages_synced += result.pages_synced
            total_result.pages_skipped += result.pages_skipped
            total_result.nodes_created += result.nodes_created
            total_result.nodes_updated += result.nodes_updated
            total_result.errors.extend(result.errors)

            if not result.success:
                total_result.success = False

        return total_result

    async def sync_page(self, page_id: str) -> CivilizationNode | None:
        """Sync a single page by ID."""
        page = await self._get_page(page_id)
        if not page:
            return None

        result = ConfluenceSyncResult(success=False)
        await self._process_page(page, None, result)

        return None

    async def sync_by_label(
        self,
        label: str,
        node_type: NodeType | None = None,
    ) -> ConfluenceSyncResult:
        """Sync all pages with a specific label."""
        result = ConfluenceSyncResult(success=False)

        try:
            pages = await self._search_by_label(label)

            for page in pages:
                try:
                    if self._should_skip_page(page):
                        result.pages_skipped += 1
                        continue

                    await self._process_page(page, node_type, result)
                    result.pages_synced += 1

                except Exception as e:
                    result.errors.append(f"Error processing page {page.title}: {e}")

            result.success = True

        except Exception as e:
            logger.exception("Confluence label sync failed")
            result.errors.append(str(e))

        return result

    async def _list_space_pages(self, space_key: str) -> list[ConfluencePage]:
        """
        List all pages in a Confluence space.
        This is a stub - actual implementation would use Confluence REST API.
        """
        # In real implementation:
        # import httpx
        # import base64
        #
        # auth = base64.b64encode(
        #     f"{self.config.username}:{self.config.api_token}".encode()
        # ).decode()
        #
        # async with httpx.AsyncClient() as client:
        #     response = await client.get(
        #         f"{self.config.base_url}/rest/api/content",
        #         params={"spaceKey": space_key, "expand": "body.storage,version"},
        #         headers={"Authorization": f"Basic {auth}"},
        #     )

        logger.info(f"Would list pages from Confluence space: {space_key}")
        return []

    async def _get_page(self, page_id: str) -> ConfluencePage | None:
        """Fetch a single Confluence page with content."""
        logger.info(f"Would fetch Confluence page: {page_id}")
        return None

    async def _search_by_label(self, label: str) -> list[ConfluencePage]:
        """Search for pages with a specific label."""
        logger.info(f"Would search Confluence for label: {label}")
        return []

    def _should_skip_page(self, page: ConfluencePage) -> bool:
        """Check if page should be skipped (not modified)."""
        cached_version = self._page_cache.get(page.page_id)
        if cached_version and page.version <= cached_version:
            return True
        return False

    async def _process_page(
        self,
        page: ConfluencePage,
        node_type: NodeType | None,
        result: ConfluenceSyncResult,
    ) -> None:
        """Process a Confluence page and create/update civilization node."""
        if not self.ingestor:
            logger.warning("No ingestor configured")
            return

        # Detect node type from labels if not specified
        detected_type = node_type or self._detect_node_type(page)

        # Convert HTML to markdown-ish text
        content = self._convert_confluence_content(page)

        # Ingest
        ingest_result = await self.ingestor.ingest(
            content=content,
            filename=f"{page.title}.md",
            source="confluence",
            source_url=page.web_url,
            node_type_hint=detected_type,
            tags=page.labels,
        )

        if ingest_result.success:
            result.nodes_created += 1
            self._page_cache[page.page_id] = page.version
        else:
            result.errors.extend(ingest_result.errors)

    def _detect_node_type(self, page: ConfluencePage) -> NodeType:
        """Detect node type from page labels."""
        for label in page.labels:
            label_lower = label.lower()
            if label_lower in self.LABEL_TYPE_MAPPING:
                return self.LABEL_TYPE_MAPPING[label_lower]
        return NodeType.KNOWLEDGE_ARTICLE

    def _convert_confluence_content(self, page: ConfluencePage) -> str:
        """Convert Confluence storage format to plain text/markdown."""
        import re

        content = page.body_storage or page.body_html

        # Simple HTML tag stripping (in production, use a proper HTML parser)
        content = re.sub(r"<br\s*/?>", "\n", content)
        content = re.sub(r"<p[^>]*>", "\n", content)
        content = re.sub(r"</p>", "\n", content)
        content = re.sub(r"<li[^>]*>", "- ", content)
        content = re.sub(r"<h(\d)[^>]*>", r"\n#" + r"\1 ", content)
        content = re.sub(r"</h\d>", "\n", content)
        content = re.sub(r"<[^>]+>", "", content)

        # Clean up whitespace
        content = re.sub(r"\n{3,}", "\n\n", content)

        # Add title
        return f"# {page.title}\n\n{content.strip()}"

    def get_sync_status(self) -> dict:
        """Get current sync status."""
        return {
            "last_sync": self._last_sync.isoformat() if self._last_sync else None,
            "configured_spaces": self.config.space_keys,
            "sync_interval_minutes": self.config.sync_interval_minutes,
            "cached_pages": len(self._page_cache),
            "label_filters": self.config.label_filters,
        }

    def clear_cache(self) -> None:
        """Clear page version cache (forces full re-sync)."""
        self._page_cache.clear()
