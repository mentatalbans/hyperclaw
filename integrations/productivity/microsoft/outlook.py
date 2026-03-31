"""OutlookConnector — Microsoft Outlook/Mail API integration."""

from __future__ import annotations

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
from integrations.productivity.microsoft.base import MicrosoftOAuthBase

logger = logging.getLogger("hyperclaw.integrations.outlook")


class OutlookConnector(BaseConnector, MicrosoftOAuthBase):
    """
    Microsoft Outlook connector via Graph API.

    Required config (via MicrosoftOAuthBase):
        - access_token OR (client_id + client_secret + tenant_id)
    """

    def __init__(self, config: dict) -> None:
        self.config = config
        self._init_microsoft_auth(config)
        self._validate_microsoft_config(config)

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="outlook",
            platform="outlook",
            category="productivity",
            capabilities=frozenset(
                [
                    ConnectorCapability.SEND_MESSAGE,
                    ConnectorCapability.RECEIVE_MESSAGE,
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.LIST_DATA,
                    ConnectorCapability.SEARCH,
                    ConnectorCapability.FILE_UPLOAD,
                    ConnectorCapability.FILE_DOWNLOAD,
                ]
            ),
            auth_type="oauth2",
            rate_limit_per_minute=120,
            supports_threads=True,
            supports_attachments=True,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        return await self._get_microsoft_client()

    async def health(self) -> bool:
        """Check if Outlook API is accessible."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.GRAPH_URL}/me")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Outlook health check failed: {e}")
            return False

    async def _send_impl(self, message: OutboundMessage) -> str:
        """Send an email via Outlook."""
        return await self.send_email(
            to=message.recipient_id,
            subject="Message",
            body=message.content,
        )

    async def _receive_impl(self) -> AsyncIterator[InboundMessage]:
        """Receive emails from inbox."""
        messages = await self.list_inbox(max_results=20)

        for msg in messages:
            sender = msg.get("from", {}).get("emailAddress", {})

            yield InboundMessage(
                message_id=msg.get("id", ""),
                platform="outlook",
                sender_id=sender.get("address", ""),
                sender_name=sender.get("name", sender.get("address", "")),
                content=msg.get("bodyPreview", ""),
                thread_id=msg.get("conversationId"),
                raw=msg,
            )

    async def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        is_html: bool = False,
    ) -> str:
        """Send an email."""
        client = await self._get_client()

        payload = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": "HTML" if is_html else "Text",
                    "content": body,
                },
                "toRecipients": [{"emailAddress": {"address": to}}],
            }
        }

        response = await client.post(
            f"{self.GRAPH_URL}/me/sendMail",
            json=payload,
        )

        if response.status_code not in (200, 202):
            raise Exception(f"Outlook send failed: {response.text}")

        return "sent"

    async def list_inbox(
        self,
        max_results: int = 20,
        filter_query: str = "",
    ) -> list[dict]:
        """List inbox messages."""
        client = await self._get_client()

        params = {"$top": max_results, "$orderby": "receivedDateTime desc"}
        if filter_query:
            params["$filter"] = filter_query

        response = await client.get(
            f"{self.GRAPH_URL}/me/messages",
            params=params,
        )

        if response.status_code != 200:
            raise Exception(f"Outlook list inbox failed: {response.text}")

        return response.json().get("value", [])

    async def search_emails(self, query: str) -> list[dict]:
        """Search emails."""
        client = await self._get_client()

        response = await client.get(
            f"{self.GRAPH_URL}/me/messages",
            params={"$search": f'"{query}"', "$top": 50},
        )

        if response.status_code != 200:
            raise Exception(f"Outlook search failed: {response.text}")

        return response.json().get("value", [])

    async def get_message(self, message_id: str) -> dict:
        """Get a specific message."""
        client = await self._get_client()

        response = await client.get(f"{self.GRAPH_URL}/me/messages/{message_id}")

        if response.status_code != 200:
            raise Exception(f"Outlook get message failed: {response.text}")

        return response.json()

    async def create_draft(
        self,
        to: str,
        subject: str,
        body: str,
    ) -> str:
        """Create an email draft."""
        client = await self._get_client()

        payload = {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": to}}],
        }

        response = await client.post(
            f"{self.GRAPH_URL}/me/messages",
            json=payload,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Outlook create draft failed: {response.text}")

        return response.json().get("id", "")

    async def move_to_folder(
        self,
        message_id: str,
        folder_id: str,
    ) -> bool:
        """Move a message to a folder."""
        client = await self._get_client()

        response = await client.post(
            f"{self.GRAPH_URL}/me/messages/{message_id}/move",
            json={"destinationId": folder_id},
        )

        return response.status_code == 201

    async def flag_message(self, message_id: str) -> bool:
        """Flag a message for follow-up."""
        client = await self._get_client()

        response = await client.patch(
            f"{self.GRAPH_URL}/me/messages/{message_id}",
            json={"flag": {"flagStatus": "flagged"}},
        )

        return response.status_code == 200

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read a message."""
        return await self.get_message(resource_id)

    async def _search_impl(self, query: str, **kwargs) -> list[dict]:
        """Search implementation."""
        return await self.search_emails(query)

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List implementation."""
        return await self.list_inbox(
            max_results=filters.get("max_results", 20),
            filter_query=filters.get("filter", ""),
        )
