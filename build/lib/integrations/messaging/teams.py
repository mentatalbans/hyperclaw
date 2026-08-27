"""TeamsConnector — Microsoft Teams integration."""

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

logger = logging.getLogger("hyperclaw.integrations.teams")


class TeamsConnector(BaseConnector):
    """
    Microsoft Teams connector.

    Supports two modes:
    1. Webhook mode: Simple outbound only via webhook_url
    2. Bot mode: Full bi-directional via app_id + app_password

    Required config (one of):
        - webhook_url: Teams Incoming Webhook URL (simple mode)
        - app_id + app_password: Bot Framework credentials (full mode)
    """

    BOT_FRAMEWORK_URL = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"

    def __init__(self, config: dict) -> None:
        self.config = config
        self.webhook_url = config.get("webhook_url", "")
        self.app_id = config.get("app_id", "")
        self.app_password = config.get("app_password", "")

        if config.get("enabled", False):
            if not self.webhook_url and not (self.app_id and self.app_password):
                raise ValueError(
                    "TeamsConnector requires webhook_url OR (app_id + app_password)"
                )

        self._client: httpx.AsyncClient | None = None
        self._access_token: str | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="teams",
            platform="teams",
            category="messaging",
            capabilities=frozenset(
                [
                    ConnectorCapability.SEND_MESSAGE,
                    ConnectorCapability.RECEIVE_MESSAGE,
                    ConnectorCapability.FILE_UPLOAD,
                    ConnectorCapability.WEBHOOKS,
                    ConnectorCapability.ACTION,
                ]
            ),
            auth_type="oauth2" if self.app_id else "webhook_token",
            rate_limit_per_minute=60,
            supports_threads=True,
            supports_attachments=True,
            supports_reactions=True,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def health(self) -> bool:
        """Check if connector is configured."""
        # Webhook mode: just check URL is set
        if self.webhook_url:
            return bool(self.webhook_url)

        # Bot mode: try to get access token
        try:
            await self._get_bot_token()
            return True
        except Exception as e:
            logger.error(f"Teams health check failed: {e}")
            return False

    async def _get_bot_token(self) -> str:
        """Get Bot Framework access token."""
        if self._access_token:
            return self._access_token

        client = await self._get_client()

        response = await client.post(
            self.BOT_FRAMEWORK_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self.app_id,
                "client_secret": self.app_password,
                "scope": "https://api.botframework.com/.default",
            },
        )

        if response.status_code != 200:
            raise Exception(f"Failed to get Teams bot token: {response.text}")

        data = response.json()
        self._access_token = data.get("access_token", "")
        return self._access_token

    async def _send_impl(self, message: OutboundMessage) -> str:
        """Send a message via Teams."""
        if self.webhook_url:
            return await self._send_via_webhook(message)
        else:
            return await self._send_via_bot(message)

    async def _send_via_webhook(self, message: OutboundMessage) -> str:
        """Send via incoming webhook."""
        client = await self._get_client()

        payload = {"text": message.content}

        response = await client.post(self.webhook_url, json=payload)

        if response.status_code not in (200, 201):
            raise Exception(f"Teams webhook send failed: {response.text}")

        return "webhook_sent"

    async def _send_via_bot(self, message: OutboundMessage) -> str:
        """Send via Bot Framework."""
        client = await self._get_client()
        token = await self._get_bot_token()

        # Bot Framework requires conversation reference
        # This is a simplified implementation
        service_url = self.config.get("service_url", "https://smba.trafficmanager.net/")
        conversation_id = message.recipient_id

        payload = {
            "type": "message",
            "text": message.content,
        }

        response = await client.post(
            f"{service_url}v3/conversations/{conversation_id}/activities",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Teams bot send failed: {response.text}")

        data = response.json()
        return data.get("id", "")

    async def _receive_impl(self) -> AsyncIterator[InboundMessage]:
        """
        Receive messages. Teams uses Bot Framework webhooks.
        """
        return
        yield

    async def send_adaptive_card(
        self,
        webhook_url: str | None,
        card: dict,
    ) -> str:
        """Send an Adaptive Card."""
        client = await self._get_client()

        url = webhook_url or self.webhook_url
        if not url:
            raise ValueError("No webhook URL provided")

        payload = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": card,
                }
            ],
        }

        response = await client.post(url, json=payload)

        if response.status_code not in (200, 201):
            raise Exception(f"Teams adaptive card send failed: {response.text}")

        return "card_sent"

    async def _action_impl(self, action_name: str, params: dict) -> dict:
        """Execute Teams-specific actions."""
        if action_name == "send_adaptive_card":
            result = await self.send_adaptive_card(
                params.get("webhook_url"),
                params["card"],
            )
            return {"status": "sent", "id": result}

        raise ValueError(f"Unknown action: {action_name}")
