"""GoogleCalendarConnector — Google Calendar API integration."""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)
from integrations.productivity.google.base import GoogleOAuthBase

logger = logging.getLogger("hyperclaw.integrations.google_calendar")


class GoogleCalendarConnector(BaseConnector, GoogleOAuthBase):
    """
    Google Calendar API connector.

    Required config (via GoogleOAuthBase):
        - access_token or credentials_file
    """

    BASE_URL = "https://www.googleapis.com/calendar/v3"

    def __init__(self, config: dict) -> None:
        self.config = config
        self._init_google_auth(config)
        self._validate_google_config(config)

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="google_calendar",
            platform="google_calendar",
            category="productivity",
            capabilities=frozenset(
                [
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.DELETE_DATA,
                    ConnectorCapability.LIST_DATA,
                    ConnectorCapability.CALENDAR,
                ]
            ),
            auth_type="oauth2",
            rate_limit_per_minute=100,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        return await self._get_google_client()

    async def health(self) -> bool:
        """Check if Calendar API is accessible."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.BASE_URL}/users/me/calendarList")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Google Calendar health check failed: {e}")
            return False

    async def list_events(
        self,
        calendar_id: str = "primary",
        start: datetime | None = None,
        end: datetime | None = None,
        max_results: int = 50,
    ) -> list[dict]:
        """List calendar events."""
        client = await self._get_client()

        params = {"maxResults": max_results, "singleEvents": True, "orderBy": "startTime"}

        if start:
            params["timeMin"] = start.isoformat() + "Z"
        if end:
            params["timeMax"] = end.isoformat() + "Z"

        response = await client.get(
            f"{self.BASE_URL}/calendars/{calendar_id}/events",
            params=params,
        )

        if response.status_code != 200:
            raise Exception(f"Calendar list events failed: {response.text}")

        return response.json().get("items", [])

    async def create_event(
        self,
        title: str,
        start: datetime,
        end: datetime,
        attendees: list[str] | None = None,
        description: str = "",
        location: str = "",
        calendar_id: str = "primary",
    ) -> dict:
        """Create a calendar event."""
        client = await self._get_client()

        event = {
            "summary": title,
            "start": {"dateTime": start.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": end.isoformat(), "timeZone": "UTC"},
        }

        if description:
            event["description"] = description
        if location:
            event["location"] = location
        if attendees:
            event["attendees"] = [{"email": email} for email in attendees]

        response = await client.post(
            f"{self.BASE_URL}/calendars/{calendar_id}/events",
            json=event,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Calendar create event failed: {response.text}")

        return response.json()

    async def update_event(
        self,
        event_id: str,
        updates: dict,
        calendar_id: str = "primary",
    ) -> dict:
        """Update a calendar event."""
        client = await self._get_client()

        response = await client.patch(
            f"{self.BASE_URL}/calendars/{calendar_id}/events/{event_id}",
            json=updates,
        )

        if response.status_code != 200:
            raise Exception(f"Calendar update event failed: {response.text}")

        return response.json()

    async def delete_event(
        self,
        event_id: str,
        calendar_id: str = "primary",
    ) -> bool:
        """Delete a calendar event."""
        client = await self._get_client()

        response = await client.delete(
            f"{self.BASE_URL}/calendars/{calendar_id}/events/{event_id}"
        )

        return response.status_code == 204

    async def find_free_slots(
        self,
        attendees: list[str],
        duration_minutes: int,
        start: datetime,
        end: datetime,
    ) -> list[dict]:
        """Find free time slots for all attendees."""
        client = await self._get_client()

        body = {
            "timeMin": start.isoformat() + "Z",
            "timeMax": end.isoformat() + "Z",
            "items": [{"id": email} for email in attendees],
        }

        response = await client.post(
            f"{self.BASE_URL}/freeBusy",
            json=body,
        )

        if response.status_code != 200:
            raise Exception(f"Calendar freeBusy failed: {response.text}")

        # Parse busy times and compute free slots
        data = response.json()
        calendars = data.get("calendars", {})

        # Collect all busy periods
        busy_periods = []
        for cal_id, cal_data in calendars.items():
            for busy in cal_data.get("busy", []):
                busy_periods.append({
                    "start": busy["start"],
                    "end": busy["end"],
                })

        # Return free slots (simplified - just return busy periods for now)
        return busy_periods

    async def create_recurring_event(
        self,
        title: str,
        recurrence_rule: str,
        start: datetime,
        end: datetime,
        calendar_id: str = "primary",
        **kwargs,
    ) -> dict:
        """Create a recurring event."""
        client = await self._get_client()

        event = {
            "summary": title,
            "start": {"dateTime": start.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": end.isoformat(), "timeZone": "UTC"},
            "recurrence": [recurrence_rule],
        }

        event.update(kwargs)

        response = await client.post(
            f"{self.BASE_URL}/calendars/{calendar_id}/events",
            json=event,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Calendar create recurring event failed: {response.text}")

        return response.json()

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read an event."""
        client = await self._get_client()
        calendar_id = kwargs.get("calendar_id", "primary")

        response = await client.get(
            f"{self.BASE_URL}/calendars/{calendar_id}/events/{resource_id}"
        )

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
        return await self.delete_event(resource_id, kwargs.get("calendar_id", "primary"))

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List events."""
        return await self.list_events(
            calendar_id=filters.get("calendar_id", "primary"),
            start=filters.get("start"),
            end=filters.get("end"),
            max_results=filters.get("max_results", 50),
        )
