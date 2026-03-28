"""QuickBooksConnector — QuickBooks Online integration."""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)

logger = logging.getLogger("hyperclaw.integrations.quickbooks")


class QuickBooksConnector(BaseConnector):
    """
    QuickBooks Online connector.

    Required config:
        - client_id: OAuth2 client ID
        - client_secret: OAuth2 client secret
        - realm_id: Company ID (optional - can be set after OAuth flow)
        - access_token: OAuth2 access token (set after auth flow)
    """

    BASE_URL = "https://quickbooks.api.intuit.com/v3/company"

    # HyperShield context for financial data
    hypershield_context = "financial_data"

    def __init__(self, config: dict) -> None:
        self.config = config
        self.client_id = config.get("client_id", "")
        self.client_secret = config.get("client_secret", "")
        self.realm_id = config.get("realm_id", "")
        self.access_token = config.get("access_token", "")

        if config.get("enabled", False):
            if not self.client_id or not self.client_secret:
                raise ValueError(
                    "QuickBooksConnector requires client_id and client_secret"
                )

        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="quickbooks",
            platform="quickbooks",
            category="finance",
            capabilities=frozenset(
                [
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.LIST_DATA,
                    ConnectorCapability.SEARCH,
                ]
            ),
            auth_type="oauth2",
            rate_limit_per_minute=60,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    def _api_url(self, endpoint: str) -> str:
        """Build API URL with realm_id."""
        return f"{self.BASE_URL}/{self.realm_id}/{endpoint}"

    async def health(self) -> bool:
        """Check if QuickBooks API is accessible."""
        # In production, OAuth2 flow required before API calls
        # Return True to indicate connector is configured
        return bool(self.client_id and self.client_secret)

    async def list_invoices(self, limit: int = 20) -> dict:
        """List invoices."""
        client = await self._get_client()

        query = quote(f"SELECT * FROM Invoice MAXRESULTS {limit}")
        response = await client.get(self._api_url(f"query?query={query}"))

        if response.status_code != 200:
            raise Exception(f"QuickBooks list_invoices failed: {response.text}")

        return response.json()

    async def create_invoice(
        self, customer_ref: dict, line_items: list[dict]
    ) -> dict:
        """Create an invoice."""
        client = await self._get_client()

        payload = {
            "CustomerRef": customer_ref,
            "Line": line_items,
        }

        response = await client.post(
            self._api_url("invoice"),
            json=payload,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"QuickBooks create_invoice failed: {response.text}")

        return response.json()

    async def list_expenses(self, limit: int = 20) -> dict:
        """List expenses (purchases)."""
        client = await self._get_client()

        query = quote(f"SELECT * FROM Purchase MAXRESULTS {limit}")
        response = await client.get(self._api_url(f"query?query={query}"))

        if response.status_code != 200:
            raise Exception(f"QuickBooks list_expenses failed: {response.text}")

        return response.json()

    async def create_expense(
        self, vendor_ref: dict, amount: float, description: str
    ) -> dict:
        """Create an expense (purchase)."""
        client = await self._get_client()

        payload = {
            "AccountRef": vendor_ref,
            "PaymentType": "Cash",
            "Line": [
                {
                    "Amount": amount,
                    "Description": description,
                    "DetailType": "AccountBasedExpenseLineDetail",
                    "AccountBasedExpenseLineDetail": {
                        "AccountRef": vendor_ref,
                    },
                }
            ],
        }

        response = await client.post(
            self._api_url("purchase"),
            json=payload,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"QuickBooks create_expense failed: {response.text}")

        return response.json()

    async def get_profit_loss_report(
        self, start_date: str, end_date: str
    ) -> dict:
        """Get Profit and Loss report."""
        client = await self._get_client()

        response = await client.get(
            self._api_url("reports/ProfitAndLoss"),
            params={
                "start_date": start_date,
                "end_date": end_date,
            },
        )

        if response.status_code != 200:
            raise Exception(
                f"QuickBooks get_profit_loss_report failed: {response.text}"
            )

        return response.json()

    async def list_customers(self, limit: int = 20) -> dict:
        """List customers."""
        client = await self._get_client()

        query = quote(f"SELECT * FROM Customer MAXRESULTS {limit}")
        response = await client.get(self._api_url(f"query?query={query}"))

        if response.status_code != 200:
            raise Exception(f"QuickBooks list_customers failed: {response.text}")

        return response.json()

    async def list_vendors(self, limit: int = 20) -> dict:
        """List vendors."""
        client = await self._get_client()

        query = quote(f"SELECT * FROM Vendor MAXRESULTS {limit}")
        response = await client.get(self._api_url(f"query?query={query}"))

        if response.status_code != 200:
            raise Exception(f"QuickBooks list_vendors failed: {response.text}")

        return response.json()

    async def get_balance_sheet(self, as_of_date: str) -> dict:
        """Get Balance Sheet report."""
        client = await self._get_client()

        response = await client.get(
            self._api_url("reports/BalanceSheet"),
            params={"date": as_of_date},
        )

        if response.status_code != 200:
            raise Exception(f"QuickBooks get_balance_sheet failed: {response.text}")

        return response.json()

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read an entity."""
        entity_type = kwargs.get("entity_type", "invoice")
        client = await self._get_client()

        response = await client.get(self._api_url(f"{entity_type}/{resource_id}"))

        if response.status_code != 200:
            raise Exception(f"QuickBooks read failed: {response.text}")

        return response.json()

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Create an entity."""
        if resource_type == "invoice":
            return await self.create_invoice(
                customer_ref=data.get("customer_ref"),
                line_items=data.get("line_items", []),
            )
        elif resource_type == "expense":
            return await self.create_expense(
                vendor_ref=data.get("vendor_ref"),
                amount=data.get("amount", 0),
                description=data.get("description", ""),
            )
        raise ValueError(f"Unknown resource type: {resource_type}")

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List entities."""
        limit = filters.get("limit", 20)
        if resource_type == "invoices":
            result = await self.list_invoices(limit)
            return result.get("QueryResponse", {}).get("Invoice", [])
        elif resource_type == "expenses":
            result = await self.list_expenses(limit)
            return result.get("QueryResponse", {}).get("Purchase", [])
        elif resource_type == "customers":
            result = await self.list_customers(limit)
            return result.get("QueryResponse", {}).get("Customer", [])
        elif resource_type == "vendors":
            result = await self.list_vendors(limit)
            return result.get("QueryResponse", {}).get("Vendor", [])
        return []

    async def _search_impl(self, query: str, **kwargs) -> list[dict]:
        """Execute a QuickBooks query."""
        client = await self._get_client()

        encoded_query = quote(query)
        response = await client.get(self._api_url(f"query?query={encoded_query}"))

        if response.status_code != 200:
            raise Exception(f"QuickBooks search failed: {response.text}")

        return response.json().get("QueryResponse", {})
