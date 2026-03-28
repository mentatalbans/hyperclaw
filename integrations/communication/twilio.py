"""TwilioConnector — Twilio messaging integration."""

from __future__ import annotations

import logging

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)

logger = logging.getLogger("hyperclaw.integrations.twilio")


class TwilioConnector(BaseConnector):
    """
    Twilio messaging connector for SMS, WhatsApp, and voice.

    Required config:
        - account_sid: Twilio account SID
        - auth_token: Twilio auth token
    Optional config:
        - from_number: Default from phone number
    """

    def __init__(self, config: dict) -> None:
        self.config = config
        self.account_sid = config.get("account_sid", "")
        self.auth_token = config.get("auth_token", "")
        self.from_number = config.get("from_number", "")

        if config.get("enabled", False):
            if not self.account_sid or not self.auth_token:
                raise ValueError(
                    "TwilioConnector requires account_sid and auth_token"
                )

        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="twilio",
            platform="twilio",
            category="communication",
            capabilities=frozenset(
                [
                    ConnectorCapability.SEND_MESSAGE,
                    ConnectorCapability.RECEIVE_MESSAGE,
                    ConnectorCapability.ACTION,
                    ConnectorCapability.WEBHOOKS,
                ]
            ),
            auth_type="api_key",
            rate_limit_per_minute=100,
        )

    @property
    def _base_url(self) -> str:
        return f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                auth=(self.account_sid, self.auth_token),
            )
        return self._client

    async def health(self) -> bool:
        """Check if Twilio API is accessible."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self._base_url}.json")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Twilio health check failed: {e}")
            return False

    async def send_sms(
        self,
        to: str,
        body: str,
        from_: str | None = None,
    ) -> dict:
        """Send an SMS message."""
        client = await self._get_client()

        data = {
            "To": to,
            "Body": body,
            "From": from_ or self.from_number,
        }

        response = await client.post(
            f"{self._base_url}/Messages.json",
            data=data,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Twilio send_sms failed: {response.text}")

        return response.json()

    async def send_whatsapp(
        self,
        to: str,
        body: str,
        from_: str | None = None,
    ) -> dict:
        """Send a WhatsApp message via Twilio."""
        client = await self._get_client()

        # Add whatsapp: prefix if not present
        if not to.startswith("whatsapp:"):
            to = f"whatsapp:{to}"

        from_num = from_ or self.from_number
        if not from_num.startswith("whatsapp:"):
            from_num = f"whatsapp:{from_num}"

        data = {
            "To": to,
            "Body": body,
            "From": from_num,
        }

        response = await client.post(
            f"{self._base_url}/Messages.json",
            data=data,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Twilio send_whatsapp failed: {response.text}")

        return response.json()

    async def make_call(
        self,
        to: str,
        from_: str,
        url: str,
    ) -> dict:
        """Make a voice call."""
        client = await self._get_client()

        data = {
            "To": to,
            "From": from_,
            "Url": url,
        }

        response = await client.post(
            f"{self._base_url}/Calls.json",
            data=data,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Twilio make_call failed: {response.text}")

        return response.json()

    async def send_mms(
        self,
        to: str,
        body: str,
        media_url: str,
        from_: str | None = None,
    ) -> dict:
        """Send an MMS message with media."""
        client = await self._get_client()

        data = {
            "To": to,
            "Body": body,
            "From": from_ or self.from_number,
            "MediaUrl": media_url,
        }

        response = await client.post(
            f"{self._base_url}/Messages.json",
            data=data,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Twilio send_mms failed: {response.text}")

        return response.json()

    async def list_messages(self, limit: int = 20) -> dict:
        """List recent messages."""
        client = await self._get_client()

        response = await client.get(
            f"{self._base_url}/Messages.json",
            params={"PageSize": limit},
        )

        if response.status_code != 200:
            raise Exception(f"Twilio list_messages failed: {response.text}")

        return response.json()

    async def get_message(self, message_sid: str) -> dict:
        """Get a specific message."""
        client = await self._get_client()

        response = await client.get(
            f"{self._base_url}/Messages/{message_sid}.json"
        )

        if response.status_code != 200:
            raise Exception(f"Twilio get_message failed: {response.text}")

        return response.json()

    async def list_calls(self, limit: int = 20) -> dict:
        """List recent calls."""
        client = await self._get_client()

        response = await client.get(
            f"{self._base_url}/Calls.json",
            params={"PageSize": limit},
        )

        if response.status_code != 200:
            raise Exception(f"Twilio list_calls failed: {response.text}")

        return response.json()

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read a message."""
        return await self.get_message(resource_id)

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List resources."""
        limit = filters.get("limit", 20)
        if resource_type == "messages":
            result = await self.list_messages(limit)
            return result.get("messages", [])
        elif resource_type == "calls":
            result = await self.list_calls(limit)
            return result.get("calls", [])
        return []

    async def _action_impl(self, action_name: str, params: dict) -> dict:
        """Execute Twilio-specific actions."""
        if action_name == "send_sms":
            return await self.send_sms(
                to=params.get("to"),
                body=params.get("body", ""),
                from_=params.get("from"),
            )
        elif action_name == "send_whatsapp":
            return await self.send_whatsapp(
                to=params.get("to"),
                body=params.get("body", ""),
                from_=params.get("from"),
            )
        elif action_name == "make_call":
            return await self.make_call(
                to=params.get("to"),
                from_=params.get("from"),
                url=params.get("url"),
            )
        elif action_name == "send_mms":
            return await self.send_mms(
                to=params.get("to"),
                body=params.get("body", ""),
                media_url=params.get("media_url"),
                from_=params.get("from"),
            )

        raise ValueError(f"Unknown action: {action_name}")
