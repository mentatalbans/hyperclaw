"""AirtableConnector — Airtable API integration."""

from __future__ import annotations

import logging

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)

logger = logging.getLogger("hyperclaw.integrations.airtable")


class AirtableConnector(BaseConnector):
    """
    Airtable API connector.

    Required config:
        - api_key: Airtable API key or Personal Access Token
        - base_id: Airtable base ID
    """

    BASE_URL = "https://api.airtable.com/v0"

    def __init__(self, config: dict) -> None:
        self.config = config
        self.api_key = config.get("api_key", "")
        self.base_id = config.get("base_id", "")

        if config.get("enabled", False):
            if not self.api_key:
                raise ValueError("AirtableConnector requires api_key")
            if not self.base_id:
                raise ValueError("AirtableConnector requires base_id")

        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="airtable",
            platform="airtable",
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
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def health(self) -> bool:
        """Check if Airtable API is accessible."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.BASE_URL}/meta/bases")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Airtable health check failed: {e}")
            return False

    async def list_records(
        self,
        table_name: str,
        filter_formula: str | None = None,
        sort: list[dict] | None = None,
        max_records: int = 100,
    ) -> list[dict]:
        """List records in a table."""
        client = await self._get_client()

        params = {"maxRecords": max_records}
        if filter_formula:
            params["filterByFormula"] = filter_formula
        if sort:
            for i, s in enumerate(sort):
                params[f"sort[{i}][field]"] = s.get("field")
                params[f"sort[{i}][direction]"] = s.get("direction", "asc")

        response = await client.get(
            f"{self.BASE_URL}/{self.base_id}/{table_name}",
            params=params,
        )

        if response.status_code != 200:
            raise Exception(f"Airtable list records failed: {response.text}")

        return response.json().get("records", [])

    async def get_record(
        self,
        table_name: str,
        record_id: str,
    ) -> dict:
        """Get a single record."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/{self.base_id}/{table_name}/{record_id}"
        )

        if response.status_code != 200:
            raise Exception(f"Airtable get record failed: {response.text}")

        return response.json()

    async def create_record(
        self,
        table_name: str,
        fields: dict,
    ) -> dict:
        """Create a new record."""
        client = await self._get_client()

        response = await client.post(
            f"{self.BASE_URL}/{self.base_id}/{table_name}",
            json={"fields": fields},
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Airtable create record failed: {response.text}")

        return response.json()

    async def update_record(
        self,
        table_name: str,
        record_id: str,
        fields: dict,
    ) -> dict:
        """Update a record."""
        client = await self._get_client()

        response = await client.patch(
            f"{self.BASE_URL}/{self.base_id}/{table_name}/{record_id}",
            json={"fields": fields},
        )

        if response.status_code != 200:
            raise Exception(f"Airtable update record failed: {response.text}")

        return response.json()

    async def delete_record(
        self,
        table_name: str,
        record_id: str,
    ) -> bool:
        """Delete a record."""
        client = await self._get_client()

        response = await client.delete(
            f"{self.BASE_URL}/{self.base_id}/{table_name}/{record_id}"
        )

        return response.status_code == 200

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read a record."""
        return await self.get_record(
            table_name=kwargs.get("table_name"),
            record_id=resource_id,
        )

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Create a record."""
        return await self.create_record(
            table_name=data.get("table_name"),
            fields=data.get("fields", {}),
        )

    async def _delete_impl(self, resource_id: str, **kwargs) -> bool:
        """Delete a record."""
        return await self.delete_record(
            table_name=kwargs.get("table_name"),
            record_id=resource_id,
        )

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List records."""
        return await self.list_records(
            table_name=filters.get("table_name"),
            filter_formula=filters.get("filter_formula"),
            sort=filters.get("sort"),
            max_records=filters.get("max_records", 100),
        )

    async def _search_impl(self, query: str, **kwargs) -> list[dict]:
        """Search records using filter formula."""
        table_name = kwargs.get("table_name")
        field = kwargs.get("field", "Name")
        return await self.list_records(
            table_name=table_name,
            filter_formula=f"SEARCH('{query}', {{{field}}})",
        )
