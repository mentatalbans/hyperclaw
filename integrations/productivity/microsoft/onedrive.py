"""OneDriveConnector — Microsoft OneDrive API integration."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)
from integrations.productivity.microsoft.base import MicrosoftOAuthBase

logger = logging.getLogger("hyperclaw.integrations.onedrive")


class OneDriveConnector(BaseConnector, MicrosoftOAuthBase):
    """
    Microsoft OneDrive connector via Graph API.

    Required config (via MicrosoftOAuthBase):
        - access_token OR (client_id + client_secret + tenant_id)
    """

    def __init__(self, config: dict) -> None:
        self.config = config
        self._init_microsoft_auth(config)
        self._validate_microsoft_config(config)

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="onedrive",
            platform="onedrive",
            category="productivity",
            capabilities=frozenset(
                [
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.DELETE_DATA,
                    ConnectorCapability.LIST_DATA,
                    ConnectorCapability.FILE_UPLOAD,
                    ConnectorCapability.FILE_DOWNLOAD,
                    ConnectorCapability.SEARCH,
                ]
            ),
            auth_type="oauth2",
            rate_limit_per_minute=100,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        return await self._get_microsoft_client()

    async def health(self) -> bool:
        """Check if OneDrive API is accessible."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.GRAPH_URL}/me/drive")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"OneDrive health check failed: {e}")
            return False

    async def list_folder(
        self,
        folder_path: str = "root",
        max_items: int = 100,
    ) -> list[dict]:
        """List items in a folder."""
        client = await self._get_client()

        if folder_path == "root":
            url = f"{self.GRAPH_URL}/me/drive/root/children"
        else:
            url = f"{self.GRAPH_URL}/me/drive/root:/{folder_path}:/children"

        response = await client.get(url, params={"$top": max_items})

        if response.status_code != 200:
            raise Exception(f"OneDrive list folder failed: {response.text}")

        return response.json().get("value", [])

    async def upload_file(
        self,
        local_path: str,
        drive_path: str,
    ) -> dict:
        """Upload a file to OneDrive."""
        client = await self._get_client()

        file_path = Path(local_path)

        with open(local_path, "rb") as f:
            content = f.read()

        response = await client.put(
            f"{self.GRAPH_URL}/me/drive/root:/{drive_path}:/content",
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )

        if response.status_code not in (200, 201):
            raise Exception(f"OneDrive upload failed: {response.text}")

        return response.json()

    async def download_file(
        self,
        drive_path: str,
        destination: str,
    ) -> str:
        """Download a file from OneDrive."""
        client = await self._get_client()

        response = await client.get(
            f"{self.GRAPH_URL}/me/drive/root:/{drive_path}:/content",
        )

        if response.status_code != 200:
            raise Exception(f"OneDrive download failed: {response.text}")

        with open(destination, "wb") as f:
            f.write(response.content)

        return destination

    async def get_file_metadata(self, drive_path: str) -> dict:
        """Get file metadata."""
        client = await self._get_client()

        response = await client.get(f"{self.GRAPH_URL}/me/drive/root:/{drive_path}")

        if response.status_code != 200:
            raise Exception(f"OneDrive get metadata failed: {response.text}")

        return response.json()

    async def create_folder(
        self,
        name: str,
        parent_path: str = "root",
    ) -> dict:
        """Create a folder."""
        client = await self._get_client()

        if parent_path == "root":
            url = f"{self.GRAPH_URL}/me/drive/root/children"
        else:
            url = f"{self.GRAPH_URL}/me/drive/root:/{parent_path}:/children"

        response = await client.post(
            url,
            json={
                "name": name,
                "folder": {},
                "@microsoft.graph.conflictBehavior": "rename",
            },
        )

        if response.status_code not in (200, 201):
            raise Exception(f"OneDrive create folder failed: {response.text}")

        return response.json()

    async def delete_item(self, drive_path: str) -> bool:
        """Delete a file or folder."""
        client = await self._get_client()

        response = await client.delete(f"{self.GRAPH_URL}/me/drive/root:/{drive_path}")

        return response.status_code == 204

    async def search_files(self, query: str) -> list[dict]:
        """Search for files."""
        client = await self._get_client()

        response = await client.get(
            f"{self.GRAPH_URL}/me/drive/root/search(q='{query}')"
        )

        if response.status_code != 200:
            raise Exception(f"OneDrive search failed: {response.text}")

        return response.json().get("value", [])

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read file metadata."""
        return await self.get_file_metadata(resource_id)

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Upload a file or create a folder."""
        if resource_type == "folder":
            return await self.create_folder(
                name=data.get("name"),
                parent_path=data.get("parent_path", "root"),
            )
        else:
            return await self.upload_file(
                local_path=data.get("local_path"),
                drive_path=data.get("drive_path"),
            )

    async def _delete_impl(self, resource_id: str, **kwargs) -> bool:
        """Delete a file or folder."""
        return await self.delete_item(resource_id)

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List files."""
        return await self.list_folder(
            folder_path=filters.get("folder_path", "root"),
            max_items=filters.get("max_items", 100),
        )

    async def _search_impl(self, query: str, **kwargs) -> list[dict]:
        """Search files."""
        return await self.search_files(query)
