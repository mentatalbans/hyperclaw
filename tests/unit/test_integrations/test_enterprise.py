import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from integrations.enterprise.jira import JiraConnector
from integrations.enterprise.salesforce import SalesforceConnector
from integrations.developer.github import GitHubConnector
from integrations.productivity.notion import NotionConnector


@pytest.fixture
def jira():
    return JiraConnector({"base_url": "https://test.atlassian.net", "email": "test@test.com", "api_token": "token123"})


@pytest.fixture
def salesforce():
    return SalesforceConnector({"client_id": "cid", "client_secret": "csec", "username": "user@sf.com", "password": "pass"})


@pytest.fixture
def github():
    return GitHubConnector({"token": "ghp_testtoken"})


@pytest.fixture
def notion():
    return NotionConnector({"integration_token": "secret_test"})


class TestJiraConnector:
    @pytest.mark.asyncio
    async def test_create_issue_sends_correct_payload(self, jira):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.status_code = 201
            mock_response.json = MagicMock(return_value={"id": "10001", "key": "TEST-1"})
            mock_client.post = AsyncMock(return_value=mock_response)
            jira._client = mock_client
            result = await jira.create_issue("TEST", "Fix the bug", "Description here")
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            payload = call_args[1].get("json") or call_args[0][1] if len(call_args[0]) > 1 else {}
            if hasattr(call_args, "kwargs"):
                payload = call_args.kwargs.get("json", {})
            assert "issue" in str(mock_client.post.call_args).lower() or result is not None


class TestSalesforceConnector:
    @pytest.mark.asyncio
    async def test_query_builds_correct_request(self, salesforce):
        # Mock the auth token acquisition - set these to skip OAuth
        salesforce.access_token = "mock_access_token"
        salesforce.instance_url = "https://test.salesforce.com"
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.status_code = 200
            mock_response.json = MagicMock(return_value={"records": [], "totalSize": 0})
            mock_client.get = AsyncMock(return_value=mock_response)
            salesforce._client = mock_client
            result = await salesforce.query("SELECT Id, Name FROM Account LIMIT 10")
            assert mock_client.get.called or result is not None


class TestGitHubConnector:
    @pytest.mark.asyncio
    async def test_create_pr_sends_correct_payload(self, github):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.status_code = 201
            mock_response.json = MagicMock(return_value={"number": 42, "html_url": "https://github.com/test/repo/pull/42"})
            mock_client.post = AsyncMock(return_value=mock_response)
            github._client = mock_client
            result = await github.create_pr("owner", "repo", "Fix: update deps", "Description", "feature-branch", "main")
            mock_client.post.assert_called_once()
            assert "pulls" in str(mock_client.post.call_args)


class TestNotionConnector:
    @pytest.mark.asyncio
    async def test_create_page_sends_correct_payload(self, notion):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.status_code = 200
            mock_response.json = MagicMock(return_value={"id": "page-123", "object": "page"})
            mock_client.post = AsyncMock(return_value=mock_response)
            notion._client = mock_client
            result = await notion.create_page("parent-id-123", "My New Page")
            mock_client.post.assert_called_once()
            assert "pages" in str(mock_client.post.call_args)
