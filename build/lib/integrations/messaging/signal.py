"""SignalConnector — Signal Messenger integration via signal-cli REST API."""

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

logger = logging.getLogger("hyperclaw.integrations.signal")


class SignalConnector(BaseConnector):
    """
    Signal Messenger connector via signal-cli REST API.

    Required config:
        - api_url: URL of signal-cli REST API (e.g., http://localhost:8080)
        - phone_number: Registered Signal phone number

    Note: This connector uses isolated HyperShield context for maximum privacy.
    """

    # HyperShield context for privacy isolation
    hypershield_context = "isolated"

    def __init__(self, config: dict) -> None:
        self.config = config
        self.api_url = config.get("api_url", "").rstrip("/")
        self.phone_number = config.get("phone_number", "")

        if config.get("enabled", False):
            if not self.api_url:
                raise ValueError("SignalConnector requires api_url")
            if not self.phone_number:
                raise ValueError("SignalConnector requires phone_number")

        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="signal",
            platform="signal",
            category="messaging",
            capabilities=frozenset(
                [
                    ConnectorCapability.SEND_MESSAGE,
                    ConnectorCapability.RECEIVE_MESSAGE,
                    ConnectorCapability.FILE_UPLOAD,
                ]
            ),
            auth_type="api_key",
            rate_limit_per_minute=30,
            supports_threads=False,
            supports_attachments=True,
            supports_reactions=True,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={"Content-Type": "application/json"},
            )
        return self._client

    async def health(self) -> bool:
        """Check if signal-cli API is reachable."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.api_url}/v1/about")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Signal health check failed: {e}")
            return False

    async def _send_impl(self, message: OutboundMessage) -> str:
        """Send a message via Signal."""
        client = await self._get_client()

        payload = {
            "number": self.phone_number,
            "recipients": [message.recipient_id],
            "message": message.content,
        }

        response = await client.post(
            f"{self.api_url}/v2/send",
            json=payload,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Signal send failed: {response.text}")

        data = response.json()
        # signal-cli returns timestamps as message IDs
        return str(data.get("timestamp", ""))

    async def _receive_impl(self) -> AsyncIterator[InboundMessage]:
        """Receive messages from Signal."""
        client = await self._get_client()

        response = await client.get(
            f"{self.api_url}/v1/receive/{self.phone_number}",
        )

        if response.status_code != 200:
            return

        data = response.json()

        for envelope in data:
            data_message = envelope.get("dataMessage", {})
            if not data_message:
                continue

            source = envelope.get("source", "")
            source_name = envelope.get("sourceName", source)

            attachments = []
            for attach in data_message.get("attachments", []):
                attachments.append({
                    "type": attach.get("contentType", ""),
                    "id": attach.get("id", ""),
                })

            yield InboundMessage(
                message_id=str(envelope.get("timestamp", "")),
                platform="signal",
                sender_id=source,
                sender_name=source_name,
                content=data_message.get("message", ""),
                thread_id=data_message.get("groupId"),
                attachments=attachments,
                raw=envelope,
            )

    async def send_attachment(
        self,
        recipient: str,
        file_path: str,
        message: str = "",
    ) -> str:
        """Send a file attachment via Signal."""
        client = await self._get_client()

        with open(file_path, "rb") as f:
            files = {"attachment": f}
            data = {
                "number": self.phone_number,
                "recipients": [recipient],
            }
            if message:
                data["message"] = message

            response = await client.post(
                f"{self.api_url}/v2/send",
                data=data,
                files=files,
            )

        if response.status_code not in (200, 201):
            raise Exception(f"Signal send_attachment failed: {response.text}")

        result = response.json()
        return str(result.get("timestamp", ""))
