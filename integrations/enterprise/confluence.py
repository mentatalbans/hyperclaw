"""ConfluenceConnector — Atlassian Confluence API integration."""

from __future__ import annotations

import base64
import logging

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)

logger = logging.getLogger("hyperclaw.integrations.confluence")


class ConfluenceConnector(BaseConnector):
    """
    Confluence API connector.

    Required config:
        - base_url: Confluence instance URL (e.g., https://your-domain.atlassian.net/wiki)
        - email: User email for authentication
        - api_token: Confluence API token
    """

    def __init__(self, config: dict) -> None:
        self.config = config
        self.base_url = config.get("base_url", "").rstrip("/")
        self.email = config.get("email", "")
        self.api_token = config.get("api_token", "")

        if config.get("enabled", False):
            if not self.base_url:
                raise ValueError("ConfluenceConnector requires base_url")
            if not self.email:
                raise ValueError("ConfluenceConnector requires email")
            if not self.api_token:
                raise ValueError("ConfluenceConnector requires api_token")

        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="confluence",
            platform="confluence",
            category="enterprise",
            capabilities=frozenset(
                [
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.LIST_DATA,
                    ConnectorCapability.SEARCH,
                ]
            ),
            auth_type="api_key",
            rate_limit_per_minute=100,
        )

    def _get_auth_header(self) -> str:
        """Generate Basic auth header."""
        credentials = f"{self.email}:{self.api_token}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return f"Basic {encoded}"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={
                    "Authorization": self._get_auth_header(),
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def health(self) -> bool:
        """Check if Confluence API is accessible."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.base_url}/rest/api/space?limit=1")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Confluence health check failed: {e}")
            return False

    async def get_page(self, page_id: str) -> dict:
        """Get a page by ID."""
        client = await self._get_client()

        response = await client.get(
            f"{self.base_url}/rest/api/content/{page_id}",
            params={"expand": "body.storage,version"},
        )

        if response.status_code != 200:
            raise Exception(f"Confluence get page failed: {response.text}")

        return response.json()

    async def create_page(
        self,
        space_key: str,
        title: str,
        content: str,
        parent_id: str | None = None,
    ) -> dict:
        """Create a new page."""
        client = await self._get_client()

        body = {
            "type": "page",
            "title": title,
            "space": {"key": space_key},
            "body": {
                "storage": {
                    "value": content,
                    "representation": "storage",
                }
            },
        }

        if parent_id:
            body["ancestors"] = [{"id": parent_id}]

        response = await client.post(
            f"{self.base_url}/rest/api/content",
            json=body,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Confluence create page failed: {response.text}")

        return response.json()

    async def update_page(
        self,
        page_id: str,
        title: str,
        content: str,
        version: int,
    ) -> dict:
        """Update a page."""
        client = await self._get_client()

        body = {
            "version": {"number": version + 1},
            "title": title,
            "type": "page",
            "body": {
                "storage": {
                    "value": content,
                    "representation": "storage",
                }
            },
        }

        response = await client.put(
            f"{self.base_url}/rest/api/content/{page_id}",
            json=body,
        )

        if response.status_code != 200:
            raise Exception(f"Confluence update page failed: {response.text}")

        return response.json()

    async def search_content(
        self,
        query: str,
        limit: int = 25,
    ) -> list[dict]:
        """Search content using CQL."""
        client = await self._get_client()

        response = await client.get(
            f"{self.base_url}/rest/api/content/search",
            params={
                "cql": f'text ~ "{query}"',
                "limit": limit,
            },
        )

        if response.status_code != 200:
            raise Exception(f"Confluence search failed: {response.text}")

        return response.json().get("results", [])

    async def list_spaces(self) -> list[dict]:
        """List all spaces."""
        client = await self._get_client()

        response = await client.get(f"{self.base_url}/rest/api/space")

        if response.status_code != 200:
            raise Exception(f"Confluence list spaces failed: {response.text}")

        return response.json().get("results", [])

    async def get_space(self, space_key: str) -> dict:
        """Get a space by key."""
        client = await self._get_client()

        response = await client.get(f"{self.base_url}/rest/api/space/{space_key}")

        if response.status_code != 200:
            raise Exception(f"Confluence get space failed: {response.text}")

        return response.json()

    async def add_comment(
        self,
        page_id: str,
        body: str,
    ) -> dict:
        """Add a comment to a page."""
        client = await self._get_client()

        response = await client.post(
            f"{self.base_url}/rest/api/content/{page_id}/child/comment",
            json={
                "type": "comment",
                "container": {"id": page_id, "type": "page"},
                "body": {
                    "storage": {
                        "value": body,
                        "representation": "storage",
                    }
                },
            },
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Confluence add comment failed: {response.text}")

        return response.json()

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read a page."""
        return await self.get_page(resource_id)

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Create a page."""
        return await self.create_page(
            space_key=data.get("space_key"),
            title=data.get("title", "Untitled"),
            content=data.get("content", ""),
            parent_id=data.get("parent_id"),
        )

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List spaces."""
        if resource_type == "spaces":
            return await self.list_spaces()
        # List pages in a space
        cql = filters.get("cql", f'space = "{filters.get("space_key")}"')
        return await self.search_content(cql)

    async def _search_impl(self, query: str, **kwargs) -> list[dict]:
        """Search content."""
        return await self.search_content(query)
