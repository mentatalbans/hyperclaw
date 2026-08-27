"""AsanaConnector — Asana API integration."""

from __future__ import annotations

import logging

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)

logger = logging.getLogger("hyperclaw.integrations.asana")


class AsanaConnector(BaseConnector):
    """
    Asana API connector.

    Required config:
        - access_token: Asana Personal Access Token
    """

    BASE_URL = "https://app.asana.com/api/1.0"

    def __init__(self, config: dict) -> None:
        self.config = config
        self.access_token = config.get("access_token", "")

        if config.get("enabled", False) and not self.access_token:
            raise ValueError("AsanaConnector requires access_token")

        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="asana",
            platform="asana",
            category="productivity",
            capabilities=frozenset(
                [
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.DELETE_DATA,
                    ConnectorCapability.LIST_DATA,
                    ConnectorCapability.SEARCH,
                    ConnectorCapability.WEBHOOKS,
                ]
            ),
            auth_type="bearer",
            rate_limit_per_minute=150,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def health(self) -> bool:
        """Check if Asana API is accessible."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.BASE_URL}/users/me")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Asana health check failed: {e}")
            return False

    async def list_projects(self, workspace_gid: str) -> list[dict]:
        """List projects in a workspace."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/workspaces/{workspace_gid}/projects"
        )

        if response.status_code != 200:
            raise Exception(f"Asana list projects failed: {response.text}")

        return response.json().get("data", [])

    async def list_tasks(
        self,
        project_gid: str,
        completed: bool | None = None,
    ) -> list[dict]:
        """List tasks in a project."""
        client = await self._get_client()

        params = {"opt_fields": "name,completed,due_on,assignee.name"}
        if completed is not None:
            params["completed"] = str(completed).lower()

        response = await client.get(
            f"{self.BASE_URL}/projects/{project_gid}/tasks",
            params=params,
        )

        if response.status_code != 200:
            raise Exception(f"Asana list tasks failed: {response.text}")

        return response.json().get("data", [])

    async def create_task(
        self,
        name: str,
        project_gid: str,
        assignee: str | None = None,
        due_on: str | None = None,
        notes: str = "",
    ) -> dict:
        """Create a new task."""
        client = await self._get_client()

        data = {
            "name": name,
            "projects": [project_gid],
        }
        if assignee:
            data["assignee"] = assignee
        if due_on:
            data["due_on"] = due_on
        if notes:
            data["notes"] = notes

        response = await client.post(
            f"{self.BASE_URL}/tasks",
            json={"data": data},
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Asana create task failed: {response.text}")

        return response.json().get("data", {})

    async def update_task(
        self,
        task_gid: str,
        fields: dict,
    ) -> dict:
        """Update a task."""
        client = await self._get_client()

        response = await client.put(
            f"{self.BASE_URL}/tasks/{task_gid}",
            json={"data": fields},
        )

        if response.status_code != 200:
            raise Exception(f"Asana update task failed: {response.text}")

        return response.json().get("data", {})

    async def complete_task(self, task_gid: str) -> dict:
        """Mark a task as completed."""
        return await self.update_task(task_gid, {"completed": True})

    async def add_comment(
        self,
        task_gid: str,
        text: str,
    ) -> dict:
        """Add a comment to a task."""
        client = await self._get_client()

        response = await client.post(
            f"{self.BASE_URL}/tasks/{task_gid}/stories",
            json={"data": {"text": text}},
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Asana add comment failed: {response.text}")

        return response.json().get("data", {})

    async def search_tasks(
        self,
        workspace_gid: str,
        query: str,
    ) -> list[dict]:
        """Search for tasks."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/workspaces/{workspace_gid}/tasks/search",
            params={"text": query},
        )

        if response.status_code != 200:
            raise Exception(f"Asana search failed: {response.text}")

        return response.json().get("data", [])

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read a task."""
        client = await self._get_client()

        response = await client.get(f"{self.BASE_URL}/tasks/{resource_id}")

        if response.status_code != 200:
            raise Exception(f"Asana read task failed: {response.text}")

        return response.json().get("data", {})

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Create a task."""
        return await self.create_task(
            name=data.get("name", "Untitled"),
            project_gid=data.get("project_gid"),
            assignee=data.get("assignee"),
            due_on=data.get("due_on"),
            notes=data.get("notes", ""),
        )

    async def _delete_impl(self, resource_id: str, **kwargs) -> bool:
        """Delete a task."""
        client = await self._get_client()

        response = await client.delete(f"{self.BASE_URL}/tasks/{resource_id}")

        return response.status_code == 200

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List projects or tasks."""
        if resource_type == "projects":
            return await self.list_projects(filters.get("workspace_gid"))
        return await self.list_tasks(
            project_gid=filters.get("project_gid"),
            completed=filters.get("completed"),
        )

    async def _search_impl(self, query: str, **kwargs) -> list[dict]:
        """Search tasks."""
        return await self.search_tasks(
            workspace_gid=kwargs.get("workspace_gid"),
            query=query,
        )
