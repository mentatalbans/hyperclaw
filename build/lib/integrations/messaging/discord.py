"""DiscordConnector — Discord Bot API integration."""

from __future__ import annotations

import logging
from typing import AsyncIterator

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
    InboundMessage,
    OutboundMessage,
)

logger = logging.getLogger("hyperclaw.integrations.discord")


class DiscordConnector(BaseConnector):
    """
    Discord Bot API connector.

    Required config:
        - bot_token: Discord Bot token
    """

    BASE_URL = "https://discord.com/api/v10"

    def __init__(self, config: dict) -> None:
        self.config = config
        self.bot_token = config.get("bot_token", "")

        if config.get("enabled", False) and not self.bot_token:
            raise ValueError("DiscordConnector requires bot_token")

        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="discord",
            platform="discord",
            category="messaging",
            capabilities=frozenset(
                [
                    ConnectorCapability.SEND_MESSAGE,
                    ConnectorCapability.RECEIVE_MESSAGE,
                    ConnectorCapability.FILE_UPLOAD,
                    ConnectorCapability.WEBHOOKS,
                    ConnectorCapability.NOTIFY,
                ]
            ),
            auth_type="bearer",
            rate_limit_per_minute=50,
            supports_threads=True,
            supports_attachments=True,
            supports_reactions=True,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={
                    "Authorization": f"Bot {self.bot_token}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def health(self) -> bool:
        """Check if bot token is valid."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.BASE_URL}/users/@me")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Discord health check failed: {e}")
            return False

    async def _send_impl(self, message: OutboundMessage) -> str:
        """Send a message to a Discord channel."""
        client = await self._get_client()

        # Use channel_id if provided, otherwise fall back to recipient_id
        channel_id = message.channel_id or message.recipient_id

        payload = {"content": message.content}

        if message.thread_id:
            # Send to thread
            channel_id = message.thread_id

        response = await client.post(
            f"{self.BASE_URL}/channels/{channel_id}/messages",
            json=payload,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Discord send failed: {response.text}")

        data = response.json()
        return data.get("id", "")

    async def _receive_impl(self) -> AsyncIterator[InboundMessage]:
        """
        Receive messages. In production, use Discord Gateway with websockets.
        This is a stub for the interface.
        """
        # Discord requires websocket connection for real-time messages.
        # For production, implement Discord Gateway protocol.
        return
        yield

    async def send_embed(
        self,
        channel_id: str,
        embed: dict,
        content: str = "",
    ) -> str:
        """Send a rich embed message."""
        client = await self._get_client()

        payload = {"embeds": [embed]}
        if content:
            payload["content"] = content

        response = await client.post(
            f"{self.BASE_URL}/channels/{channel_id}/messages",
            json=payload,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Discord send_embed failed: {response.text}")

        data = response.json()
        return data.get("id", "")

    async def add_reaction(
        self,
        channel_id: str,
        message_id: str,
        emoji: str,
    ) -> bool:
        """Add a reaction to a message."""
        client = await self._get_client()

        # URL encode the emoji
        import urllib.parse

        encoded_emoji = urllib.parse.quote(emoji)

        response = await client.put(
            f"{self.BASE_URL}/channels/{channel_id}/messages/{message_id}/reactions/{encoded_emoji}/@me",
        )

        return response.status_code == 204
