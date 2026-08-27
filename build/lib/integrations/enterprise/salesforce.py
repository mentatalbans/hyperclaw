"""SalesforceConnector — Salesforce API integration."""

from __future__ import annotations

import logging

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)

logger = logging.getLogger("hyperclaw.integrations.salesforce")


class SalesforceConnector(BaseConnector):
    """
    Salesforce API connector.

    Required config:
        - client_id: Salesforce Connected App client ID
        - client_secret: Salesforce Connected App client secret
        - username: Salesforce username
        - password: Salesforce password (with security token appended)
    """

    LOGIN_URL = "https://login.salesforce.com/services/oauth2/token"

    def __init__(self, config: dict) -> None:
        self.config = config
        self.client_id = config.get("client_id", "")
        self.client_secret = config.get("client_secret", "")
        self.username = config.get("username", "")
        self.password = config.get("password", "")
        self.instance_url = config.get("instance_url", "")
        self.access_token = config.get("access_token", "")

        if config.get("enabled", False):
            has_oauth = self.client_id and self.client_secret and self.username and self.password
            has_token = self.access_token and self.instance_url
            if not has_oauth and not has_token:
                raise ValueError(
                    "SalesforceConnector requires (client_id, client_secret, username, password) "
                    "OR (access_token, instance_url)"
                )

        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="salesforce",
            platform="salesforce",
            category="enterprise",
            capabilities=frozenset(
                [
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.DELETE_DATA,
                    ConnectorCapability.LIST_DATA,
                    ConnectorCapability.SEARCH,
                    ConnectorCapability.WEBHOOKS,
                ]
            ),
            auth_type="oauth2",
            rate_limit_per_minute=100,
        )

    async def _authenticate(self) -> None:
        """Authenticate and get access token."""
        if self.access_token and self.instance_url:
            return

        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.LOGIN_URL,
                data={
                    "grant_type": "password",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "username": self.username,
                    "password": self.password,
                },
            )

            if response.status_code != 200:
                raise Exception(f"Salesforce auth failed: {response.text}")

            data = response.json()
            self.access_token = data.get("access_token", "")
            self.instance_url = data.get("instance_url", "")

    async def _get_client(self) -> httpx.AsyncClient:
        await self._authenticate()

        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def health(self) -> bool:
        """Check if Salesforce API is accessible."""
        try:
            await self._authenticate()
            return bool(self.access_token)
        except Exception as e:
            logger.error(f"Salesforce health check failed: {e}")
            return False

    async def query(self, soql: str) -> list[dict]:
        """Execute a SOQL query."""
        client = await self._get_client()

        response = await client.get(
            f"{self.instance_url}/services/data/v58.0/query",
            params={"q": soql},
        )

        if response.status_code != 200:
            raise Exception(f"Salesforce query failed: {response.text}")

        return response.json().get("records", [])

    async def get_record(
        self,
        object_type: str,
        record_id: str,
    ) -> dict:
        """Get a single record."""
        client = await self._get_client()

        response = await client.get(
            f"{self.instance_url}/services/data/v58.0/sobjects/{object_type}/{record_id}"
        )

        if response.status_code != 200:
            raise Exception(f"Salesforce get record failed: {response.text}")

        return response.json()

    async def create_record(
        self,
        object_type: str,
        data: dict,
    ) -> dict:
        """Create a new record."""
        client = await self._get_client()

        response = await client.post(
            f"{self.instance_url}/services/data/v58.0/sobjects/{object_type}",
            json=data,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Salesforce create record failed: {response.text}")

        return response.json()

    async def update_record(
        self,
        object_type: str,
        record_id: str,
        data: dict,
    ) -> bool:
        """Update a record."""
        client = await self._get_client()

        response = await client.patch(
            f"{self.instance_url}/services/data/v58.0/sobjects/{object_type}/{record_id}",
            json=data,
        )

        return response.status_code == 204

    async def delete_record(
        self,
        object_type: str,
        record_id: str,
    ) -> bool:
        """Delete a record."""
        client = await self._get_client()

        response = await client.delete(
            f"{self.instance_url}/services/data/v58.0/sobjects/{object_type}/{record_id}"
        )

        return response.status_code == 204

    async def search(self, sosl_query: str) -> list[dict]:
        """Execute a SOSL search."""
        client = await self._get_client()

        response = await client.get(
            f"{self.instance_url}/services/data/v58.0/search",
            params={"q": sosl_query},
        )

        if response.status_code != 200:
            raise Exception(f"Salesforce search failed: {response.text}")

        return response.json().get("searchRecords", [])

    async def list_objects(self) -> list[dict]:
        """List available Salesforce objects."""
        client = await self._get_client()

        response = await client.get(
            f"{self.instance_url}/services/data/v58.0/sobjects"
        )

        if response.status_code != 200:
            raise Exception(f"Salesforce list objects failed: {response.text}")

        return response.json().get("sobjects", [])

    async def create_task(
        self,
        subject: str,
        related_to: str,
        due_date: str,
    ) -> dict:
        """Create a task."""
        return await self.create_record(
            "Task",
            {
                "Subject": subject,
                "WhatId": related_to,
                "ActivityDate": due_date,
            },
        )

    async def log_activity(
        self,
        object_id: str,
        description: str,
    ) -> dict:
        """Log an activity."""
        return await self.create_record(
            "Task",
            {
                "Subject": "Activity Log",
                "WhatId": object_id,
                "Description": description,
                "Status": "Completed",
            },
        )

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read a record."""
        return await self.get_record(
            object_type=kwargs.get("object_type", "Account"),
            record_id=resource_id,
        )

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Create a record."""
        return await self.create_record(
            object_type=resource_type,
            data=data,
        )

    async def _delete_impl(self, resource_id: str, **kwargs) -> bool:
        """Delete a record."""
        return await self.delete_record(
            object_type=kwargs.get("object_type", "Account"),
            record_id=resource_id,
        )

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """Query records."""
        soql = filters.get("soql", f"SELECT Id, Name FROM {resource_type} LIMIT 100")
        return await self.query(soql)

    async def _search_impl(self, query: str, **kwargs) -> list[dict]:
        """Search records."""
        sosl = f"FIND {{{query}}} IN ALL FIELDS"
        return await self.search(sosl)
