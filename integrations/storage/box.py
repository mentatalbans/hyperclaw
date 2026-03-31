"""BoxConnector — Box cloud storage integration."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)

logger = logging.getLogger("hyperclaw.integrations.box")


class BoxConnector(BaseConnector):
    """
    Box cloud storage connector.

    Required config:
        - access_token: Box API access token
        OR
        - client_id + client_secret: For OAuth2 flow
    """

    BASE_URL = "https://api.box.com/2.0"
    UPLOAD_URL = "https://upload.box.com/api/2.0"

    # HyperShield context for enterprise data
    hypershield_context = "enterprise_data"

    def __init__(self, config: dict) -> None:
        self.config = config
        self.access_token = config.get("access_token", "")
        self.client_id = config.get("client_id", "")
        self.client_secret = config.get("client_secret", "")

        if config.get("enabled", False) and not self.access_token:
            if not (self.client_id and self.client_secret):
                raise ValueError(
                    "BoxConnector requires access_token or client_id + client_secret"
                )

        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="box",
            platform="box",
            category="storage",
            capabilities=frozenset(
                [
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.DELETE_DATA,
                    ConnectorCapability.LIST_DATA,
                    ConnectorCapability.FILE_UPLOAD,
                    ConnectorCapability.FILE_DOWNLOAD,
                    ConnectorCapability.SEARCH,
                    ConnectorCapability.ACTION,
                ]
            ),
            auth_type="oauth2",
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
        """Check if Box API is accessible."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.BASE_URL}/users/me")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Box health check failed: {e}")
            return False

    async def list_folder(self, folder_id: str = "0") -> dict:
        """List items in a folder. folder_id='0' is the root folder."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/folders/{folder_id}/items",
            params={"limit": 100},
        )

        if response.status_code != 200:
            raise Exception(f"Box list_folder failed: {response.text}")

        return response.json()

    async def get_file(self, file_id: str) -> dict:
        """Get file metadata."""
        client = await self._get_client()

        response = await client.get(f"{self.BASE_URL}/files/{file_id}")

        if response.status_code != 200:
            raise Exception(f"Box get_file failed: {response.text}")

        return response.json()

    async def upload_file(self, path: str, folder_id: str = "0") -> dict:
        """Upload a file to Box."""
        client = await self._get_client()
        file_path = Path(path)

        # Box requires multipart form for uploads
        with open(file_path, "rb") as f:
            files = {
                "file": (file_path.name, f, "application/octet-stream"),
            }
            data = {
                "attributes": f'{{"name": "{file_path.name}", "parent": {{"id": "{folder_id}"}}}}',
            }

            # Remove Content-Type header for multipart
            headers = {"Authorization": f"Bearer {self.access_token}"}

            async with httpx.AsyncClient(timeout=120.0, headers=headers) as upload_client:
                response = await upload_client.post(
                    f"{self.UPLOAD_URL}/files/content",
                    data=data,
                    files=files,
                )

        if response.status_code not in (200, 201):
            raise Exception(f"Box upload_file failed: {response.text}")

        return response.json()

    async def download_file(self, file_id: str, destination: str) -> str:
        """Download a file from Box."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/files/{file_id}/content",
            follow_redirects=True,
        )

        if response.status_code != 200:
            raise Exception(f"Box download_file failed: {response.text}")

        dest_path = Path(destination)
        dest_path.write_bytes(response.content)

        return str(dest_path)

    async def create_folder(self, name: str, parent_id: str = "0") -> dict:
        """Create a new folder."""
        client = await self._get_client()

        response = await client.post(
            f"{self.BASE_URL}/folders",
            json={
                "name": name,
                "parent": {"id": parent_id},
            },
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Box create_folder failed: {response.text}")

        return response.json()

    async def share_file(
        self, file_id: str, email: str, role: str = "viewer"
    ) -> dict:
        """Share a file with a user via collaboration."""
        client = await self._get_client()

        response = await client.post(
            f"{self.BASE_URL}/collaborations",
            json={
                "item": {"type": "file", "id": file_id},
                "accessible_by": {"type": "user", "login": email},
                "role": role,
            },
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Box share_file failed: {response.text}")

        return response.json()

    async def search(self, query: str) -> dict:
        """Search for files and folders."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/search",
            params={"query": query},
        )

        if response.status_code != 200:
            raise Exception(f"Box search failed: {response.text}")

        return response.json()

    async def get_metadata(self, file_id: str) -> dict:
        """Get file metadata including custom metadata."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/files/{file_id}",
            params={"fields": "metadata"},
        )

        if response.status_code != 200:
            raise Exception(f"Box get_metadata failed: {response.text}")

        return response.json()

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read file metadata."""
        return await self.get_file(resource_id)

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Create a folder."""
        if resource_type == "folder":
            return await self.create_folder(
                name=data.get("name", "New Folder"),
                parent_id=data.get("parent_id", "0"),
            )
        raise ValueError(f"Unknown resource type: {resource_type}")

    async def _delete_impl(self, resource_id: str, **kwargs) -> bool:
        """Delete a file."""
        client = await self._get_client()
        response = await client.delete(f"{self.BASE_URL}/files/{resource_id}")
        return response.status_code == 204

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List folder contents."""
        result = await self.list_folder(filters.get("folder_id", "0"))
        return result.get("entries", [])

    async def _search_impl(self, query: str, **kwargs) -> list[dict]:
        """Search files and folders."""
        result = await self.search(query)
        return result.get("entries", [])

    async def _action_impl(self, action_name: str, params: dict) -> dict:
        """Execute Box-specific actions."""
        if action_name == "share":
            return await self.share_file(
                file_id=params.get("file_id"),
                email=params.get("email"),
                role=params.get("role", "viewer"),
            )
        elif action_name == "upload":
            return await self.upload_file(
                path=params.get("path"),
                folder_id=params.get("folder_id", "0"),
            )
        elif action_name == "download":
            path = await self.download_file(
                file_id=params.get("file_id"),
                destination=params.get("destination"),
            )
            return {"path": path}

        raise ValueError(f"Unknown action: {action_name}")
