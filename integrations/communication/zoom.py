"""ZoomConnector — Zoom video conferencing integration."""

from __future__ import annotations

import base64
import logging

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)

logger = logging.getLogger("hyperclaw.integrations.zoom")


class ZoomConnector(BaseConnector):
    """
    Zoom video conferencing connector.

    Required config:
        - account_id: Zoom account ID
        - client_id: OAuth2 client ID
        - client_secret: OAuth2 client secret
    """

    BASE_URL = "https://api.zoom.us/v2"
    OAUTH_URL = "https://zoom.us/oauth/token"

    def __init__(self, config: dict) -> None:
        self.config = config
        self.account_id = config.get("account_id", "")
        self.client_id = config.get("client_id", "")
        self.client_secret = config.get("client_secret", "")
        self._access_token: str | None = None

        if config.get("enabled", False):
            if not all([self.account_id, self.client_id, self.client_secret]):
                raise ValueError(
                    "ZoomConnector requires account_id, client_id, and client_secret"
                )

        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="zoom",
            platform="zoom",
            category="communication",
            capabilities=frozenset(
                [
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.LIST_DATA,
                    ConnectorCapability.ACTION,
                    ConnectorCapability.WEBHOOKS,
                ]
            ),
            auth_type="oauth2",
            rate_limit_per_minute=100,
        )

    async def _get_access_token(self) -> str:
        """Get OAuth2 access token using Server-to-Server OAuth."""
        if self._access_token:
            return self._access_token

        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                self.OAUTH_URL,
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "account_credentials",
                    "account_id": self.account_id,
                },
            )

            if response.status_code != 200:
                raise Exception(f"Zoom OAuth failed: {response.text}")

            data = response.json()
            self._access_token = data.get("access_token", "")
            return self._access_token

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            token = await self._get_access_token()
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def health(self) -> bool:
        """Check if Zoom API is accessible."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.BASE_URL}/users/me")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Zoom health check failed: {e}")
            return False

    async def list_meetings(self, user_id: str = "me") -> dict:
        """List meetings for a user."""
        client = await self._get_client()

        response = await client.get(f"{self.BASE_URL}/users/{user_id}/meetings")

        if response.status_code != 200:
            raise Exception(f"Zoom list_meetings failed: {response.text}")

        return response.json()

    async def create_meeting(
        self,
        topic: str,
        start_time: str,
        duration: int = 60,
        agenda: str = "",
    ) -> dict:
        """Create a meeting."""
        client = await self._get_client()

        payload = {
            "topic": topic,
            "type": 2,  # Scheduled meeting
            "start_time": start_time,
            "duration": duration,
            "timezone": "UTC",
        }
        if agenda:
            payload["agenda"] = agenda

        response = await client.post(
            f"{self.BASE_URL}/users/me/meetings",
            json=payload,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Zoom create_meeting failed: {response.text}")

        return response.json()

    async def get_meeting(self, meeting_id: str) -> dict:
        """Get meeting details."""
        client = await self._get_client()

        response = await client.get(f"{self.BASE_URL}/meetings/{meeting_id}")

        if response.status_code != 200:
            raise Exception(f"Zoom get_meeting failed: {response.text}")

        return response.json()

    async def list_recordings(self, user_id: str = "me") -> dict:
        """List recordings for a user."""
        client = await self._get_client()

        response = await client.get(f"{self.BASE_URL}/users/{user_id}/recordings")

        if response.status_code != 200:
            raise Exception(f"Zoom list_recordings failed: {response.text}")

        return response.json()

    async def get_transcript(self, meeting_id: str) -> dict:
        """Get transcript for a meeting recording."""
        client = await self._get_client()

        # First get the recording details
        response = await client.get(
            f"{self.BASE_URL}/meetings/{meeting_id}/recordings"
        )

        if response.status_code != 200:
            raise Exception(f"Zoom get_transcript failed: {response.text}")

        data = response.json()

        # Find VTT transcript file
        for file in data.get("recording_files", []):
            if file.get("file_type") == "TRANSCRIPT":
                return file

        return data

    async def list_webinars(self, user_id: str = "me") -> dict:
        """List webinars for a user."""
        client = await self._get_client()

        response = await client.get(f"{self.BASE_URL}/users/{user_id}/webinars")

        if response.status_code != 200:
            raise Exception(f"Zoom list_webinars failed: {response.text}")

        return response.json()

    async def send_chat_message(
        self,
        to_jid: str,
        message: str,
        channel_id: str | None = None,
    ) -> dict:
        """Send a chat message."""
        client = await self._get_client()

        payload = {"message": message}
        if channel_id:
            payload["to_channel"] = channel_id
        else:
            payload["to_jid"] = to_jid

        response = await client.post(
            f"{self.BASE_URL}/chat/users/me/messages",
            json=payload,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Zoom send_chat_message failed: {response.text}")

        return response.json()

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read meeting details."""
        return await self.get_meeting(resource_id)

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Create a meeting."""
        if resource_type == "meeting":
            return await self.create_meeting(
                topic=data.get("topic", "Meeting"),
                start_time=data.get("start_time"),
                duration=data.get("duration", 60),
                agenda=data.get("agenda", ""),
            )
        elif resource_type == "chat_message":
            return await self.send_chat_message(
                to_jid=data.get("to_jid", ""),
                message=data.get("message", ""),
                channel_id=data.get("channel_id"),
            )
        raise ValueError(f"Unknown resource type: {resource_type}")

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List resources."""
        user_id = filters.get("user_id", "me")
        if resource_type == "meetings":
            result = await self.list_meetings(user_id)
            return result.get("meetings", [])
        elif resource_type == "recordings":
            result = await self.list_recordings(user_id)
            return result.get("meetings", [])
        elif resource_type == "webinars":
            result = await self.list_webinars(user_id)
            return result.get("webinars", [])
        return []

    async def _action_impl(self, action_name: str, params: dict) -> dict:
        """Execute Zoom-specific actions."""
        if action_name == "create_meeting":
            return await self.create_meeting(
                topic=params.get("topic", "Meeting"),
                start_time=params.get("start_time"),
                duration=params.get("duration", 60),
                agenda=params.get("agenda", ""),
            )
        elif action_name == "send_chat":
            return await self.send_chat_message(
                to_jid=params.get("to_jid", ""),
                message=params.get("message", ""),
                channel_id=params.get("channel_id"),
            )

        raise ValueError(f"Unknown action: {action_name}")
