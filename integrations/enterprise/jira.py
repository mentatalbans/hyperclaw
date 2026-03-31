"""JiraConnector — Atlassian Jira API integration."""

from __future__ import annotations

import base64
import logging

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)

logger = logging.getLogger("hyperclaw.integrations.jira")


class JiraConnector(BaseConnector):
    """
    Jira API connector.

    Required config:
        - base_url: Jira instance URL (e.g., https://your-domain.atlassian.net)
        - email: User email for authentication
        - api_token: Jira API token
    """

    def __init__(self, config: dict) -> None:
        self.config = config
        self.base_url = config.get("base_url", "").rstrip("/")
        self.email = config.get("email", "")
        self.api_token = config.get("api_token", "")

        if config.get("enabled", False):
            if not self.base_url:
                raise ValueError("JiraConnector requires base_url")
            if not self.email:
                raise ValueError("JiraConnector requires email")
            if not self.api_token:
                raise ValueError("JiraConnector requires api_token")

        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="jira",
            platform="jira",
            category="enterprise",
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
            auth_type="api_key",
            rate_limit_per_minute=100,
        )

    def _get_auth_header(self) -> str:
        """Generate Basic auth header."""
        credentials = f"{self.email}:{self.api_token}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return f"Basic {encoded}"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={
                    "Authorization": self._get_auth_header(),
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def health(self) -> bool:
        """Check if Jira API is accessible."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.base_url}/rest/api/3/myself")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Jira health check failed: {e}")
            return False

    async def get_issue(self, issue_key: str) -> dict:
        """Get an issue by key."""
        client = await self._get_client()

        response = await client.get(
            f"{self.base_url}/rest/api/3/issue/{issue_key}"
        )

        if response.status_code != 200:
            raise Exception(f"Jira get issue failed: {response.text}")

        return response.json()

    async def create_issue(
        self,
        project_key: str,
        summary: str,
        description: str = "",
        issue_type: str = "Task",
        priority: str = "Medium",
    ) -> dict:
        """Create a new issue."""
        client = await self._get_client()

        fields = {
            "project": {"key": project_key},
            "summary": summary,
            "issuetype": {"name": issue_type},
            "priority": {"name": priority},
        }

        if description:
            fields["description"] = {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": description}],
                    }
                ],
            }

        response = await client.post(
            f"{self.base_url}/rest/api/3/issue",
            json={"fields": fields},
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Jira create issue failed: {response.text}")

        return response.json()

    async def update_issue(
        self,
        issue_key: str,
        fields: dict,
    ) -> bool:
        """Update an issue."""
        client = await self._get_client()

        response = await client.put(
            f"{self.base_url}/rest/api/3/issue/{issue_key}",
            json={"fields": fields},
        )

        return response.status_code == 204

    async def transition_issue(
        self,
        issue_key: str,
        transition_name: str,
    ) -> bool:
        """Transition an issue to a new status."""
        client = await self._get_client()

        # Get available transitions
        transitions_response = await client.get(
            f"{self.base_url}/rest/api/3/issue/{issue_key}/transitions"
        )

        if transitions_response.status_code != 200:
            raise Exception(f"Jira get transitions failed: {transitions_response.text}")

        transitions = transitions_response.json().get("transitions", [])
        transition_id = None

        for t in transitions:
            if t["name"].lower() == transition_name.lower():
                transition_id = t["id"]
                break

        if not transition_id:
            raise ValueError(f"Transition '{transition_name}' not found")

        response = await client.post(
            f"{self.base_url}/rest/api/3/issue/{issue_key}/transitions",
            json={"transition": {"id": transition_id}},
        )

        return response.status_code == 204

    async def add_comment(
        self,
        issue_key: str,
        body: str,
    ) -> dict:
        """Add a comment to an issue."""
        client = await self._get_client()

        response = await client.post(
            f"{self.base_url}/rest/api/3/issue/{issue_key}/comment",
            json={
                "body": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": body}],
                        }
                    ],
                }
            },
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Jira add comment failed: {response.text}")

        return response.json()

    async def list_sprints(self, board_id: str) -> list[dict]:
        """List sprints for a board."""
        client = await self._get_client()

        response = await client.get(
            f"{self.base_url}/rest/agile/1.0/board/{board_id}/sprint"
        )

        if response.status_code != 200:
            raise Exception(f"Jira list sprints failed: {response.text}")

        return response.json().get("values", [])

    async def search_issues(self, jql: str, max_results: int = 50) -> list[dict]:
        """Search issues using JQL."""
        client = await self._get_client()

        response = await client.post(
            f"{self.base_url}/rest/api/3/search",
            json={
                "jql": jql,
                "maxResults": max_results,
            },
        )

        if response.status_code != 200:
            raise Exception(f"Jira search failed: {response.text}")

        return response.json().get("issues", [])

    async def assign_issue(
        self,
        issue_key: str,
        assignee: str,
    ) -> bool:
        """Assign an issue to a user."""
        client = await self._get_client()

        response = await client.put(
            f"{self.base_url}/rest/api/3/issue/{issue_key}/assignee",
            json={"accountId": assignee},
        )

        return response.status_code == 204

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read an issue."""
        return await self.get_issue(resource_id)

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Create an issue."""
        return await self.create_issue(
            project_key=data.get("project_key"),
            summary=data.get("summary", "Untitled"),
            description=data.get("description", ""),
            issue_type=data.get("issue_type", "Task"),
            priority=data.get("priority", "Medium"),
        )

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List or search issues."""
        jql = filters.get("jql", "order by created DESC")
        return await self.search_issues(jql, filters.get("max_results", 50))

    async def _search_impl(self, query: str, **kwargs) -> list[dict]:
        """Search issues."""
        jql = f'text ~ "{query}"'
        return await self.search_issues(jql)

    async def _action_impl(self, action_name: str, params: dict) -> dict:
        """Execute Jira-specific actions."""
        if action_name == "transition":
            success = await self.transition_issue(
                params["issue_key"],
                params["transition_name"],
            )
            return {"success": success}
        elif action_name == "assign":
            success = await self.assign_issue(
                params["issue_key"],
                params["assignee"],
            )
            return {"success": success}
        elif action_name == "comment":
            return await self.add_comment(
                params["issue_key"],
                params["body"],
            )

        raise ValueError(f"Unknown action: {action_name}")
