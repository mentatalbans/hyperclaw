"""HubSpotConnector — HubSpot API integration."""

from __future__ import annotations

import logging

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)

logger = logging.getLogger("hyperclaw.integrations.hubspot")


class HubSpotConnector(BaseConnector):
    """
    HubSpot API connector.

    Required config:
        - api_key: HubSpot API key or Private App access token
    """

    BASE_URL = "https://api.hubapi.com"

    def __init__(self, config: dict) -> None:
        self.config = config
        self.api_key = config.get("api_key", "")

        if config.get("enabled", False) and not self.api_key:
            raise ValueError("HubSpotConnector requires api_key")

        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="hubspot",
            platform="hubspot",
            category="enterprise",
            capabilities=frozenset(
                [
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.LIST_DATA,
                    ConnectorCapability.SEARCH,
                    ConnectorCapability.WEBHOOKS,
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
        """Check if HubSpot API is accessible."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.BASE_URL}/crm/v3/objects/contacts?limit=1")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"HubSpot health check failed: {e}")
            return False

    async def list_contacts(self, limit: int = 100) -> list[dict]:
        """List contacts."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/crm/v3/objects/contacts",
            params={"limit": limit},
        )

        if response.status_code != 200:
            raise Exception(f"HubSpot list contacts failed: {response.text}")

        return response.json().get("results", [])

    async def get_contact(self, contact_id: str) -> dict:
        """Get a contact by ID."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/crm/v3/objects/contacts/{contact_id}"
        )

        if response.status_code != 200:
            raise Exception(f"HubSpot get contact failed: {response.text}")

        return response.json()

    async def create_contact(self, properties: dict) -> dict:
        """Create a new contact."""
        client = await self._get_client()

        response = await client.post(
            f"{self.BASE_URL}/crm/v3/objects/contacts",
            json={"properties": properties},
        )

        if response.status_code not in (200, 201):
            raise Exception(f"HubSpot create contact failed: {response.text}")

        return response.json()

    async def update_contact(
        self,
        contact_id: str,
        properties: dict,
    ) -> dict:
        """Update a contact."""
        client = await self._get_client()

        response = await client.patch(
            f"{self.BASE_URL}/crm/v3/objects/contacts/{contact_id}",
            json={"properties": properties},
        )

        if response.status_code != 200:
            raise Exception(f"HubSpot update contact failed: {response.text}")

        return response.json()

    async def list_deals(self, limit: int = 100) -> list[dict]:
        """List deals."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/crm/v3/objects/deals",
            params={"limit": limit},
        )

        if response.status_code != 200:
            raise Exception(f"HubSpot list deals failed: {response.text}")

        return response.json().get("results", [])

    async def create_deal(self, properties: dict) -> dict:
        """Create a new deal."""
        client = await self._get_client()

        response = await client.post(
            f"{self.BASE_URL}/crm/v3/objects/deals",
            json={"properties": properties},
        )

        if response.status_code not in (200, 201):
            raise Exception(f"HubSpot create deal failed: {response.text}")

        return response.json()

    async def update_deal_stage(
        self,
        deal_id: str,
        stage: str,
    ) -> dict:
        """Update deal stage."""
        client = await self._get_client()

        response = await client.patch(
            f"{self.BASE_URL}/crm/v3/objects/deals/{deal_id}",
            json={"properties": {"dealstage": stage}},
        )

        if response.status_code != 200:
            raise Exception(f"HubSpot update deal stage failed: {response.text}")

        return response.json()

    async def log_email(
        self,
        contact_id: str,
        subject: str,
        body: str,
    ) -> dict:
        """Log an email engagement."""
        client = await self._get_client()

        response = await client.post(
            f"{self.BASE_URL}/crm/v3/objects/emails",
            json={
                "properties": {
                    "hs_email_subject": subject,
                    "hs_email_text": body,
                    "hs_email_direction": "OUTGOING",
                },
                "associations": [
                    {
                        "to": {"id": contact_id},
                        "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 9}],
                    }
                ],
            },
        )

        if response.status_code not in (200, 201):
            raise Exception(f"HubSpot log email failed: {response.text}")

        return response.json()

    async def create_note(
        self,
        contact_id: str,
        body: str,
    ) -> dict:
        """Create a note on a contact."""
        client = await self._get_client()

        response = await client.post(
            f"{self.BASE_URL}/crm/v3/objects/notes",
            json={
                "properties": {"hs_note_body": body},
                "associations": [
                    {
                        "to": {"id": contact_id},
                        "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 10}],
                    }
                ],
            },
        )

        if response.status_code not in (200, 201):
            raise Exception(f"HubSpot create note failed: {response.text}")

        return response.json()

    async def list_companies(self, limit: int = 100) -> list[dict]:
        """List companies."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/crm/v3/objects/companies",
            params={"limit": limit},
        )

        if response.status_code != 200:
            raise Exception(f"HubSpot list companies failed: {response.text}")

        return response.json().get("results", [])

    async def search_contacts(self, query: str) -> list[dict]:
        """Search contacts."""
        client = await self._get_client()

        response = await client.post(
            f"{self.BASE_URL}/crm/v3/objects/contacts/search",
            json={
                "query": query,
                "limit": 50,
            },
        )

        if response.status_code != 200:
            raise Exception(f"HubSpot search failed: {response.text}")

        return response.json().get("results", [])

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read a contact."""
        return await self.get_contact(resource_id)

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Create a contact or deal."""
        if resource_type == "deal":
            return await self.create_deal(data)
        return await self.create_contact(data)

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List contacts, deals, or companies."""
        limit = filters.get("limit", 100)
        if resource_type == "deals":
            return await self.list_deals(limit)
        elif resource_type == "companies":
            return await self.list_companies(limit)
        return await self.list_contacts(limit)

    async def _search_impl(self, query: str, **kwargs) -> list[dict]:
        """Search contacts."""
        return await self.search_contacts(query)
