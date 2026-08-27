"""SharePointConnector — Microsoft SharePoint API integration."""

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

logger = logging.getLogger("hyperclaw.integrations.sharepoint")


class SharePointConnector(BaseConnector, MicrosoftOAuthBase):
    """
    Microsoft SharePoint connector via Graph API.

    Required config (via MicrosoftOAuthBase):
        - access_token OR (client_id + client_secret + tenant_id)
        - site_id (optional): SharePoint site ID
    """

    def __init__(self, config: dict) -> None:
        self.config = config
        self._init_microsoft_auth(config)
        self._validate_microsoft_config(config)
        self.site_id = config.get("site_id", "")

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="sharepoint",
            platform="sharepoint",
            category="productivity",
            capabilities=frozenset(
                [
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.LIST_DATA,
                    ConnectorCapability.SEARCH,
                    ConnectorCapability.FILE_UPLOAD,
                ]
            ),
            auth_type="oauth2",
            rate_limit_per_minute=100,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        return await self._get_microsoft_client()

    async def health(self) -> bool:
        """Check if SharePoint API is accessible."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.GRAPH_URL}/sites/root")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"SharePoint health check failed: {e}")
            return False

    async def list_sites(self) -> list[dict]:
        """List SharePoint sites."""
        client = await self._get_client()

        response = await client.get(
            f"{self.GRAPH_URL}/sites",
            params={"search": "*"},
        )

        if response.status_code != 200:
            raise Exception(f"SharePoint list sites failed: {response.text}")

        return response.json().get("value", [])

    async def get_site(self, site_id: str) -> dict:
        """Get site details."""
        client = await self._get_client()

        response = await client.get(f"{self.GRAPH_URL}/sites/{site_id}")

        if response.status_code != 200:
            raise Exception(f"SharePoint get site failed: {response.text}")

        return response.json()

    async def list_drives(self, site_id: str | None = None) -> list[dict]:
        """List document libraries in a site."""
        client = await self._get_client()
        site = site_id or self.site_id

        if not site:
            raise ValueError("site_id is required")

        response = await client.get(f"{self.GRAPH_URL}/sites/{site}/drives")

        if response.status_code != 200:
            raise Exception(f"SharePoint list drives failed: {response.text}")

        return response.json().get("value", [])

    async def list_items(
        self,
        drive_id: str,
        folder_path: str = "root",
    ) -> list[dict]:
        """List items in a document library folder."""
        client = await self._get_client()

        if folder_path == "root":
            url = f"{self.GRAPH_URL}/drives/{drive_id}/root/children"
        else:
            url = f"{self.GRAPH_URL}/drives/{drive_id}/root:/{folder_path}:/children"

        response = await client.get(url)

        if response.status_code != 200:
            raise Exception(f"SharePoint list items failed: {response.text}")

        return response.json().get("value", [])

    async def upload_file(
        self,
        drive_id: str,
        local_path: str,
        remote_path: str,
    ) -> dict:
        """Upload a file to SharePoint."""
        client = await self._get_client()

        with open(local_path, "rb") as f:
            content = f.read()

        response = await client.put(
            f"{self.GRAPH_URL}/drives/{drive_id}/root:/{remote_path}:/content",
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )

        if response.status_code not in (200, 201):
            raise Exception(f"SharePoint upload failed: {response.text}")

        return response.json()

    async def search_content(self, query: str) -> list[dict]:
        """Search across SharePoint content."""
        client = await self._get_client()

        response = await client.get(
            f"{self.GRAPH_URL}/search/query",
            params={"query": query},
        )

        if response.status_code != 200:
            raise Exception(f"SharePoint search failed: {response.text}")

        return response.json().get("value", [])

    async def get_list_items(
        self,
        site_id: str,
        list_id: str,
    ) -> list[dict]:
        """Get items from a SharePoint list."""
        client = await self._get_client()

        response = await client.get(
            f"{self.GRAPH_URL}/sites/{site_id}/lists/{list_id}/items",
            params={"expand": "fields"},
        )

        if response.status_code != 200:
            raise Exception(f"SharePoint get list items failed: {response.text}")

        return response.json().get("value", [])

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read a site or drive item."""
        return await self.get_site(resource_id)

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Upload a file."""
        return await self.upload_file(
            drive_id=data.get("drive_id"),
            local_path=data.get("local_path"),
            remote_path=data.get("remote_path"),
        )

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List resources."""
        if resource_type == "sites":
            return await self.list_sites()
        elif resource_type == "drives":
            return await self.list_drives(filters.get("site_id"))
        elif resource_type == "items":
            return await self.list_items(
                drive_id=filters.get("drive_id"),
                folder_path=filters.get("folder_path", "root"),
            )
        return []

    async def _search_impl(self, query: str, **kwargs) -> list[dict]:
        """Search SharePoint content."""
        return await self.search_content(query)
