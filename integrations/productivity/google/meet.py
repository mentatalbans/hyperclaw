"""GoogleMeetConnector — Google Meet API integration."""

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

logger = logging.getLogger("hyperclaw.integrations.google_meet")


class GoogleMeetConnector(BaseConnector, GoogleOAuthBase):
    """
    Google Meet API connector.

    Uses Google Calendar API to create Meet events.

    Required config (via GoogleOAuthBase):
        - access_token or credentials_file
    """

    CALENDAR_URL = "https://www.googleapis.com/calendar/v3"

    def __init__(self, config: dict) -> None:
        self.config = config
        self._init_google_auth(config)
        self._validate_google_config(config)

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="google_meet",
            platform="google_meet",
            category="productivity",
            capabilities=frozenset(
                [
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.ACTION,
                ]
            ),
            auth_type="oauth2",
            rate_limit_per_minute=60,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        return await self._get_google_client()

    async def health(self) -> bool:
        """Check if Meet/Calendar API is accessible."""
        try:
            client = await self._get_client()
            response = await client.get(
                f"{self.CALENDAR_URL}/users/me/calendarList",
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Google Meet health check failed: {e}")
            return False

    async def create_meeting(
        self,
        title: str,
        start: datetime,
        end: datetime,
        attendees: list[str] | None = None,
    ) -> dict:
        """Create a Google Meet meeting via Calendar."""
        client = await self._get_client()

        event = {
            "summary": title,
            "start": {"dateTime": start.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": end.isoformat(), "timeZone": "UTC"},
            "conferenceData": {
                "createRequest": {
                    "requestId": f"meet-{datetime.utcnow().timestamp()}",
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            },
        }

        if attendees:
            event["attendees"] = [{"email": email} for email in attendees]

        response = await client.post(
            f"{self.CALENDAR_URL}/calendars/primary/events",
            params={"conferenceDataVersion": 1},
            json=event,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Meet create meeting failed: {response.text}")

        data = response.json()

        # Extract Meet link
        conference_data = data.get("conferenceData", {})
        entry_points = conference_data.get("entryPoints", [])
        meet_link = next(
            (ep.get("uri") for ep in entry_points if ep.get("entryPointType") == "video"),
            None,
        )

        return {
            "event_id": data.get("id"),
            "meet_link": meet_link,
            "calendar_link": data.get("htmlLink"),
            "start": data.get("start"),
            "end": data.get("end"),
        }

    async def get_meeting_transcript(self, meeting_id: str) -> dict:
        """
        Get meeting transcript.
        Note: This requires Meet transcription to be enabled and
        uses the Google Cloud Speech-to-Text API or Meet add-ons.
        This is a placeholder for the interface.
        """
        # Meet transcripts are stored in Drive or via external services
        # This would require additional API integration
        return {
            "meeting_id": meeting_id,
            "transcript": None,
            "status": "Transcript retrieval requires additional configuration",
        }

    async def list_upcoming_meetings(
        self,
        max_results: int = 10,
    ) -> list[dict]:
        """List upcoming meetings with Meet links."""
        client = await self._get_client()

        now = datetime.utcnow().isoformat() + "Z"

        response = await client.get(
            f"{self.CALENDAR_URL}/calendars/primary/events",
            params={
                "timeMin": now,
                "maxResults": max_results,
                "singleEvents": True,
                "orderBy": "startTime",
            },
        )

        if response.status_code != 200:
            raise Exception(f"Meet list meetings failed: {response.text}")

        events = response.json().get("items", [])

        # Filter to only events with Meet links
        meetings = []
        for event in events:
            conference_data = event.get("conferenceData", {})
            entry_points = conference_data.get("entryPoints", [])
            meet_link = next(
                (ep.get("uri") for ep in entry_points if ep.get("entryPointType") == "video"),
                None,
            )

            if meet_link:
                meetings.append({
                    "event_id": event.get("id"),
                    "title": event.get("summary"),
                    "meet_link": meet_link,
                    "start": event.get("start"),
                    "end": event.get("end"),
                    "attendees": event.get("attendees", []),
                })

        return meetings

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read a meeting event."""
        client = await self._get_client()

        response = await client.get(
            f"{self.CALENDAR_URL}/calendars/primary/events/{resource_id}"
        )

        if response.status_code != 200:
            raise Exception(f"Meet read failed: {response.text}")

        return response.json()

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Create a meeting."""
        return await self.create_meeting(
            title=data.get("title", "Meeting"),
            start=data.get("start"),
            end=data.get("end"),
            attendees=data.get("attendees"),
        )

    async def _action_impl(self, action_name: str, params: dict) -> dict:
        """Execute Meet-specific actions."""
        if action_name == "create_meeting":
            return await self.create_meeting(
                title=params.get("title", "Meeting"),
                start=params["start"],
                end=params["end"],
                attendees=params.get("attendees"),
            )
        elif action_name == "get_transcript":
            return await self.get_meeting_transcript(params["meeting_id"])
        elif action_name == "list_upcoming":
            return {"meetings": await self.list_upcoming_meetings(params.get("max_results", 10))}

        raise ValueError(f"Unknown action: {action_name}")
