"""GmailConnector — Gmail API integration."""

from __future__ import annotations

import base64
import logging
from typing import AsyncIterator

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
    InboundMessage,
    OutboundMessage,
)
from integrations.productivity.google.base import GoogleOAuthBase

logger = logging.getLogger("hyperclaw.integrations.gmail")


class GmailConnector(BaseConnector, GoogleOAuthBase):
    """
    Gmail API connector.

    Required config (via GoogleOAuthBase):
        - access_token or credentials_file
    """

    BASE_URL = "https://gmail.googleapis.com/gmail/v1"

    def __init__(self, config: dict) -> None:
        self.config = config
        self._init_google_auth(config)
        self._validate_google_config(config)
        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="gmail",
            platform="gmail",
            category="productivity",
            capabilities=frozenset(
                [
                    ConnectorCapability.SEND_MESSAGE,
                    ConnectorCapability.RECEIVE_MESSAGE,
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.DELETE_DATA,
                    ConnectorCapability.LIST_DATA,
                    ConnectorCapability.SEARCH,
                    ConnectorCapability.FILE_UPLOAD,
                    ConnectorCapability.FILE_DOWNLOAD,
                ]
            ),
            auth_type="oauth2",
            rate_limit_per_minute=250,
            supports_threads=True,
            supports_attachments=True,
            supports_reactions=False,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        return await self._get_google_client()

    async def health(self) -> bool:
        """Check if Gmail API is accessible."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.BASE_URL}/users/me/profile")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Gmail health check failed: {e}")
            return False

    async def _send_impl(self, message: OutboundMessage) -> str:
        """Send an email via Gmail."""
        return await self.send_email(
            to=message.recipient_id,
            subject="Message",
            body=message.content,
        )

    async def _receive_impl(self) -> AsyncIterator[InboundMessage]:
        """Receive emails from inbox."""
        messages = await self.list_inbox(max_results=20)

        client = await self._get_client()

        for msg_ref in messages:
            response = await client.get(
                f"{self.BASE_URL}/users/me/messages/{msg_ref['id']}"
            )

            if response.status_code != 200:
                continue

            msg = response.json()
            headers = {
                h["name"]: h["value"]
                for h in msg.get("payload", {}).get("headers", [])
            }

            yield InboundMessage(
                message_id=msg.get("id", ""),
                platform="gmail",
                sender_id=headers.get("From", ""),
                sender_name=headers.get("From", ""),
                content=msg.get("snippet", ""),
                thread_id=msg.get("threadId"),
                raw=msg,
            )

    async def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        attachments: list[str] | None = None,
    ) -> str:
        """Send an email."""
        client = await self._get_client()

        # Build raw email
        email_lines = [
            f"To: {to}",
            f"Subject: {subject}",
            "Content-Type: text/plain; charset=utf-8",
            "",
            body,
        ]
        email_content = "\r\n".join(email_lines)
        raw = base64.urlsafe_b64encode(email_content.encode()).decode()

        response = await client.post(
            f"{self.BASE_URL}/users/me/messages/send",
            json={"raw": raw},
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Gmail send failed: {response.text}")

        return response.json().get("id", "")

    async def list_inbox(
        self,
        max_results: int = 20,
        query: str = "",
    ) -> list[dict]:
        """List inbox messages."""
        client = await self._get_client()

        params = {"maxResults": max_results, "labelIds": "INBOX"}
        if query:
            params["q"] = query

        response = await client.get(
            f"{self.BASE_URL}/users/me/messages",
            params=params,
        )

        if response.status_code != 200:
            raise Exception(f"Gmail list failed: {response.text}")

        return response.json().get("messages", [])

    async def search_emails(self, query: str) -> list[dict]:
        """Search emails."""
        return await self.list_inbox(max_results=50, query=query)

    async def get_thread(self, thread_id: str) -> dict:
        """Get an email thread."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/users/me/threads/{thread_id}"
        )

        if response.status_code != 200:
            raise Exception(f"Gmail get thread failed: {response.text}")

        return response.json()

    async def create_draft(
        self,
        to: str,
        subject: str,
        body: str,
    ) -> str:
        """Create an email draft."""
        client = await self._get_client()

        email_lines = [f"To: {to}", f"Subject: {subject}", "", body]
        email_content = "\r\n".join(email_lines)
        raw = base64.urlsafe_b64encode(email_content.encode()).decode()

        response = await client.post(
            f"{self.BASE_URL}/users/me/drafts",
            json={"message": {"raw": raw}},
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Gmail create draft failed: {response.text}")

        return response.json().get("id", "")

    async def mark_read(self, message_id: str) -> bool:
        """Mark a message as read."""
        client = await self._get_client()

        response = await client.post(
            f"{self.BASE_URL}/users/me/messages/{message_id}/modify",
            json={"removeLabelIds": ["UNREAD"]},
        )

        return response.status_code == 200

    async def add_label(self, message_id: str, label: str) -> bool:
        """Add a label to a message."""
        client = await self._get_client()

        response = await client.post(
            f"{self.BASE_URL}/users/me/messages/{message_id}/modify",
            json={"addLabelIds": [label]},
        )

        return response.status_code == 200

    async def create_filter(self, criteria: dict, action: dict) -> str:
        """Create an email filter."""
        client = await self._get_client()

        response = await client.post(
            f"{self.BASE_URL}/users/me/settings/filters",
            json={"criteria": criteria, "action": action},
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Gmail create filter failed: {response.text}")

        return response.json().get("id", "")

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read a message."""
        client = await self._get_client()

        response = await client.get(
            f"{self.BASE_URL}/users/me/messages/{resource_id}"
        )

        if response.status_code != 200:
            raise Exception(f"Gmail read failed: {response.text}")

        return response.json()

    async def _delete_impl(self, resource_id: str, **kwargs) -> bool:
        """Delete a message."""
        client = await self._get_client()

        response = await client.delete(
            f"{self.BASE_URL}/users/me/messages/{resource_id}"
        )

        return response.status_code == 204

    async def _search_impl(self, query: str, **kwargs) -> list[dict]:
        """Search implementation."""
        return await self.search_emails(query)

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List implementation."""
        return await self.list_inbox(
            max_results=filters.get("max_results", 20),
            query=filters.get("query", ""),
        )
