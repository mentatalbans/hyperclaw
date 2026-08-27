"""
Google Drive integration for syncing organizational documents.
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
class GDriveConfig:
    """Configuration for Google Drive sync."""
    credentials_path: str = ""
    folder_ids: list[str] = field(default_factory=list)
    folder_type_mapping: dict[str, NodeType] = field(default_factory=dict)
    sync_interval_minutes: int = 120
    file_types: list[str] = field(default_factory=lambda: [".md", ".txt", ".docx", ".pdf"])
    max_file_size_mb: int = 10


@dataclass
class GDriveFile:
    """Represents a Google Drive file."""
    file_id: str
    name: str
    mime_type: str
    folder_id: str
    web_view_link: str
    modified_time: datetime
    size_bytes: int = 0
    content: bytes | None = None


@dataclass
class GDriveSyncResult:
    """Result of a Google Drive sync operation."""
    success: bool
    files_processed: int = 0
    files_skipped: int = 0
    nodes_created: int = 0
    nodes_updated: int = 0
    errors: list[str] = field(default_factory=list)
    sync_time: datetime = field(default_factory=datetime.utcnow)


class GDriveSync:
    """
    Synchronizes Google Drive documents with Civilization Knowledge Layer.
    """

    SUPPORTED_MIME_TYPES = {
        "application/vnd.google-apps.document": "gdoc",
        "text/plain": "txt",
        "text/markdown": "md",
        "application/pdf": "pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    }

    def __init__(
        self,
        org_id: str,
        config: GDriveConfig,
        ingestor: DocumentIngestor | None = None,
    ):
        self.org_id = org_id
        self.config = config
        self.ingestor = ingestor
        self._last_sync: datetime | None = None
        self._file_cache: dict[str, datetime] = {}  # file_id -> last_modified

    async def sync_folder(
        self,
        folder_id: str,
        node_type: NodeType | None = None,
        recursive: bool = True,
    ) -> GDriveSyncResult:
        """
        Sync a Google Drive folder to civilization nodes.

        Args:
            folder_id: Google Drive folder ID
            node_type: Optional node type to assign to all files
            recursive: Whether to sync subfolders

        Returns:
            GDriveSyncResult with sync statistics
        """
        result = GDriveSyncResult(success=False)

        try:
            # Fetch files from folder
            files = await self._list_folder_files(folder_id, recursive)

            for file in files:
                try:
                    # Skip if not modified since last sync
                    if self._should_skip_file(file):
                        result.files_skipped += 1
                        continue

                    # Process file
                    await self._process_file(file, node_type, result)
                    result.files_processed += 1

                except Exception as e:
                    result.errors.append(f"Error processing file {file.name}: {e}")

            result.success = True
            self._last_sync = datetime.utcnow()

        except Exception as e:
            logger.exception("Google Drive sync failed")
            result.errors.append(str(e))

        return result

    async def sync_all_folders(self) -> GDriveSyncResult:
        """Sync all configured folders."""
        total_result = GDriveSyncResult(success=True)

        for folder_id in self.config.folder_ids:
            node_type = self.config.folder_type_mapping.get(folder_id)
            result = await self.sync_folder(folder_id, node_type)

            total_result.files_processed += result.files_processed
            total_result.files_skipped += result.files_skipped
            total_result.nodes_created += result.nodes_created
            total_result.nodes_updated += result.nodes_updated
            total_result.errors.extend(result.errors)

            if not result.success:
                total_result.success = False

        return total_result

    async def sync_file(self, file_id: str) -> CivilizationNode | None:
        """Sync a single file by ID."""
        file = await self._get_file(file_id)
        if not file:
            return None

        result = GDriveSyncResult(success=False)
        await self._process_file(file, None, result)

        return None  # Would return created/updated node

    async def _list_folder_files(
        self,
        folder_id: str,
        recursive: bool,
    ) -> list[GDriveFile]:
        """
        List files in a Google Drive folder.
        This is a stub - actual implementation would use Google Drive API.
        """
        # In real implementation:
        # from google.oauth2 import service_account
        # from googleapiclient.discovery import build
        #
        # creds = service_account.Credentials.from_service_account_file(
        #     self.config.credentials_path
        # )
        # service = build('drive', 'v3', credentials=creds)
        # results = service.files().list(
        #     q=f"'{folder_id}' in parents",
        #     pageSize=100,
        #     fields="files(id, name, mimeType, modifiedTime, size, webViewLink)"
        # ).execute()

        logger.info(f"Would list files from Google Drive folder: {folder_id}")
        return []

    async def _get_file(self, file_id: str) -> GDriveFile | None:
        """Fetch a single file with content."""
        logger.info(f"Would fetch Google Drive file: {file_id}")
        return None

    async def _download_file_content(self, file: GDriveFile) -> bytes | None:
        """Download file content."""
        # Check size limit
        max_size = self.config.max_file_size_mb * 1024 * 1024
        if file.size_bytes > max_size:
            logger.warning(f"File {file.name} exceeds size limit")
            return None

        # In real implementation, would download file content
        logger.info(f"Would download content for file: {file.name}")
        return None

    def _should_skip_file(self, file: GDriveFile) -> bool:
        """Check if file should be skipped (not modified)."""
        # Check mime type
        if file.mime_type not in self.SUPPORTED_MIME_TYPES:
            return True

        # Check extension
        ext = "." + file.name.split(".")[-1].lower() if "." in file.name else ""
        if ext and ext not in self.config.file_types:
            return True

        # Check if modified since last seen
        last_seen = self._file_cache.get(file.file_id)
        if last_seen and file.modified_time <= last_seen:
            return True

        return False

    async def _process_file(
        self,
        file: GDriveFile,
        node_type: NodeType | None,
        result: GDriveSyncResult,
    ) -> None:
        """Process a Google Drive file and create/update civilization node."""
        if not self.ingestor:
            logger.warning("No ingestor configured")
            return

        # Download content
        content = await self._download_file_content(file)
        if not content:
            return

        # Ingest
        ingest_result = await self.ingestor.ingest(
            content=content,
            filename=file.name,
            source="gdrive",
            source_url=file.web_view_link,
            node_type_hint=node_type,
        )

        if ingest_result.success:
            result.nodes_created += 1
            self._file_cache[file.file_id] = file.modified_time
        else:
            result.errors.extend(ingest_result.errors)

    def get_sync_status(self) -> dict:
        """Get current sync status."""
        return {
            "last_sync": self._last_sync.isoformat() if self._last_sync else None,
            "configured_folders": self.config.folder_ids,
            "sync_interval_minutes": self.config.sync_interval_minutes,
            "cached_files": len(self._file_cache),
        }

    def clear_cache(self) -> None:
        """Clear the file modification cache (forces full re-sync)."""
        self._file_cache.clear()
