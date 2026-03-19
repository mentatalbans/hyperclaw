"""LinearConnector — Linear API integration."""

from __future__ import annotations

import logging

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)

logger = logging.getLogger("hyperclaw.integrations.linear")


class LinearConnector(BaseConnector):
    """
    Linear API connector using GraphQL.

    Required config:
        - api_key: Linear API key
    """

    BASE_URL = "https://api.linear.app/graphql"

    def __init__(self, config: dict) -> None:
        self.config = config
        self.api_key = config.get("api_key", "")

        if config.get("enabled", False) and not self.api_key:
            raise ValueError("LinearConnector requires api_key")

        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="linear",
            platform="linear",
            category="productivity",
            capabilities=frozenset(
                [
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.LIST_DATA,
                    ConnectorCapability.SEARCH,
                    ConnectorCapability.WEBHOOKS,
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
                    "Authorization": self.api_key,
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def _graphql(self, query: str, variables: dict | None = None) -> dict:
        """Execute a GraphQL query."""
        client = await self._get_client()

        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        response = await client.post(self.BASE_URL, json=payload)

        if response.status_code != 200:
            raise Exception(f"Linear GraphQL failed: {response.text}")

        data = response.json()
        if "errors" in data:
            raise Exception(f"Linear GraphQL errors: {data['errors']}")

        return data.get("data", {})

    async def health(self) -> bool:
        """Check if Linear API is accessible."""
        try:
            await self._graphql("query { viewer { id } }")
            return True
        except Exception as e:
            logger.error(f"Linear health check failed: {e}")
            return False

    async def list_issues(
        self,
        team_id: str | None = None,
        state: str | None = None,
        first: int = 50,
    ) -> list[dict]:
        """List issues."""
        filters = []
        if team_id:
            filters.append(f'team: {{ id: {{ eq: "{team_id}" }} }}')
        if state:
            filters.append(f'state: {{ name: {{ eq: "{state}" }} }}')

        filter_str = ", ".join(filters)
        filter_clause = f"filter: {{ {filter_str} }}" if filters else ""

        query = f"""
        query {{
            issues({filter_clause}, first: {first}) {{
                nodes {{
                    id
                    title
                    description
                    priority
                    state {{ name }}
                    assignee {{ name }}
                    team {{ name }}
                }}
            }}
        }}
        """

        data = await self._graphql(query)
        return data.get("issues", {}).get("nodes", [])

    async def create_issue(
        self,
        title: str,
        description: str = "",
        team_id: str | None = None,
        priority: int | None = None,
    ) -> dict:
        """Create a new issue."""
        mutation = """
        mutation CreateIssue($input: IssueCreateInput!) {
            issueCreate(input: $input) {
                success
                issue {
                    id
                    title
                    identifier
                }
            }
        }
        """

        input_data = {"title": title}
        if description:
            input_data["description"] = description
        if team_id:
            input_data["teamId"] = team_id
        if priority is not None:
            input_data["priority"] = priority

        data = await self._graphql(mutation, {"input": input_data})
        return data.get("issueCreate", {}).get("issue", {})

    async def update_issue(
        self,
        issue_id: str,
        fields: dict,
    ) -> dict:
        """Update an issue."""
        mutation = """
        mutation UpdateIssue($id: String!, $input: IssueUpdateInput!) {
            issueUpdate(id: $id, input: $input) {
                success
                issue {
                    id
                    title
                }
            }
        }
        """

        data = await self._graphql(mutation, {"id": issue_id, "input": fields})
        return data.get("issueUpdate", {}).get("issue", {})

    async def add_comment(
        self,
        issue_id: str,
        body: str,
    ) -> dict:
        """Add a comment to an issue."""
        mutation = """
        mutation CreateComment($input: CommentCreateInput!) {
            commentCreate(input: $input) {
                success
                comment {
                    id
                    body
                }
            }
        }
        """

        data = await self._graphql(mutation, {"input": {"issueId": issue_id, "body": body}})
        return data.get("commentCreate", {}).get("comment", {})

    async def set_priority(
        self,
        issue_id: str,
        priority: int,
    ) -> dict:
        """Set issue priority (0=none, 1=urgent, 2=high, 3=normal, 4=low)."""
        return await self.update_issue(issue_id, {"priority": priority})

    async def assign_issue(
        self,
        issue_id: str,
        assignee_id: str,
    ) -> dict:
        """Assign an issue to a user."""
        return await self.update_issue(issue_id, {"assigneeId": assignee_id})

    async def search_issues(self, query: str) -> list[dict]:
        """Search for issues."""
        gql = """
        query SearchIssues($query: String!) {
            issueSearch(query: $query, first: 50) {
                nodes {
                    id
                    title
                    description
                    identifier
                    state { name }
                }
            }
        }
        """

        data = await self._graphql(gql, {"query": query})
        return data.get("issueSearch", {}).get("nodes", [])

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read an issue."""
        query = """
        query GetIssue($id: String!) {
            issue(id: $id) {
                id
                title
                description
                priority
                state { name }
                assignee { name }
                team { name }
            }
        }
        """

        data = await self._graphql(query, {"id": resource_id})
        return data.get("issue", {})

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Create an issue."""
        return await self.create_issue(
            title=data.get("title", "Untitled"),
            description=data.get("description", ""),
            team_id=data.get("team_id"),
            priority=data.get("priority"),
        )

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List issues."""
        return await self.list_issues(
            team_id=filters.get("team_id"),
            state=filters.get("state"),
            first=filters.get("first", 50),
        )

    async def _search_impl(self, query: str, **kwargs) -> list[dict]:
        """Search issues."""
        return await self.search_issues(query)
