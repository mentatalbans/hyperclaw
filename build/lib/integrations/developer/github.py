"""GitHubConnector — GitHub API integration."""

from __future__ import annotations

import base64
import logging

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)

logger = logging.getLogger("hyperclaw.integrations.github")


class GitHubConnector(BaseConnector):
    """
    GitHub API connector.

    Required config:
        - token: GitHub Personal Access Token or App token
    """

    BASE_URL = "https://api.github.com"

    def __init__(self, config: dict) -> None:
        self.config = config
        self.token = config.get("token", "")

        if config.get("enabled", False) and not self.token:
            raise ValueError("GitHubConnector requires token")

        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="github",
            platform="github",
            category="developer",
            capabilities=frozenset(
                [
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.LIST_DATA,
                    ConnectorCapability.SEARCH,
                    ConnectorCapability.WEBHOOKS,
                    ConnectorCapability.ACTION,
                ]
            ),
            auth_type="bearer",
            rate_limit_per_minute=60,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        return self._client

    async def health(self) -> bool:
        """Check if GitHub API is accessible."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.BASE_URL}/user")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"GitHub health check failed: {e}")
            return False

    async def list_repos(self, org: str | None = None) -> list[dict]:
        """List repositories."""
        client = await self._get_client()

        if org:
            url = f"{self.BASE_URL}/orgs/{org}/repos"
        else:
            url = f"{self.BASE_URL}/user/repos"

        response = await client.get(url, params={"per_page": 100})

        if response.status_code != 200:
            raise Exception(f"GitHub list repos failed: {response.text}")

        return response.json()

    async def get_repo(self, owner: str, repo: str) -> dict:
        """Get repository details."""
        client = await self._get_client()

        response = await client.get(f"{self.BASE_URL}/repos/{owner}/{repo}")

        if response.status_code != 200:
            raise Exception(f"GitHub get repo failed: {response.text}")

        return response.json()

    async def list_issues(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        labels: list[str] | None = None,
    ) -> list[dict]:
        """List issues."""
        client = await self._get_client()

        params = {"state": state, "per_page": 100}
        if labels:
            params["labels"] = ",".join(labels)

        response = await client.get(
            f"{self.BASE_URL}/repos/{owner}/{repo}/issues",
            params=params,
        )

        if response.status_code != 200:
            raise Exception(f"GitHub list issues failed: {response.text}")

        return response.json()

    async def create_issue(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str = "",
        labels: list[str] | None = None,
    ) -> dict:
        """Create an issue."""
        client = await self._get_client()

        data = {"title": title}
        if body:
            data["body"] = body
        if labels:
            data["labels"] = labels

        response = await client.post(
            f"{self.BASE_URL}/repos/{owner}/{repo}/issues",
            json=data,
        )

        if response.status_code != 201:
            raise Exception(f"GitHub create issue failed: {response.text}")

        return response.json()

    async def create_pr(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> dict:
        """Create a pull request."""
        client = await self._get_client()

        response = await client.post(
            f"{self.BASE_URL}/repos/{owner}/{repo}/pulls",
            json={
                "title": title,
                "body": body,
                "head": head,
                "base": base,
            },
        )

        if response.status_code != 201:
            raise Exception(f"GitHub create PR failed: {response.text}")

        return response.json()

    async def get_pr(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> dict:
        """Get a pull request."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/repos/{owner}/{repo}/pulls/{pr_number}"
        )

        if response.status_code != 200:
            raise Exception(f"GitHub get PR failed: {response.text}")

        return response.json()

    async def list_commits(
        self,
        owner: str,
        repo: str,
        branch: str = "main",
    ) -> list[dict]:
        """List commits."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/repos/{owner}/{repo}/commits",
            params={"sha": branch, "per_page": 100},
        )

        if response.status_code != 200:
            raise Exception(f"GitHub list commits failed: {response.text}")

        return response.json()

    async def get_file_contents(
        self,
        owner: str,
        repo: str,
        path: str,
        branch: str = "main",
    ) -> dict:
        """Get file contents."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/repos/{owner}/{repo}/contents/{path}",
            params={"ref": branch},
        )

        if response.status_code != 200:
            raise Exception(f"GitHub get file failed: {response.text}")

        data = response.json()
        if data.get("content"):
            data["decoded_content"] = base64.b64decode(data["content"]).decode("utf-8")

        return data

    async def create_or_update_file(
        self,
        owner: str,
        repo: str,
        path: str,
        message: str,
        content: str,
        branch: str = "main",
        sha: str | None = None,
    ) -> dict:
        """Create or update a file."""
        client = await self._get_client()

        encoded_content = base64.b64encode(content.encode()).decode()

        data = {
            "message": message,
            "content": encoded_content,
            "branch": branch,
        }
        if sha:
            data["sha"] = sha

        response = await client.put(
            f"{self.BASE_URL}/repos/{owner}/{repo}/contents/{path}",
            json=data,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"GitHub create/update file failed: {response.text}")

        return response.json()

    async def list_workflows(
        self,
        owner: str,
        repo: str,
    ) -> list[dict]:
        """List workflows."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/repos/{owner}/{repo}/actions/workflows"
        )

        if response.status_code != 200:
            raise Exception(f"GitHub list workflows failed: {response.text}")

        return response.json().get("workflows", [])

    async def trigger_workflow(
        self,
        owner: str,
        repo: str,
        workflow_id: str,
        ref: str,
        inputs: dict | None = None,
    ) -> bool:
        """Trigger a workflow dispatch event."""
        client = await self._get_client()

        data = {"ref": ref}
        if inputs:
            data["inputs"] = inputs

        response = await client.post(
            f"{self.BASE_URL}/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches",
            json=data,
        )

        return response.status_code == 204

    async def search_code(self, query: str) -> list[dict]:
        """Search code."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/search/code",
            params={"q": query},
        )

        if response.status_code != 200:
            raise Exception(f"GitHub search code failed: {response.text}")

        return response.json().get("items", [])

    async def search_repos(self, query: str) -> list[dict]:
        """Search repositories."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/search/repositories",
            params={"q": query},
        )

        if response.status_code != 200:
            raise Exception(f"GitHub search repos failed: {response.text}")

        return response.json().get("items", [])

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read a repository."""
        parts = resource_id.split("/")
        if len(parts) >= 2:
            return await self.get_repo(parts[0], parts[1])
        raise ValueError("resource_id must be in format 'owner/repo'")

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Create an issue or PR."""
        if resource_type == "pr":
            return await self.create_pr(
                owner=data.get("owner"),
                repo=data.get("repo"),
                title=data.get("title"),
                body=data.get("body", ""),
                head=data.get("head"),
                base=data.get("base"),
            )
        return await self.create_issue(
            owner=data.get("owner"),
            repo=data.get("repo"),
            title=data.get("title", "Untitled"),
            body=data.get("body", ""),
            labels=data.get("labels"),
        )

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List repos or issues."""
        if resource_type == "repos":
            return await self.list_repos(filters.get("org"))
        elif resource_type == "issues":
            return await self.list_issues(
                owner=filters.get("owner"),
                repo=filters.get("repo"),
                state=filters.get("state", "open"),
                labels=filters.get("labels"),
            )
        return []

    async def _search_impl(self, query: str, **kwargs) -> list[dict]:
        """Search repos or code."""
        search_type = kwargs.get("search_type", "repos")
        if search_type == "code":
            return await self.search_code(query)
        return await self.search_repos(query)

    async def _action_impl(self, action_name: str, params: dict) -> dict:
        """Execute GitHub-specific actions."""
        if action_name == "trigger_workflow":
            success = await self.trigger_workflow(
                owner=params.get("owner"),
                repo=params.get("repo"),
                workflow_id=params.get("workflow_id"),
                ref=params.get("ref"),
                inputs=params.get("inputs"),
            )
            return {"success": success}

        raise ValueError(f"Unknown action: {action_name}")
