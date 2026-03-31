"""DropboxConnector — Dropbox cloud storage integration."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)

logger = logging.getLogger("hyperclaw.integrations.dropbox")


class DropboxConnector(BaseConnector):
    """
    Dropbox cloud storage connector.

    Required config:
        - access_token: Dropbox API access token
    """

    BASE_URL = "https://api.dropboxapi.com/2"
    CONTENT_URL = "https://content.dropboxapi.com/2"

    def __init__(self, config: dict) -> None:
        self.config = config
        self.access_token = config.get("access_token", "")

        if config.get("enabled", False) and not self.access_token:
            raise ValueError("DropboxConnector requires access_token")

        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="dropbox",
            platform="dropbox",
            category="storage",
            capabilities=frozenset(
                [
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.DELETE_DATA,
                    ConnectorCapability.FILE_UPLOAD,
                    ConnectorCapability.FILE_DOWNLOAD,
                    ConnectorCapability.SEARCH,
                ]
            ),
            auth_type="bearer",
            rate_limit_per_minute=60,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=60.0,
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def health(self) -> bool:
        """Check if Dropbox API is accessible."""
        try:
            client = await self._get_client()
            response = await client.post(
                f"{self.BASE_URL}/users/get_current_account"
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Dropbox health check failed: {e}")
            return False

    async def list_folder(self, path: str = "") -> dict:
        """List items in a folder. path='' is the root folder."""
        client = await self._get_client()

        response = await client.post(
            f"{self.BASE_URL}/files/list_folder",
            json={"path": path if path else ""},
        )

        if response.status_code != 200:
            raise Exception(f"Dropbox list_folder failed: {response.text}")

        return response.json()

    async def get_file_metadata(self, path: str) -> dict:
        """Get file metadata."""
        client = await self._get_client()

        response = await client.post(
            f"{self.BASE_URL}/files/get_metadata",
            json={"path": path},
        )

        if response.status_code != 200:
            raise Exception(f"Dropbox get_file_metadata failed: {response.text}")

        return response.json()

    async def upload_file(self, local_path: str, dropbox_path: str) -> dict:
        """Upload a file to Dropbox."""
        file_path = Path(local_path)

        with open(file_path, "rb") as f:
            file_content = f.read()

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/octet-stream",
            "Dropbox-API-Arg": json.dumps({
                "path": dropbox_path,
                "mode": "add",
                "autorename": True,
            }),
        }

        async with httpx.AsyncClient(timeout=120.0) as upload_client:
            response = await upload_client.post(
                f"{self.CONTENT_URL}/files/upload",
                headers=headers,
                content=file_content,
            )

        if response.status_code != 200:
            raise Exception(f"Dropbox upload_file failed: {response.text}")

        return response.json()

    async def download_file(self, dropbox_path: str, destination: str) -> str:
        """Download a file from Dropbox."""
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Dropbox-API-Arg": json.dumps({"path": dropbox_path}),
        }

        async with httpx.AsyncClient(timeout=120.0) as download_client:
            response = await download_client.post(
                f"{self.CONTENT_URL}/files/download",
                headers=headers,
            )

        if response.status_code != 200:
            raise Exception(f"Dropbox download_file failed: {response.text}")

        dest_path = Path(destination)
        dest_path.write_bytes(response.content)

        return str(dest_path)

    async def delete(self, path: str) -> dict:
        """Delete a file or folder."""
        client = await self._get_client()

        response = await client.post(
            f"{self.BASE_URL}/files/delete_v2",
            json={"path": path},
        )

        if response.status_code != 200:
            raise Exception(f"Dropbox delete failed: {response.text}")

        return response.json()

    async def search(self, query: str) -> dict:
        """Search for files and folders."""
        client = await self._get_client()

        response = await client.post(
            f"{self.BASE_URL}/files/search_v2",
            json={"query": query},
        )

        if response.status_code != 200:
            raise Exception(f"Dropbox search failed: {response.text}")

        return response.json()

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read file metadata."""
        return await self.get_file_metadata(resource_id)

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Upload a file."""
        return await self.upload_file(
            local_path=data.get("local_path"),
            dropbox_path=data.get("dropbox_path"),
        )

    async def _delete_impl(self, resource_id: str, **kwargs) -> bool:
        """Delete a file or folder."""
        await self.delete(resource_id)
        return True

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List folder contents."""
        result = await self.list_folder(filters.get("path", ""))
        return result.get("entries", [])

    async def _search_impl(self, query: str, **kwargs) -> list[dict]:
        """Search files and folders."""
        result = await self.search(query)
        return result.get("matches", [])
