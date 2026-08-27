"""ZapierConnector — Zapier webhook integration."""

from __future__ import annotations

import logging

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)

logger = logging.getLogger("hyperclaw.integrations.zapier")


class ZapierConnector(BaseConnector):
    """
    Zapier webhook connector for triggering Zaps.

    Required config:
        - webhook_url: Zapier webhook URL
    """

    def __init__(self, config: dict) -> None:
        self.config = config
        self.webhook_url = config.get("webhook_url", "")

        if config.get("enabled", False) and not self.webhook_url:
            raise ValueError("ZapierConnector requires webhook_url")

        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="zapier",
            platform="zapier",
            category="automation",
            capabilities=frozenset(
                [
                    ConnectorCapability.ACTION,
                    ConnectorCapability.WEBHOOKS,
                ]
            ),
            auth_type="webhook_token",
            rate_limit_per_minute=60,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={"Content-Type": "application/json"},
            )
        return self._client

    async def health(self) -> bool:
        """Zapier webhooks don't have a health endpoint."""
        return True

    async def trigger_zap(self, data: dict) -> dict:
        """Trigger a Zap with the given data."""
        client = await self._get_client()

        response = await client.post(
            self.webhook_url,
            json=data,
        )

        # Zapier webhooks return various status codes
        if response.status_code not in (200, 201, 202):
            raise Exception(f"Zapier trigger_zap failed: {response.text}")

        try:
            return response.json()
        except Exception:
            return {"status": "triggered", "response": response.text}

    async def _action_impl(self, action_name: str, params: dict) -> dict:
        """Execute Zapier-specific actions."""
        if action_name == "trigger" or action_name == "trigger_zap":
            return await self.trigger_zap(params)

        # Default: pass all params to trigger
        return await self.trigger_zap(params)
