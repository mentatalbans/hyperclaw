"""GoogleTasksConnector — Google Tasks API integration."""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)
from integrations.productivity.google.base import GoogleOAuthBase

logger = logging.getLogger("hyperclaw.integrations.google_tasks")


class GoogleTasksConnector(BaseConnector, GoogleOAuthBase):
    """
    Google Tasks API connector.

    Required config (via GoogleOAuthBase):
        - access_token or credentials_file
    """

    BASE_URL = "https://tasks.googleapis.com/tasks/v1"

    def __init__(self, config: dict) -> None:
        self.config = config
        self._init_google_auth(config)
        self._validate_google_config(config)

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="google_tasks",
            platform="google_tasks",
            category="productivity",
            capabilities=frozenset(
                [
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.DELETE_DATA,
                    ConnectorCapability.LIST_DATA,
                ]
            ),
            auth_type="oauth2",
            rate_limit_per_minute=100,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        return await self._get_google_client()

    async def health(self) -> bool:
        """Check if Tasks API is accessible."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.BASE_URL}/users/@me/lists")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Google Tasks health check failed: {e}")
            return False

    async def list_tasks(
        self,
        tasklist_id: str = "@default",
        show_completed: bool = False,
        max_results: int = 100,
    ) -> list[dict]:
        """List tasks in a task list."""
        client = await self._get_client()

        params = {"maxResults": max_results}
        if not show_completed:
            params["showCompleted"] = "false"

        response = await client.get(
            f"{self.BASE_URL}/lists/{tasklist_id}/tasks",
            params=params,
        )

        if response.status_code != 200:
            raise Exception(f"Tasks list failed: {response.text}")

        return response.json().get("items", [])

    async def create_task(
        self,
        title: str,
        due: datetime | None = None,
        notes: str | None = None,
        tasklist_id: str = "@default",
    ) -> dict:
        """Create a new task."""
        client = await self._get_client()

        task = {"title": title}

        if due:
            task["due"] = due.isoformat() + "Z"
        if notes:
            task["notes"] = notes

        response = await client.post(
            f"{self.BASE_URL}/lists/{tasklist_id}/tasks",
            json=task,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Tasks create failed: {response.text}")

        return response.json()

    async def complete_task(
        self,
        task_id: str,
        tasklist_id: str = "@default",
    ) -> dict:
        """Mark a task as completed."""
        client = await self._get_client()

        response = await client.patch(
            f"{self.BASE_URL}/lists/{tasklist_id}/tasks/{task_id}",
            json={"status": "completed"},
        )

        if response.status_code != 200:
            raise Exception(f"Tasks complete failed: {response.text}")

        return response.json()

    async def delete_task(
        self,
        task_id: str,
        tasklist_id: str = "@default",
    ) -> bool:
        """Delete a task."""
        client = await self._get_client()

        response = await client.delete(
            f"{self.BASE_URL}/lists/{tasklist_id}/tasks/{task_id}"
        )

        return response.status_code == 204

    async def list_tasklists(self) -> list[dict]:
        """List all task lists."""
        client = await self._get_client()

        response = await client.get(f"{self.BASE_URL}/users/@me/lists")

        if response.status_code != 200:
            raise Exception(f"Tasks list tasklists failed: {response.text}")

        return response.json().get("items", [])

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read a task."""
        client = await self._get_client()
        tasklist_id = kwargs.get("tasklist_id", "@default")

        response = await client.get(
            f"{self.BASE_URL}/lists/{tasklist_id}/tasks/{resource_id}"
        )

        if response.status_code != 200:
            raise Exception(f"Tasks read failed: {response.text}")

        return response.json()

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Create a task."""
        return await self.create_task(
            title=data.get("title", "Untitled"),
            due=data.get("due"),
            notes=data.get("notes"),
            tasklist_id=data.get("tasklist_id", "@default"),
        )

    async def _delete_impl(self, resource_id: str, **kwargs) -> bool:
        """Delete a task."""
        return await self.delete_task(
            resource_id,
            kwargs.get("tasklist_id", "@default"),
        )

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List tasks."""
        if resource_type == "tasklists":
            return await self.list_tasklists()
        return await self.list_tasks(
            tasklist_id=filters.get("tasklist_id", "@default"),
            show_completed=filters.get("show_completed", False),
            max_results=filters.get("max_results", 100),
        )
