"""SMSConnector — SMS messaging via Twilio API."""

from __future__ import annotations

import base64
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

logger = logging.getLogger("hyperclaw.integrations.sms")


class SMSConnector(BaseConnector):
    """
    SMS connector via Twilio API.

    Required config:
        - account_sid: Twilio Account SID
        - auth_token: Twilio Auth Token
        - from_number: Twilio phone number to send from
    """

    BASE_URL = "https://api.twilio.com/2010-04-01"

    def __init__(self, config: dict) -> None:
        self.config = config
        self.account_sid = config.get("account_sid", "")
        self.auth_token = config.get("auth_token", "")
        self.from_number = config.get("from_number", "")

        if config.get("enabled", False):
            if not self.account_sid:
                raise ValueError("SMSConnector requires account_sid")
            if not self.auth_token:
                raise ValueError("SMSConnector requires auth_token")
            if not self.from_number:
                raise ValueError("SMSConnector requires from_number")

        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="sms",
            platform="sms",
            category="messaging",
            capabilities=frozenset(
                [
                    ConnectorCapability.SEND_MESSAGE,
                    ConnectorCapability.RECEIVE_MESSAGE,
                    ConnectorCapability.NOTIFY,
                ]
            ),
            auth_type="api_key",
            rate_limit_per_minute=100,
            supports_threads=False,
            supports_attachments=False,
            supports_reactions=False,
        )

    def _get_auth_header(self) -> str:
        """Generate Basic auth header."""
        credentials = f"{self.account_sid}:{self.auth_token}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return f"Basic {encoded}"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={
                    "Authorization": self._get_auth_header(),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
        return self._client

    async def health(self) -> bool:
        """Check if Twilio credentials are valid."""
        try:
            client = await self._get_client()
            response = await client.get(
                f"{self.BASE_URL}/Accounts/{self.account_sid}.json",
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"SMS health check failed: {e}")
            return False

    async def _send_impl(self, message: OutboundMessage) -> str:
        """Send an SMS message."""
        client = await self._get_client()

        data = {
            "To": message.recipient_id,
            "From": self.from_number,
            "Body": message.content,
        }

        response = await client.post(
            f"{self.BASE_URL}/Accounts/{self.account_sid}/Messages.json",
            data=data,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"SMS send failed: {response.text}")

        result = response.json()
        return result.get("sid", "")

    async def _receive_impl(self) -> AsyncIterator[InboundMessage]:
        """
        Receive SMS messages. Twilio uses webhooks for incoming messages.
        This fetches recent messages via API for polling mode.
        """
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/Accounts/{self.account_sid}/Messages.json",
            params={"To": self.from_number, "PageSize": 20},
        )

        if response.status_code != 200:
            return

        data = response.json()

        for msg in data.get("messages", []):
            if msg.get("direction") != "inbound":
                continue

            yield InboundMessage(
                message_id=msg.get("sid", ""),
                platform="sms",
                sender_id=msg.get("from", ""),
                sender_name=msg.get("from", ""),
                content=msg.get("body", ""),
                raw=msg,
            )

    async def send_mms(
        self,
        to: str,
        body: str,
        media_url: str,
    ) -> str:
        """Send an MMS with media attachment."""
        client = await self._get_client()

        data = {
            "To": to,
            "From": self.from_number,
            "Body": body,
            "MediaUrl": media_url,
        }

        response = await client.post(
            f"{self.BASE_URL}/Accounts/{self.account_sid}/Messages.json",
            data=data,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"MMS send failed: {response.text}")

        result = response.json()
        return result.get("sid", "")
