"""NotionConnector — Notion API integration."""

from __future__ import annotations

import logging

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)

logger = logging.getLogger("hyperclaw.integrations.notion")


class NotionConnector(BaseConnector):
    """
    Notion API connector.

    Required config:
        - integration_token: Notion integration token
    """

    BASE_URL = "https://api.notion.com/v1"
    NOTION_VERSION = "2022-06-28"

    def __init__(self, config: dict) -> None:
        self.config = config
        self.integration_token = config.get("integration_token", "")

        if config.get("enabled", False) and not self.integration_token:
            raise ValueError("NotionConnector requires integration_token")

        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="notion",
            platform="notion",
            category="productivity",
            capabilities=frozenset(
                [
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.DELETE_DATA,
                    ConnectorCapability.LIST_DATA,
                    ConnectorCapability.SEARCH,
                ]
            ),
            auth_type="bearer",
            rate_limit_per_minute=100,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={
                    "Authorization": f"Bearer {self.integration_token}",
                    "Content-Type": "application/json",
                    "Notion-Version": self.NOTION_VERSION,
                },
            )
        return self._client

    async def health(self) -> bool:
        """Check if Notion API is accessible."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.BASE_URL}/users/me")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Notion health check failed: {e}")
            return False

    async def get_page(self, page_id: str) -> dict:
        """Get a page by ID."""
        client = await self._get_client()

        response = await client.get(f"{self.BASE_URL}/pages/{page_id}")

        if response.status_code != 200:
            raise Exception(f"Notion get page failed: {response.text}")

        return response.json()

    async def create_page(
        self,
        parent_id: str,
        title: str,
        content_blocks: list[dict] | None = None,
    ) -> dict:
        """Create a new page."""
        client = await self._get_client()

        body = {
            "parent": {"page_id": parent_id},
            "properties": {
                "title": {"title": [{"text": {"content": title}}]}
            },
        }

        if content_blocks:
            body["children"] = content_blocks

        response = await client.post(
            f"{self.BASE_URL}/pages",
            json=body,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Notion create page failed: {response.text}")

        return response.json()

    async def update_page(
        self,
        page_id: str,
        properties: dict,
    ) -> dict:
        """Update page properties."""
        client = await self._get_client()

        response = await client.patch(
            f"{self.BASE_URL}/pages/{page_id}",
            json={"properties": properties},
        )

        if response.status_code != 200:
            raise Exception(f"Notion update page failed: {response.text}")

        return response.json()

    async def query_database(
        self,
        database_id: str,
        filter: dict | None = None,
        sort: list[dict] | None = None,
    ) -> list[dict]:
        """Query a database."""
        client = await self._get_client()

        body = {}
        if filter:
            body["filter"] = filter
        if sort:
            body["sorts"] = sort

        response = await client.post(
            f"{self.BASE_URL}/databases/{database_id}/query",
            json=body,
        )

        if response.status_code != 200:
            raise Exception(f"Notion query database failed: {response.text}")

        return response.json().get("results", [])

    async def create_database_entry(
        self,
        database_id: str,
        properties: dict,
    ) -> dict:
        """Create a new entry in a database."""
        client = await self._get_client()

        body = {
            "parent": {"database_id": database_id},
            "properties": properties,
        }

        response = await client.post(
            f"{self.BASE_URL}/pages",
            json=body,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Notion create entry failed: {response.text}")

        return response.json()

    async def search(self, query: str) -> list[dict]:
        """Search Notion workspace."""
        client = await self._get_client()

        response = await client.post(
            f"{self.BASE_URL}/search",
            json={"query": query},
        )

        if response.status_code != 200:
            raise Exception(f"Notion search failed: {response.text}")

        return response.json().get("results", [])

    async def archive_page(self, page_id: str) -> bool:
        """Archive (soft delete) a page."""
        client = await self._get_client()

        response = await client.patch(
            f"{self.BASE_URL}/pages/{page_id}",
            json={"archived": True},
        )

        return response.status_code == 200

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read a page."""
        return await self.get_page(resource_id)

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Create a page or database entry."""
        if resource_type == "database_entry":
            return await self.create_database_entry(
                database_id=data.get("database_id"),
                properties=data.get("properties", {}),
            )
        return await self.create_page(
            parent_id=data.get("parent_id"),
            title=data.get("title", "Untitled"),
            content_blocks=data.get("content_blocks"),
        )

    async def _delete_impl(self, resource_id: str, **kwargs) -> bool:
        """Archive a page."""
        return await self.archive_page(resource_id)

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """Query a database."""
        return await self.query_database(
            database_id=filters.get("database_id"),
            filter=filters.get("filter"),
            sort=filters.get("sort"),
        )

    async def _search_impl(self, query: str, **kwargs) -> list[dict]:
        """Search implementation."""
        return await self.search(query)
