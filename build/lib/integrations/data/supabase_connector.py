"""SupabaseConnector — Supabase database integration."""

from __future__ import annotations

import logging

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)

logger = logging.getLogger("hyperclaw.integrations.supabase")


class SupabaseConnector(BaseConnector):
    """
    Supabase database connector.

    Required config:
        - url: Supabase project URL
        - service_role_key: Supabase service role key
    """

    def __init__(self, config: dict) -> None:
        self.config = config
        self.url = config.get("url", "").rstrip("/")
        self.service_role_key = config.get("service_role_key", "")

        if config.get("enabled", False):
            if not self.url or not self.service_role_key:
                raise ValueError(
                    "SupabaseConnector requires url and service_role_key"
                )

        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="supabase",
            platform="supabase",
            category="data",
            capabilities=frozenset(
                [
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.DELETE_DATA,
                    ConnectorCapability.LIST_DATA,
                    ConnectorCapability.SEARCH,
                ]
            ),
            auth_type="api_key",
            rate_limit_per_minute=60,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={
                    "apikey": self.service_role_key,
                    "Authorization": f"Bearer {self.service_role_key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=representation",
                },
            )
        return self._client

    async def health(self) -> bool:
        """Check if Supabase API is accessible."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.url}/rest/v1/")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Supabase health check failed: {e}")
            return False

    async def query(self, table: str, filters: dict | None = None) -> list[dict]:
        """Query a table with optional filters."""
        client = await self._get_client()

        url = f"{self.url}/rest/v1/{table}"
        params = {}

        if filters:
            for key, value in filters.items():
                params[key] = f"eq.{value}"

        response = await client.get(url, params=params)

        if response.status_code != 200:
            raise Exception(f"Supabase query failed: {response.text}")

        return response.json()

    async def insert(self, table: str, data: dict) -> dict:
        """Insert a row into a table."""
        client = await self._get_client()

        response = await client.post(
            f"{self.url}/rest/v1/{table}",
            json=data,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Supabase insert failed: {response.text}")

        result = response.json()
        return result[0] if isinstance(result, list) else result

    async def update(self, table: str, id: str, data: dict) -> dict:
        """Update a row in a table."""
        client = await self._get_client()

        response = await client.patch(
            f"{self.url}/rest/v1/{table}",
            params={"id": f"eq.{id}"},
            json=data,
        )

        if response.status_code not in (200, 204):
            raise Exception(f"Supabase update failed: {response.text}")

        result = response.json()
        return result[0] if isinstance(result, list) and result else {}

    async def delete(self, table: str, id: str) -> bool:
        """Delete a row from a table."""
        client = await self._get_client()

        response = await client.delete(
            f"{self.url}/rest/v1/{table}",
            params={"id": f"eq.{id}"},
        )

        if response.status_code not in (200, 204):
            raise Exception(f"Supabase delete failed: {response.text}")

        return True

    async def rpc(self, function_name: str, params: dict | None = None) -> dict:
        """Call a Supabase RPC function."""
        client = await self._get_client()

        response = await client.post(
            f"{self.url}/rest/v1/rpc/{function_name}",
            json=params or {},
        )

        if response.status_code != 200:
            raise Exception(f"Supabase rpc failed: {response.text}")

        return response.json()

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read a row by ID."""
        table = kwargs.get("table", "")
        if not table:
            raise ValueError("table parameter required")

        result = await self.query(table, {"id": resource_id})
        return result[0] if result else {}

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Insert a row."""
        return await self.insert(resource_type, data)

    async def _delete_impl(self, resource_id: str, **kwargs) -> bool:
        """Delete a row."""
        table = kwargs.get("table", "")
        if not table:
            raise ValueError("table parameter required")

        return await self.delete(table, resource_id)

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List rows from a table."""
        return await self.query(resource_type, filters)

    async def _search_impl(self, query: str, **kwargs) -> list[dict]:
        """Search using RPC or full-text search."""
        table = kwargs.get("table", "")
        column = kwargs.get("column", "")

        if table and column:
            # Use PostgREST full-text search
            client = await self._get_client()
            response = await client.get(
                f"{self.url}/rest/v1/{table}",
                params={column: f"ilike.*{query}*"},
            )
            if response.status_code == 200:
                return response.json()

        # Fallback to RPC if available
        return await self.rpc("search", {"query": query})
