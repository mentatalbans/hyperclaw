"""TodoistConnector — Todoist API integration."""

from __future__ import annotations

import logging

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)

logger = logging.getLogger("hyperclaw.integrations.todoist")


class TodoistConnector(BaseConnector):
    """
    Todoist API connector.

    Required config:
        - api_token: Todoist API token
    """

    BASE_URL = "https://api.todoist.com/rest/v2"
    SYNC_URL = "https://api.todoist.com/sync/v9"

    def __init__(self, config: dict) -> None:
        self.config = config
        self.api_token = config.get("api_token", "")

        if config.get("enabled", False) and not self.api_token:
            raise ValueError("TodoistConnector requires api_token")

        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="todoist",
            platform="todoist",
            category="productivity",
            capabilities=frozenset(
                [
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.DELETE_DATA,
                    ConnectorCapability.LIST_DATA,
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
                    "Authorization": f"Bearer {self.api_token}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def health(self) -> bool:
        """Check if Todoist API is accessible."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.BASE_URL}/projects")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Todoist health check failed: {e}")
            return False

    async def list_tasks(
        self,
        project_id: str | None = None,
        filter: str | None = None,
    ) -> list[dict]:
        """List tasks."""
        client = await self._get_client()

        params = {}
        if project_id:
            params["project_id"] = project_id
        if filter:
            params["filter"] = filter

        response = await client.get(
            f"{self.BASE_URL}/tasks",
            params=params,
        )

        if response.status_code != 200:
            raise Exception(f"Todoist list tasks failed: {response.text}")

        return response.json()

    async def create_task(
        self,
        content: str,
        project_id: str | None = None,
        due_string: str | None = None,
        priority: int = 1,
        description: str = "",
    ) -> dict:
        """Create a new task."""
        client = await self._get_client()

        data = {"content": content, "priority": priority}
        if project_id:
            data["project_id"] = project_id
        if due_string:
            data["due_string"] = due_string
        if description:
            data["description"] = description

        response = await client.post(
            f"{self.BASE_URL}/tasks",
            json=data,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Todoist create task failed: {response.text}")

        return response.json()

    async def complete_task(self, task_id: str) -> bool:
        """Complete a task."""
        client = await self._get_client()

        response = await client.post(f"{self.BASE_URL}/tasks/{task_id}/close")

        return response.status_code == 204

    async def update_task(
        self,
        task_id: str,
        fields: dict,
    ) -> dict:
        """Update a task."""
        client = await self._get_client()

        response = await client.post(
            f"{self.BASE_URL}/tasks/{task_id}",
            json=fields,
        )

        if response.status_code != 200:
            raise Exception(f"Todoist update task failed: {response.text}")

        return response.json()

    async def delete_task(self, task_id: str) -> bool:
        """Delete a task."""
        client = await self._get_client()

        response = await client.delete(f"{self.BASE_URL}/tasks/{task_id}")

        return response.status_code == 204

    async def list_projects(self) -> list[dict]:
        """List all projects."""
        client = await self._get_client()

        response = await client.get(f"{self.BASE_URL}/projects")

        if response.status_code != 200:
            raise Exception(f"Todoist list projects failed: {response.text}")

        return response.json()

    async def create_project(
        self,
        name: str,
        parent_id: str | None = None,
    ) -> dict:
        """Create a new project."""
        client = await self._get_client()

        data = {"name": name}
        if parent_id:
            data["parent_id"] = parent_id

        response = await client.post(
            f"{self.BASE_URL}/projects",
            json=data,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Todoist create project failed: {response.text}")

        return response.json()

    async def get_activity_log(self, limit: int = 50) -> list[dict]:
        """Get activity log using Sync API."""
        client = await self._get_client()

        response = await client.post(
            f"{self.SYNC_URL}/activity/get",
            json={"limit": limit},
        )

        if response.status_code != 200:
            raise Exception(f"Todoist activity log failed: {response.text}")

        return response.json().get("events", [])

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read a task."""
        client = await self._get_client()

        response = await client.get(f"{self.BASE_URL}/tasks/{resource_id}")

        if response.status_code != 200:
            raise Exception(f"Todoist read task failed: {response.text}")

        return response.json()

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Create a task or project."""
        if resource_type == "project":
            return await self.create_project(
                name=data.get("name", "Untitled"),
                parent_id=data.get("parent_id"),
            )
        return await self.create_task(
            content=data.get("content", "Untitled"),
            project_id=data.get("project_id"),
            due_string=data.get("due_string"),
            priority=data.get("priority", 1),
            description=data.get("description", ""),
        )

    async def _delete_impl(self, resource_id: str, **kwargs) -> bool:
        """Delete a task."""
        return await self.delete_task(resource_id)

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List tasks or projects."""
        if resource_type == "projects":
            return await self.list_projects()
        return await self.list_tasks(
            project_id=filters.get("project_id"),
            filter=filters.get("filter"),
        )
