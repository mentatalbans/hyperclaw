"""WhatsAppConnector — Meta WhatsApp Business API integration."""

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

logger = logging.getLogger("hyperclaw.integrations.whatsapp")


class WhatsAppConnector(BaseConnector):
    """
    WhatsApp Business API connector via Meta Graph API.

    Required config:
        - access_token: Meta Graph API access token
        - phone_number_id: WhatsApp Business phone number ID

    Note: This connector handles health data and sets hypershield_context accordingly.
    """

    BASE_URL = "https://graph.facebook.com/v17.0"

    # HyperShield context for health data protection
    hypershield_context = "health_data"

    def __init__(self, config: dict) -> None:
        self.config = config
        self.access_token = config.get("access_token", "")
        self.phone_number_id = config.get("phone_number_id", "")

        if config.get("enabled", False):
            if not self.access_token:
                raise ValueError("WhatsAppConnector requires access_token")
            if not self.phone_number_id:
                raise ValueError("WhatsAppConnector requires phone_number_id")

        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="whatsapp",
            platform="whatsapp",
            category="messaging",
            capabilities=frozenset(
                [
                    ConnectorCapability.SEND_MESSAGE,
                    ConnectorCapability.RECEIVE_MESSAGE,
                    ConnectorCapability.FILE_UPLOAD,
                    ConnectorCapability.FILE_DOWNLOAD,
                    ConnectorCapability.WEBHOOKS,
                ]
            ),
            auth_type="bearer",
            rate_limit_per_minute=80,
            supports_threads=False,
            supports_attachments=True,
            supports_reactions=True,
        )

    async def _get_client(self) -> httpx.AsyncClient:
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
        """Check if credentials are valid."""
        try:
            client = await self._get_client()
            response = await client.get(
                f"{self.BASE_URL}/{self.phone_number_id}",
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"WhatsApp health check failed: {e}")
            return False

    async def _send_impl(self, message: OutboundMessage) -> str:
        """Send a text message via WhatsApp."""
        client = await self._get_client()

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": message.recipient_id,
            "type": "text",
            "text": {"body": message.content},
        }

        response = await client.post(
            f"{self.BASE_URL}/{self.phone_number_id}/messages",
            json=payload,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"WhatsApp send failed: {response.text}")

        data = response.json()
        messages = data.get("messages", [])
        return messages[0].get("id", "") if messages else ""

    async def _receive_impl(self) -> AsyncIterator[InboundMessage]:
        """
        Receive messages. WhatsApp uses webhooks for incoming messages.
        This is a stub; implement webhook handler separately.
        """
        return
        yield

    async def send_template(
        self,
        to: str,
        template_name: str,
        language_code: str = "en",
        components: list[dict] | None = None,
    ) -> str:
        """Send a pre-approved message template."""
        client = await self._get_client()

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
            },
        }

        if components:
            payload["template"]["components"] = components

        response = await client.post(
            f"{self.BASE_URL}/{self.phone_number_id}/messages",
            json=payload,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"WhatsApp send_template failed: {response.text}")

        data = response.json()
        messages = data.get("messages", [])
        return messages[0].get("id", "") if messages else ""

    async def send_media(
        self,
        to: str,
        media_type: str,
        url: str,
        caption: str = "",
    ) -> str:
        """
        Send media (image, video, audio, document).

        Args:
            to: Recipient phone number
            media_type: One of "image", "video", "audio", "document"
            url: Public URL of the media
            caption: Optional caption (for image/video/document)
        """
        client = await self._get_client()

        media_object = {"link": url}
        if caption and media_type in ("image", "video", "document"):
            media_object["caption"] = caption

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": media_type,
            media_type: media_object,
        }

        response = await client.post(
            f"{self.BASE_URL}/{self.phone_number_id}/messages",
            json=payload,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"WhatsApp send_media failed: {response.text}")

        data = response.json()
        messages = data.get("messages", [])
        return messages[0].get("id", "") if messages else ""
