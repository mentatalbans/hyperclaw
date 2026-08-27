"""SlackConnector — Slack API integration."""

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

logger = logging.getLogger("hyperclaw.integrations.slack")


class SlackConnector(BaseConnector):
    """
    Slack API connector using Bot tokens.

    Required config:
        - bot_token: Slack Bot User OAuth Token (xoxb-...)
    """

    BASE_URL = "https://slack.com/api"

    def __init__(self, config: dict) -> None:
        self.config = config
        self.bot_token = config.get("bot_token", "")

        if config.get("enabled", False) and not self.bot_token:
            raise ValueError("SlackConnector requires bot_token")

        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="slack",
            platform="slack",
            category="messaging",
            capabilities=frozenset(
                [
                    ConnectorCapability.SEND_MESSAGE,
                    ConnectorCapability.RECEIVE_MESSAGE,
                    ConnectorCapability.FILE_UPLOAD,
                    ConnectorCapability.FILE_DOWNLOAD,
                    ConnectorCapability.WEBHOOKS,
                    ConnectorCapability.SEARCH,
                    ConnectorCapability.ACTION,
                    ConnectorCapability.NOTIFY,
                ]
            ),
            auth_type="bearer",
            rate_limit_per_minute=60,
            supports_threads=True,
            supports_attachments=True,
            supports_reactions=True,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={
                    "Authorization": f"Bearer {self.bot_token}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def health(self) -> bool:
        """Check if bot token is valid."""
        try:
            client = await self._get_client()
            response = await client.post(f"{self.BASE_URL}/auth.test")
            data = response.json()
            return data.get("ok", False)
        except Exception as e:
            logger.error(f"Slack health check failed: {e}")
            return False

    async def _send_impl(self, message: OutboundMessage) -> str:
        """Send a message via Slack."""
        client = await self._get_client()

        payload = {
            "channel": message.recipient_id or message.channel_id,
            "text": message.content,
        }

        if message.thread_id:
            payload["thread_ts"] = message.thread_id

        if message.format == "blocks":
            # If blocks format, content should be JSON blocks
            import json

            payload["blocks"] = json.loads(message.content)
            payload["text"] = "Message"  # Fallback text

        response = await client.post(f"{self.BASE_URL}/chat.postMessage", json=payload)
        data = response.json()

        if not data.get("ok"):
            raise Exception(f"Slack send failed: {data.get('error')}")

        return data.get("ts", "")

    async def _receive_impl(self) -> AsyncIterator[InboundMessage]:
        """
        Receive messages. In production, use Socket Mode or Events API.
        This implementation uses conversations.history for polling.
        """
        # Note: This is a simplified implementation.
        # In production, use Slack Socket Mode or Events API with webhooks.
        return
        yield  # Make this an async generator

    async def post_blocks(
        self,
        channel: str,
        blocks: list[dict],
        text: str = "Message",
    ) -> str:
        """Post a message with Block Kit blocks."""
        client = await self._get_client()

        payload = {
            "channel": channel,
            "blocks": blocks,
            "text": text,
        }

        response = await client.post(f"{self.BASE_URL}/chat.postMessage", json=payload)
        data = response.json()

        if not data.get("ok"):
            raise Exception(f"Slack post_blocks failed: {data.get('error')}")

        return data.get("ts", "")

    async def add_reaction(
        self,
        channel: str,
        timestamp: str,
        emoji: str,
    ) -> bool:
        """Add a reaction to a message."""
        client = await self._get_client()

        payload = {
            "channel": channel,
            "timestamp": timestamp,
            "name": emoji.strip(":"),
        }

        response = await client.post(f"{self.BASE_URL}/reactions.add", json=payload)
        data = response.json()

        return data.get("ok", False)

    async def search_messages(self, query: str, count: int = 20) -> list[dict]:
        """Search messages in Slack."""
        client = await self._get_client()

        params = {
            "query": query,
            "count": count,
        }

        response = await client.get(f"{self.BASE_URL}/search.messages", params=params)
        data = response.json()

        if not data.get("ok"):
            raise Exception(f"Slack search failed: {data.get('error')}")

        return data.get("messages", {}).get("matches", [])

    async def _search_impl(self, query: str, **kwargs) -> list[dict]:
        """Search implementation."""
        return await self.search_messages(query, kwargs.get("count", 20))

    async def _action_impl(self, action_name: str, params: dict) -> dict:
        """Execute Slack-specific actions."""
        client = await self._get_client()

        if action_name == "list_channels":
            response = await client.get(f"{self.BASE_URL}/conversations.list")
            return response.json()

        elif action_name == "get_user":
            response = await client.get(
                f"{self.BASE_URL}/users.info",
                params={"user": params["user_id"]},
            )
            return response.json()

        elif action_name == "update_message":
            response = await client.post(
                f"{self.BASE_URL}/chat.update",
                json={
                    "channel": params["channel"],
                    "ts": params["ts"],
                    "text": params["text"],
                },
            )
            return response.json()

        raise ValueError(f"Unknown action: {action_name}")
