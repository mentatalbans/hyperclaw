"""GoogleDriveConnector — Google Drive API integration."""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)
from integrations.productivity.google.base import GoogleOAuthBase

logger = logging.getLogger("hyperclaw.integrations.google_drive")


class GoogleDriveConnector(BaseConnector, GoogleOAuthBase):
    """
    Google Drive API connector.

    Required config (via GoogleOAuthBase):
        - access_token or credentials_file
    """

    BASE_URL = "https://www.googleapis.com/drive/v3"
    UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3"

    def __init__(self, config: dict) -> None:
        self.config = config
        self._init_google_auth(config)
        self._validate_google_config(config)

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="google_drive",
            platform="google_drive",
            category="productivity",
            capabilities=frozenset(
                [
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.DELETE_DATA,
                    ConnectorCapability.LIST_DATA,
                    ConnectorCapability.SEARCH,
                    ConnectorCapability.FILE_UPLOAD,
                    ConnectorCapability.FILE_DOWNLOAD,
                ]
            ),
            auth_type="oauth2",
            rate_limit_per_minute=100,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        return await self._get_google_client()

    async def health(self) -> bool:
        """Check if Drive API is accessible."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.BASE_URL}/about", params={"fields": "user"})
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Google Drive health check failed: {e}")
            return False

    async def upload_file(
        self,
        path: str,
        folder_id: str | None = None,
        mime_type: str | None = None,
    ) -> dict:
        """Upload a file to Google Drive."""
        client = await self._get_client()

        file_path = Path(path)
        if not mime_type:
            mime_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"

        metadata = {"name": file_path.name}
        if folder_id:
            metadata["parents"] = [folder_id]

        with open(path, "rb") as f:
            content = f.read()

        # Use multipart upload
        response = await client.post(
            f"{self.UPLOAD_URL}/files",
            params={"uploadType": "multipart"},
            files={
                "metadata": ("metadata", str(metadata).encode(), "application/json"),
                "file": (file_path.name, content, mime_type),
            },
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Drive upload failed: {response.text}")

        return response.json()

    async def download_file(
        self,
        file_id: str,
        destination: str,
    ) -> str:
        """Download a file from Google Drive."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/files/{file_id}",
            params={"alt": "media"},
        )

        if response.status_code != 200:
            raise Exception(f"Drive download failed: {response.text}")

        with open(destination, "wb") as f:
            f.write(response.content)

        return destination

    async def list_folder(
        self,
        folder_id: str = "root",
        page_size: int = 100,
    ) -> list[dict]:
        """List files in a folder."""
        client = await self._get_client()

        query = f"'{folder_id}' in parents and trashed=false"

        response = await client.get(
            f"{self.BASE_URL}/files",
            params={
                "q": query,
                "pageSize": page_size,
                "fields": "files(id,name,mimeType,size,createdTime,modifiedTime)",
            },
        )

        if response.status_code != 200:
            raise Exception(f"Drive list folder failed: {response.text}")

        return response.json().get("files", [])

    async def search_files(self, query: str) -> list[dict]:
        """Search for files."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/files",
            params={
                "q": f"name contains '{query}' and trashed=false",
                "pageSize": 50,
                "fields": "files(id,name,mimeType,size,createdTime,modifiedTime)",
            },
        )

        if response.status_code != 200:
            raise Exception(f"Drive search failed: {response.text}")

        return response.json().get("files", [])

    async def create_folder(
        self,
        name: str,
        parent_id: str | None = None,
    ) -> dict:
        """Create a folder."""
        client = await self._get_client()

        metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            metadata["parents"] = [parent_id]

        response = await client.post(
            f"{self.BASE_URL}/files",
            json=metadata,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Drive create folder failed: {response.text}")

        return response.json()

    async def share_file(
        self,
        file_id: str,
        email: str,
        role: str = "reader",
    ) -> dict:
        """Share a file with a user."""
        client = await self._get_client()

        response = await client.post(
            f"{self.BASE_URL}/files/{file_id}/permissions",
            json={
                "type": "user",
                "role": role,
                "emailAddress": email,
            },
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Drive share failed: {response.text}")

        return response.json()

    async def get_file_metadata(self, file_id: str) -> dict:
        """Get file metadata."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/files/{file_id}",
            params={"fields": "id,name,mimeType,size,createdTime,modifiedTime,owners,permissions"},
        )

        if response.status_code != 200:
            raise Exception(f"Drive get metadata failed: {response.text}")

        return response.json()

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read file metadata."""
        return await self.get_file_metadata(resource_id)

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Create a file or folder."""
        if resource_type == "folder":
            return await self.create_folder(
                name=data.get("name", "Untitled"),
                parent_id=data.get("parent_id"),
            )
        else:
            return await self.upload_file(
                path=data.get("path"),
                folder_id=data.get("folder_id"),
                mime_type=data.get("mime_type"),
            )

    async def _delete_impl(self, resource_id: str, **kwargs) -> bool:
        """Delete a file."""
        client = await self._get_client()

        response = await client.delete(f"{self.BASE_URL}/files/{resource_id}")

        return response.status_code == 204

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List files."""
        return await self.list_folder(
            folder_id=filters.get("folder_id", "root"),
            page_size=filters.get("page_size", 100),
        )

    async def _search_impl(self, query: str, **kwargs) -> list[dict]:
        """Search files."""
        return await self.search_files(query)
