"""GitLabConnector — GitLab API integration."""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)

logger = logging.getLogger("hyperclaw.integrations.gitlab")


class GitLabConnector(BaseConnector):
    """
    GitLab API connector.

    Required config:
        - token: GitLab Personal Access Token
        - base_url: GitLab instance URL (default: https://gitlab.com)
    """

    def __init__(self, config: dict) -> None:
        self.config = config
        self.token = config.get("token", "")
        self.base_url = config.get("base_url", "https://gitlab.com").rstrip("/")

        if config.get("enabled", False) and not self.token:
            raise ValueError("GitLabConnector requires token")

        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="gitlab",
            platform="gitlab",
            category="developer",
            capabilities=frozenset(
                [
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.LIST_DATA,
                    ConnectorCapability.SEARCH,
                    ConnectorCapability.WEBHOOKS,
                ]
            ),
            auth_type="api_key",
            rate_limit_per_minute=60,
        )

    @property
    def _api_url(self) -> str:
        return f"{self.base_url}/api/v4"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={
                    "PRIVATE-TOKEN": self.token,
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def health(self) -> bool:
        """Check if GitLab API is accessible."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self._api_url}/user")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"GitLab health check failed: {e}")
            return False

    async def list_projects(self, search: str | None = None) -> list[dict]:
        """List projects, optionally filtered by search."""
        client = await self._get_client()

        params = {"per_page": 100}
        if search:
            params["search"] = search

        response = await client.get(
            f"{self._api_url}/projects",
            params=params,
        )

        if response.status_code != 200:
            raise Exception(f"GitLab list_projects failed: {response.text}")

        return response.json()

    async def list_issues(
        self,
        project_id: int | str,
        state: str = "opened",
    ) -> list[dict]:
        """List issues for a project."""
        client = await self._get_client()

        # URL-encode project_id if it's a path
        encoded_id = quote(str(project_id), safe="")

        response = await client.get(
            f"{self._api_url}/projects/{encoded_id}/issues",
            params={"state": state, "per_page": 100},
        )

        if response.status_code != 200:
            raise Exception(f"GitLab list_issues failed: {response.text}")

        return response.json()

    async def create_issue(
        self,
        project_id: int | str,
        title: str,
        description: str = "",
    ) -> dict:
        """Create an issue."""
        client = await self._get_client()

        encoded_id = quote(str(project_id), safe="")

        response = await client.post(
            f"{self._api_url}/projects/{encoded_id}/issues",
            json={
                "title": title,
                "description": description,
            },
        )

        if response.status_code not in (200, 201):
            raise Exception(f"GitLab create_issue failed: {response.text}")

        return response.json()

    async def create_mr(
        self,
        project_id: int | str,
        title: str,
        source_branch: str,
        target_branch: str,
        description: str = "",
    ) -> dict:
        """Create a merge request."""
        client = await self._get_client()

        encoded_id = quote(str(project_id), safe="")

        response = await client.post(
            f"{self._api_url}/projects/{encoded_id}/merge_requests",
            json={
                "title": title,
                "source_branch": source_branch,
                "target_branch": target_branch,
                "description": description,
            },
        )

        if response.status_code not in (200, 201):
            raise Exception(f"GitLab create_mr failed: {response.text}")

        return response.json()

    async def get_pipeline_status(
        self,
        project_id: int | str,
        pipeline_id: int,
    ) -> dict:
        """Get pipeline status."""
        client = await self._get_client()

        encoded_id = quote(str(project_id), safe="")

        response = await client.get(
            f"{self._api_url}/projects/{encoded_id}/pipelines/{pipeline_id}"
        )

        if response.status_code != 200:
            raise Exception(f"GitLab get_pipeline_status failed: {response.text}")

        return response.json()

    async def trigger_pipeline(
        self,
        project_id: int | str,
        ref: str,
        variables: dict | None = None,
    ) -> dict:
        """Trigger a new pipeline."""
        client = await self._get_client()

        encoded_id = quote(str(project_id), safe="")

        payload = {"ref": ref}
        if variables:
            payload["variables"] = [
                {"key": k, "value": v} for k, v in variables.items()
            ]

        response = await client.post(
            f"{self._api_url}/projects/{encoded_id}/pipeline",
            json=payload,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"GitLab trigger_pipeline failed: {response.text}")

        return response.json()

    async def list_commits(
        self,
        project_id: int | str,
        ref: str = "main",
    ) -> list[dict]:
        """List commits for a branch."""
        client = await self._get_client()

        encoded_id = quote(str(project_id), safe="")

        response = await client.get(
            f"{self._api_url}/projects/{encoded_id}/repository/commits",
            params={"ref_name": ref, "per_page": 100},
        )

        if response.status_code != 200:
            raise Exception(f"GitLab list_commits failed: {response.text}")

        return response.json()

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read a project."""
        client = await self._get_client()

        encoded_id = quote(str(resource_id), safe="")
        response = await client.get(f"{self._api_url}/projects/{encoded_id}")

        if response.status_code != 200:
            raise Exception(f"GitLab read project failed: {response.text}")

        return response.json()

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Create an issue or MR."""
        if resource_type == "mr" or resource_type == "merge_request":
            return await self.create_mr(
                project_id=data.get("project_id"),
                title=data.get("title"),
                source_branch=data.get("source_branch"),
                target_branch=data.get("target_branch"),
                description=data.get("description", ""),
            )
        return await self.create_issue(
            project_id=data.get("project_id"),
            title=data.get("title", "Untitled"),
            description=data.get("description", ""),
        )

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List projects or issues."""
        if resource_type == "projects":
            return await self.list_projects(filters.get("search"))
        elif resource_type == "issues":
            return await self.list_issues(
                project_id=filters.get("project_id"),
                state=filters.get("state", "opened"),
            )
        elif resource_type == "commits":
            return await self.list_commits(
                project_id=filters.get("project_id"),
                ref=filters.get("ref", "main"),
            )
        return []

    async def _search_impl(self, query: str, **kwargs) -> list[dict]:
        """Search projects."""
        return await self.list_projects(search=query)

    async def _action_impl(self, action_name: str, params: dict) -> dict:
        """Execute GitLab-specific actions."""
        if action_name == "trigger_pipeline":
            return await self.trigger_pipeline(
                project_id=params.get("project_id"),
                ref=params.get("ref", "main"),
                variables=params.get("variables"),
            )

        raise ValueError(f"Unknown action: {action_name}")
