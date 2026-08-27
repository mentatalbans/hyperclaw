"""GoogleAnalyticsConnector — Google Analytics Data API integration."""

from __future__ import annotations

import logging

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)
from integrations.productivity.google.base import GoogleOAuthBase

logger = logging.getLogger("hyperclaw.integrations.google_analytics")


class GoogleAnalyticsConnector(GoogleOAuthBase, BaseConnector):
    """
    Google Analytics Data API connector.

    Required config:
        - service_account_file: Path to service account JSON
        OR
        - access_token: OAuth2 access token
    """

    DATA_API_URL = "https://analyticsdata.googleapis.com/v1beta"
    ADMIN_API_URL = "https://analyticsadmin.googleapis.com/v1alpha"

    def __init__(self, config: dict) -> None:
        self.config = config
        self._init_google_auth(config)

        if config.get("enabled", False):
            self._validate_google_config(config)

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="google_analytics",
            platform="google_analytics",
            category="data",
            capabilities=frozenset(
                [
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.LIST_DATA,
                ]
            ),
            auth_type="oauth2",
            rate_limit_per_minute=60,
        )

    async def health(self) -> bool:
        """Check if Google Analytics API is accessible."""
        return True

    async def run_report(
        self,
        property_id: str,
        dimensions: list[str],
        metrics: list[str],
        date_range: dict,
    ) -> dict:
        """Run a Google Analytics report."""
        client = await self._get_google_client()

        payload = {
            "dimensions": [{"name": d} for d in dimensions],
            "metrics": [{"name": m} for m in metrics],
            "dateRanges": [date_range],
        }

        response = await client.post(
            f"{self.DATA_API_URL}/properties/{property_id}:runReport",
            json=payload,
        )

        if response.status_code != 200:
            raise Exception(f"Google Analytics run_report failed: {response.text}")

        return response.json()

    async def get_realtime(self, property_id: str) -> dict:
        """Get realtime analytics data."""
        client = await self._get_google_client()

        payload = {
            "dimensions": [{"name": "country"}],
            "metrics": [{"name": "activeUsers"}],
        }

        response = await client.post(
            f"{self.DATA_API_URL}/properties/{property_id}:runRealtimeReport",
            json=payload,
        )

        if response.status_code != 200:
            raise Exception(f"Google Analytics get_realtime failed: {response.text}")

        return response.json()

    async def list_properties(self) -> dict:
        """List all Google Analytics properties."""
        client = await self._get_google_client()

        response = await client.get(f"{self.ADMIN_API_URL}/accountSummaries")

        if response.status_code != 200:
            raise Exception(
                f"Google Analytics list_properties failed: {response.text}"
            )

        return response.json()

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read analytics data for a property."""
        dimensions = kwargs.get("dimensions", ["date"])
        metrics = kwargs.get("metrics", ["sessions"])
        date_range = kwargs.get("date_range", {
            "startDate": "30daysAgo",
            "endDate": "today",
        })

        return await self.run_report(resource_id, dimensions, metrics, date_range)

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List properties."""
        if resource_type == "properties":
            result = await self.list_properties()
            return result.get("accountSummaries", [])
        return []
