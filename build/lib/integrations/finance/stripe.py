"""StripeConnector — Stripe payments integration."""

from __future__ import annotations

import logging

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)

logger = logging.getLogger("hyperclaw.integrations.stripe")


class StripeConnector(BaseConnector):
    """
    Stripe payments connector.

    Required config:
        - secret_key: Stripe secret API key (sk_live_* or sk_test_*)
    """

    BASE_URL = "https://api.stripe.com/v1"

    # HyperShield context for financial data
    hypershield_context = "financial_data"

    def __init__(self, config: dict) -> None:
        self.config = config
        self.secret_key = config.get("secret_key", "")

        if config.get("enabled", False) and not self.secret_key:
            raise ValueError("StripeConnector requires secret_key")

        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="stripe",
            platform="stripe",
            category="finance",
            capabilities=frozenset(
                [
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.LIST_DATA,
                    ConnectorCapability.WEBHOOKS,
                    ConnectorCapability.ACTION,
                ]
            ),
            auth_type="bearer",
            rate_limit_per_minute=100,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={
                    "Authorization": f"Bearer {self.secret_key}",
                },
            )
        return self._client

    async def health(self) -> bool:
        """Check if Stripe API is accessible."""
        try:
            client = await self._get_client()
            response = await client.get(
                f"{self.BASE_URL}/customers",
                params={"limit": 1},
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Stripe health check failed: {e}")
            return False

    async def list_customers(self, limit: int = 10) -> dict:
        """List customers."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/customers",
            params={"limit": limit},
        )

        if response.status_code != 200:
            raise Exception(f"Stripe list_customers failed: {response.text}")

        return response.json()

    async def get_customer(self, customer_id: str) -> dict:
        """Get a customer by ID."""
        client = await self._get_client()

        response = await client.get(f"{self.BASE_URL}/customers/{customer_id}")

        if response.status_code != 200:
            raise Exception(f"Stripe get_customer failed: {response.text}")

        return response.json()

    async def list_invoices(
        self, customer_id: str | None = None, limit: int = 10
    ) -> dict:
        """List invoices, optionally filtered by customer."""
        client = await self._get_client()

        params = {"limit": limit}
        if customer_id:
            params["customer"] = customer_id

        response = await client.get(
            f"{self.BASE_URL}/invoices",
            params=params,
        )

        if response.status_code != 200:
            raise Exception(f"Stripe list_invoices failed: {response.text}")

        return response.json()

    async def get_invoice(self, invoice_id: str) -> dict:
        """Get an invoice by ID."""
        client = await self._get_client()

        response = await client.get(f"{self.BASE_URL}/invoices/{invoice_id}")

        if response.status_code != 200:
            raise Exception(f"Stripe get_invoice failed: {response.text}")

        return response.json()

    async def list_payments(self, limit: int = 10) -> dict:
        """List payment intents."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/payment_intents",
            params={"limit": limit},
        )

        if response.status_code != 200:
            raise Exception(f"Stripe list_payments failed: {response.text}")

        return response.json()

    async def create_payment_link(self, price_id: str, quantity: int = 1) -> dict:
        """Create a payment link."""
        client = await self._get_client()

        response = await client.post(
            f"{self.BASE_URL}/payment_links",
            data={
                "line_items[0][price]": price_id,
                "line_items[0][quantity]": quantity,
            },
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Stripe create_payment_link failed: {response.text}")

        return response.json()

    async def list_subscriptions(self, customer_id: str | None = None) -> dict:
        """List subscriptions, optionally filtered by customer."""
        client = await self._get_client()

        params = {}
        if customer_id:
            params["customer"] = customer_id

        response = await client.get(
            f"{self.BASE_URL}/subscriptions",
            params=params,
        )

        if response.status_code != 200:
            raise Exception(f"Stripe list_subscriptions failed: {response.text}")

        return response.json()

    async def cancel_subscription(self, subscription_id: str) -> dict:
        """Cancel a subscription."""
        client = await self._get_client()

        response = await client.delete(
            f"{self.BASE_URL}/subscriptions/{subscription_id}"
        )

        if response.status_code != 200:
            raise Exception(f"Stripe cancel_subscription failed: {response.text}")

        return response.json()

    async def list_products(self) -> dict:
        """List products."""
        client = await self._get_client()

        response = await client.get(f"{self.BASE_URL}/products")

        if response.status_code != 200:
            raise Exception(f"Stripe list_products failed: {response.text}")

        return response.json()

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read a customer or invoice."""
        resource_type = kwargs.get("resource_type", "customer")
        if resource_type == "invoice":
            return await self.get_invoice(resource_id)
        return await self.get_customer(resource_id)

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Create a payment link."""
        if resource_type == "payment_link":
            return await self.create_payment_link(
                price_id=data.get("price_id"),
                quantity=data.get("quantity", 1),
            )
        raise ValueError(f"Unknown resource type: {resource_type}")

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List resources."""
        if resource_type == "customers":
            result = await self.list_customers(filters.get("limit", 10))
            return result.get("data", [])
        elif resource_type == "invoices":
            result = await self.list_invoices(
                customer_id=filters.get("customer_id"),
                limit=filters.get("limit", 10),
            )
            return result.get("data", [])
        elif resource_type == "payments":
            result = await self.list_payments(filters.get("limit", 10))
            return result.get("data", [])
        elif resource_type == "subscriptions":
            result = await self.list_subscriptions(filters.get("customer_id"))
            return result.get("data", [])
        elif resource_type == "products":
            result = await self.list_products()
            return result.get("data", [])
        return []

    async def _action_impl(self, action_name: str, params: dict) -> dict:
        """Execute Stripe-specific actions."""
        if action_name == "cancel_subscription":
            return await self.cancel_subscription(params.get("subscription_id"))
        elif action_name == "create_payment_link":
            return await self.create_payment_link(
                price_id=params.get("price_id"),
                quantity=params.get("quantity", 1),
            )

        raise ValueError(f"Unknown action: {action_name}")
