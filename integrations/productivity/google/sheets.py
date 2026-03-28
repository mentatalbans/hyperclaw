"""GoogleSheetsConnector — Google Sheets API integration."""

from __future__ import annotations

import logging

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)
from integrations.productivity.google.base import GoogleOAuthBase

logger = logging.getLogger("hyperclaw.integrations.google_sheets")


class GoogleSheetsConnector(BaseConnector, GoogleOAuthBase):
    """
    Google Sheets API connector.

    Required config (via GoogleOAuthBase):
        - access_token or credentials_file
    """

    BASE_URL = "https://sheets.googleapis.com/v4"

    def __init__(self, config: dict) -> None:
        self.config = config
        self._init_google_auth(config)
        self._validate_google_config(config)

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="google_sheets",
            platform="google_sheets",
            category="productivity",
            capabilities=frozenset(
                [
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.LIST_DATA,
                    ConnectorCapability.ACTION,
                ]
            ),
            auth_type="oauth2",
            rate_limit_per_minute=100,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        return await self._get_google_client()

    async def health(self) -> bool:
        """Check if Sheets API is accessible."""
        try:
            # Sheets API doesn't have a simple health endpoint
            # We'll use Drive API to verify OAuth
            client = await self._get_client()
            response = await client.get(
                "https://www.googleapis.com/drive/v3/about",
                params={"fields": "user"},
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Google Sheets health check failed: {e}")
            return False

    async def read_range(
        self,
        spreadsheet_id: str,
        range: str,
    ) -> list[list]:
        """Read values from a range."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/spreadsheets/{spreadsheet_id}/values/{range}"
        )

        if response.status_code != 200:
            raise Exception(f"Sheets read range failed: {response.text}")

        return response.json().get("values", [])

    async def write_range(
        self,
        spreadsheet_id: str,
        range: str,
        values: list[list],
    ) -> dict:
        """Write values to a range."""
        client = await self._get_client()

        response = await client.put(
            f"{self.BASE_URL}/spreadsheets/{spreadsheet_id}/values/{range}",
            params={"valueInputOption": "USER_ENTERED"},
            json={"values": values},
        )

        if response.status_code != 200:
            raise Exception(f"Sheets write range failed: {response.text}")

        return response.json()

    async def append_rows(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        rows: list[list],
    ) -> dict:
        """Append rows to a sheet."""
        client = await self._get_client()

        response = await client.post(
            f"{self.BASE_URL}/spreadsheets/{spreadsheet_id}/values/{sheet_name}:append",
            params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
            json={"values": rows},
        )

        if response.status_code != 200:
            raise Exception(f"Sheets append rows failed: {response.text}")

        return response.json()

    async def create_spreadsheet(
        self,
        title: str,
        sheets: list[str] | None = None,
    ) -> dict:
        """Create a new spreadsheet."""
        client = await self._get_client()

        body = {"properties": {"title": title}}

        if sheets:
            body["sheets"] = [{"properties": {"title": name}} for name in sheets]

        response = await client.post(
            f"{self.BASE_URL}/spreadsheets",
            json=body,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Sheets create spreadsheet failed: {response.text}")

        return response.json()

    async def get_sheet_metadata(self, spreadsheet_id: str) -> dict:
        """Get spreadsheet metadata."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/spreadsheets/{spreadsheet_id}",
            params={"fields": "properties,sheets.properties"},
        )

        if response.status_code != 200:
            raise Exception(f"Sheets get metadata failed: {response.text}")

        return response.json()

    async def clear_range(
        self,
        spreadsheet_id: str,
        range: str,
    ) -> dict:
        """Clear values from a range."""
        client = await self._get_client()

        response = await client.post(
            f"{self.BASE_URL}/spreadsheets/{spreadsheet_id}/values/{range}:clear"
        )

        if response.status_code != 200:
            raise Exception(f"Sheets clear range failed: {response.text}")

        return response.json()

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read spreadsheet metadata or range."""
        if "range" in kwargs:
            values = await self.read_range(resource_id, kwargs["range"])
            return {"values": values}
        return await self.get_sheet_metadata(resource_id)

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Create a spreadsheet."""
        return await self.create_spreadsheet(
            title=data.get("title", "Untitled"),
            sheets=data.get("sheets"),
        )

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List sheets in a spreadsheet."""
        metadata = await self.get_sheet_metadata(filters.get("spreadsheet_id"))
        return metadata.get("sheets", [])

    async def _action_impl(self, action_name: str, params: dict) -> dict:
        """Execute Sheets-specific actions."""
        if action_name == "write_range":
            return await self.write_range(
                params["spreadsheet_id"],
                params["range"],
                params["values"],
            )
        elif action_name == "append_rows":
            return await self.append_rows(
                params["spreadsheet_id"],
                params["sheet_name"],
                params["rows"],
            )
        elif action_name == "clear_range":
            return await self.clear_range(
                params["spreadsheet_id"],
                params["range"],
            )

        raise ValueError(f"Unknown action: {action_name}")
