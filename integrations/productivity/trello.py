"""TrelloConnector — Trello API integration."""

from __future__ import annotations

import logging

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)

logger = logging.getLogger("hyperclaw.integrations.trello")


class TrelloConnector(BaseConnector):
    """
    Trello API connector.

    Required config:
        - api_key: Trello API key
        - token: Trello OAuth token
    """

    BASE_URL = "https://api.trello.com/1"

    def __init__(self, config: dict) -> None:
        self.config = config
        self.api_key = config.get("api_key", "")
        self.token = config.get("token", "")

        if config.get("enabled", False):
            if not self.api_key:
                raise ValueError("TrelloConnector requires api_key")
            if not self.token:
                raise ValueError("TrelloConnector requires token")

        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="trello",
            platform="trello",
            category="productivity",
            capabilities=frozenset(
                [
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.DELETE_DATA,
                    ConnectorCapability.LIST_DATA,
                    ConnectorCapability.ACTION,
                ]
            ),
            auth_type="api_key",
            rate_limit_per_minute=100,
        )

    def _auth_params(self) -> dict:
        """Get authentication query parameters."""
        return {"key": self.api_key, "token": self.token}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def health(self) -> bool:
        """Check if Trello API is accessible."""
        try:
            client = await self._get_client()
            response = await client.get(
                f"{self.BASE_URL}/members/me",
                params=self._auth_params(),
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Trello health check failed: {e}")
            return False

    async def list_boards(self) -> list[dict]:
        """List all boards."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/members/me/boards",
            params=self._auth_params(),
        )

        if response.status_code != 200:
            raise Exception(f"Trello list boards failed: {response.text}")

        return response.json()

    async def list_cards(
        self,
        board_id: str,
        list_id: str | None = None,
    ) -> list[dict]:
        """List cards on a board or in a list."""
        client = await self._get_client()

        if list_id:
            url = f"{self.BASE_URL}/lists/{list_id}/cards"
        else:
            url = f"{self.BASE_URL}/boards/{board_id}/cards"

        response = await client.get(url, params=self._auth_params())

        if response.status_code != 200:
            raise Exception(f"Trello list cards failed: {response.text}")

        return response.json()

    async def create_card(
        self,
        list_id: str,
        name: str,
        desc: str = "",
    ) -> dict:
        """Create a new card."""
        client = await self._get_client()

        params = {
            **self._auth_params(),
            "idList": list_id,
            "name": name,
        }
        if desc:
            params["desc"] = desc

        response = await client.post(
            f"{self.BASE_URL}/cards",
            params=params,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Trello create card failed: {response.text}")

        return response.json()

    async def move_card(
        self,
        card_id: str,
        list_id: str,
    ) -> dict:
        """Move a card to a different list."""
        client = await self._get_client()

        params = {**self._auth_params(), "idList": list_id}

        response = await client.put(
            f"{self.BASE_URL}/cards/{card_id}",
            params=params,
        )

        if response.status_code != 200:
            raise Exception(f"Trello move card failed: {response.text}")

        return response.json()

    async def add_comment(
        self,
        card_id: str,
        text: str,
    ) -> dict:
        """Add a comment to a card."""
        client = await self._get_client()

        params = {**self._auth_params(), "text": text}

        response = await client.post(
            f"{self.BASE_URL}/cards/{card_id}/actions/comments",
            params=params,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Trello add comment failed: {response.text}")

        return response.json()

    async def archive_card(self, card_id: str) -> bool:
        """Archive a card."""
        client = await self._get_client()

        params = {**self._auth_params(), "closed": "true"}

        response = await client.put(
            f"{self.BASE_URL}/cards/{card_id}",
            params=params,
        )

        return response.status_code == 200

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read a card."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/cards/{resource_id}",
            params=self._auth_params(),
        )

        if response.status_code != 200:
            raise Exception(f"Trello read card failed: {response.text}")

        return response.json()

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Create a card."""
        return await self.create_card(
            list_id=data.get("list_id"),
            name=data.get("name", "Untitled"),
            desc=data.get("desc", ""),
        )

    async def _delete_impl(self, resource_id: str, **kwargs) -> bool:
        """Archive a card."""
        return await self.archive_card(resource_id)

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List boards or cards."""
        if resource_type == "boards":
            return await self.list_boards()
        return await self.list_cards(
            board_id=filters.get("board_id"),
            list_id=filters.get("list_id"),
        )

    async def _action_impl(self, action_name: str, params: dict) -> dict:
        """Execute Trello-specific actions."""
        if action_name == "move_card":
            return await self.move_card(params["card_id"], params["list_id"])
        elif action_name == "add_comment":
            return await self.add_comment(params["card_id"], params["text"])
        elif action_name == "archive_card":
            success = await self.archive_card(params["card_id"])
            return {"success": success}

        raise ValueError(f"Unknown action: {action_name}")
