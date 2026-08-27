"""MicrosoftCalendarConnector — Microsoft Outlook Calendar API integration."""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)
from integrations.productivity.microsoft.base import MicrosoftOAuthBase

logger = logging.getLogger("hyperclaw.integrations.microsoft_calendar")


class MicrosoftCalendarConnector(BaseConnector, MicrosoftOAuthBase):
    """
    Microsoft Outlook Calendar connector via Graph API.

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
            connector_id="microsoft_calendar",
            platform="microsoft_calendar",
            category="productivity",
            capabilities=frozenset(
                [
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.DELETE_DATA,
                    ConnectorCapability.CALENDAR,
                ]
            ),
            auth_type="oauth2",
            rate_limit_per_minute=100,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        return await self._get_microsoft_client()

    async def health(self) -> bool:
        """Check if Calendar API is accessible."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.GRAPH_URL}/me/calendars")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Microsoft Calendar health check failed: {e}")
            return False

    async def list_events(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        max_results: int = 50,
    ) -> list[dict]:
        """List calendar events."""
        client = await self._get_client()

        params = {"$top": max_results, "$orderby": "start/dateTime"}

        filter_parts = []
        if start:
            filter_parts.append(f"start/dateTime ge '{start.isoformat()}'")
        if end:
            filter_parts.append(f"end/dateTime le '{end.isoformat()}'")

        if filter_parts:
            params["$filter"] = " and ".join(filter_parts)

        response = await client.get(
            f"{self.GRAPH_URL}/me/events",
            params=params,
        )

        if response.status_code != 200:
            raise Exception(f"Calendar list events failed: {response.text}")

        return response.json().get("value", [])

    async def create_event(
        self,
        title: str,
        start: datetime,
        end: datetime,
        attendees: list[str] | None = None,
        description: str = "",
        location: str = "",
    ) -> dict:
        """Create a calendar event."""
        client = await self._get_client()

        event = {
            "subject": title,
            "start": {"dateTime": start.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": end.isoformat(), "timeZone": "UTC"},
        }

        if description:
            event["body"] = {"contentType": "Text", "content": description}
        if location:
            event["location"] = {"displayName": location}
        if attendees:
            event["attendees"] = [
                {"emailAddress": {"address": email}, "type": "required"}
                for email in attendees
            ]

        response = await client.post(
            f"{self.GRAPH_URL}/me/events",
            json=event,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Calendar create event failed: {response.text}")

        return response.json()

    async def update_event(
        self,
        event_id: str,
        updates: dict,
    ) -> dict:
        """Update a calendar event."""
        client = await self._get_client()

        response = await client.patch(
            f"{self.GRAPH_URL}/me/events/{event_id}",
            json=updates,
        )

        if response.status_code != 200:
            raise Exception(f"Calendar update event failed: {response.text}")

        return response.json()

    async def delete_event(self, event_id: str) -> bool:
        """Delete a calendar event."""
        client = await self._get_client()

        response = await client.delete(f"{self.GRAPH_URL}/me/events/{event_id}")

        return response.status_code == 204

    async def get_free_busy(
        self,
        attendees: list[str],
        start: datetime,
        end: datetime,
    ) -> dict:
        """Get free/busy times for attendees."""
        client = await self._get_client()

        body = {
            "schedules": attendees,
            "startTime": {"dateTime": start.isoformat(), "timeZone": "UTC"},
            "endTime": {"dateTime": end.isoformat(), "timeZone": "UTC"},
        }

        response = await client.post(
            f"{self.GRAPH_URL}/me/calendar/getSchedule",
            json=body,
        )

        if response.status_code != 200:
            raise Exception(f"Calendar get free/busy failed: {response.text}")

        return response.json()

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read an event."""
        client = await self._get_client()

        response = await client.get(f"{self.GRAPH_URL}/me/events/{resource_id}")

        if response.status_code != 200:
            raise Exception(f"Calendar read failed: {response.text}")

        return response.json()

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Create an event."""
        return await self.create_event(
            title=data.get("title", "Untitled"),
            start=data.get("start"),
            end=data.get("end"),
            attendees=data.get("attendees"),
            description=data.get("description", ""),
            location=data.get("location", ""),
        )

    async def _delete_impl(self, resource_id: str, **kwargs) -> bool:
        """Delete an event."""
        return await self.delete_event(resource_id)
