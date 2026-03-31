"""EmailConnector — Email integration via SMTP or API (Gmail/Outlook)."""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from typing import AsyncIterator
import asyncio

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
    InboundMessage,
    OutboundMessage,
)

logger = logging.getLogger("hyperclaw.integrations.email")


class EmailConnector(BaseConnector):
    """
    Email connector supporting SMTP, Gmail API, and Outlook API.

    Required config:
        - provider: "smtp", "gmail", or "outlook"

    For SMTP:
        - smtp_host, smtp_port, username, password

    For Gmail/Outlook:
        - access_token (OAuth2)
    """

    def __init__(self, config: dict) -> None:
        self.config = config
        self.provider = config.get("provider", "smtp")
        self.smtp_host = config.get("smtp_host", "")
        self.smtp_port = config.get("smtp_port", 587)
        self.username = config.get("username", "")
        self.password = config.get("password", "")
        self.access_token = config.get("access_token", "")

        if config.get("enabled", False):
            if self.provider == "smtp":
                if not self.smtp_host or not self.username or not self.password:
                    raise ValueError(
                        "EmailConnector (SMTP) requires smtp_host, username, password"
                    )
            elif self.provider in ("gmail", "outlook"):
                if not self.access_token:
                    raise ValueError(
                        f"EmailConnector ({self.provider}) requires access_token"
                    )

        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="email",
            platform="email",
            category="messaging",
            capabilities=frozenset(
                [
                    ConnectorCapability.SEND_MESSAGE,
                    ConnectorCapability.RECEIVE_MESSAGE,
                    ConnectorCapability.FILE_UPLOAD,
                    ConnectorCapability.FILE_DOWNLOAD,
                    ConnectorCapability.SEARCH,
                    ConnectorCapability.LIST_DATA,
                ]
            ),
            auth_type="oauth2" if self.provider != "smtp" else "api_key",
            rate_limit_per_minute=100,
            supports_threads=True,
            supports_attachments=True,
            supports_reactions=False,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {}
            if self.access_token:
                headers["Authorization"] = f"Bearer {self.access_token}"
            self._client = httpx.AsyncClient(timeout=30.0, headers=headers)
        return self._client

    async def health(self) -> bool:
        """Check if email service is accessible."""
        try:
            if self.provider == "smtp":
                # Test SMTP connection
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(None, self._test_smtp)
            elif self.provider == "gmail":
                client = await self._get_client()
                response = await client.get(
                    "https://gmail.googleapis.com/gmail/v1/users/me/profile"
                )
                return response.status_code == 200
            elif self.provider == "outlook":
                client = await self._get_client()
                response = await client.get(
                    "https://graph.microsoft.com/v1.0/me"
                )
                return response.status_code == 200
        except Exception as e:
            logger.error(f"Email health check failed: {e}")
            return False

        return False

    def _test_smtp(self) -> bool:
        """Test SMTP connection."""
        try:
            server = smtplib.SMTP(self.smtp_host, self.smtp_port)
            server.starttls()
            server.login(self.username, self.password)
            server.quit()
            return True
        except Exception:
            return False

    async def _send_impl(self, message: OutboundMessage) -> str:
        """Send an email."""
        if self.provider == "smtp":
            return await self._send_smtp(message)
        elif self.provider == "gmail":
            return await self._send_gmail(message)
        elif self.provider == "outlook":
            return await self._send_outlook(message)

        raise ValueError(f"Unknown provider: {self.provider}")

    async def _send_smtp(self, message: OutboundMessage) -> str:
        """Send via SMTP."""
        loop = asyncio.get_event_loop()

        def _send():
            msg = MIMEMultipart()
            msg["From"] = self.username
            msg["To"] = message.recipient_id
            msg["Subject"] = message.attachments[0].get("subject", "No Subject") if message.attachments else "No Subject"

            msg.attach(MIMEText(message.content, "plain"))

            server = smtplib.SMTP(self.smtp_host, self.smtp_port)
            server.starttls()
            server.login(self.username, self.password)
            server.sendmail(self.username, message.recipient_id, msg.as_string())
            server.quit()

            return "smtp_sent"

        return await loop.run_in_executor(None, _send)

    async def _send_gmail(self, message: OutboundMessage) -> str:
        """Send via Gmail API."""
        import base64

        client = await self._get_client()

        # Build raw email
        email_content = f"To: {message.recipient_id}\r\nSubject: Message\r\n\r\n{message.content}"
        raw = base64.urlsafe_b64encode(email_content.encode()).decode()

        response = await client.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            json={"raw": raw},
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Gmail send failed: {response.text}")

        data = response.json()
        return data.get("id", "")

    async def _send_outlook(self, message: OutboundMessage) -> str:
        """Send via Outlook/Graph API."""
        client = await self._get_client()

        payload = {
            "message": {
                "subject": "Message",
                "body": {
                    "contentType": "Text",
                    "content": message.content,
                },
                "toRecipients": [
                    {"emailAddress": {"address": message.recipient_id}}
                ],
            }
        }

        response = await client.post(
            "https://graph.microsoft.com/v1.0/me/sendMail",
            json=payload,
        )

        if response.status_code not in (200, 202):
            raise Exception(f"Outlook send failed: {response.text}")

        return "outlook_sent"

    async def _receive_impl(self) -> AsyncIterator[InboundMessage]:
        """Receive emails (via API providers)."""
        if self.provider == "gmail":
            async for msg in self._receive_gmail():
                yield msg
        elif self.provider == "outlook":
            async for msg in self._receive_outlook():
                yield msg
        else:
            # SMTP receive would require IMAP
            return

    async def _receive_gmail(self) -> AsyncIterator[InboundMessage]:
        """Receive via Gmail API."""
        client = await self._get_client()

        response = await client.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            params={"maxResults": 20},
        )

        if response.status_code != 200:
            return

        data = response.json()

        for msg_ref in data.get("messages", []):
            msg_response = await client.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_ref['id']}"
            )

            if msg_response.status_code != 200:
                continue

            msg = msg_response.json()
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}

            yield InboundMessage(
                message_id=msg.get("id", ""),
                platform="email",
                sender_id=headers.get("From", ""),
                sender_name=headers.get("From", ""),
                content=msg.get("snippet", ""),
                thread_id=msg.get("threadId"),
                raw=msg,
            )

    async def _receive_outlook(self) -> AsyncIterator[InboundMessage]:
        """Receive via Outlook/Graph API."""
        client = await self._get_client()

        response = await client.get(
            "https://graph.microsoft.com/v1.0/me/messages",
            params={"$top": 20},
        )

        if response.status_code != 200:
            return

        data = response.json()

        for msg in data.get("value", []):
            sender = msg.get("from", {}).get("emailAddress", {})

            yield InboundMessage(
                message_id=msg.get("id", ""),
                platform="email",
                sender_id=sender.get("address", ""),
                sender_name=sender.get("name", sender.get("address", "")),
                content=msg.get("bodyPreview", ""),
                thread_id=msg.get("conversationId"),
                raw=msg,
            )

    async def list_inbox(
        self,
        max_results: int = 20,
        query: str = "",
    ) -> list[dict]:
        """List inbox messages."""
        if self.provider == "gmail":
            client = await self._get_client()
            params = {"maxResults": max_results}
            if query:
                params["q"] = query

            response = await client.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                params=params,
            )

            if response.status_code != 200:
                raise Exception(f"Gmail list failed: {response.text}")

            return response.json().get("messages", [])

        elif self.provider == "outlook":
            client = await self._get_client()
            params = {"$top": max_results}
            if query:
                params["$search"] = f'"{query}"'

            response = await client.get(
                "https://graph.microsoft.com/v1.0/me/messages",
                params=params,
            )

            if response.status_code != 200:
                raise Exception(f"Outlook list failed: {response.text}")

            return response.json().get("value", [])

        return []

    async def search_emails(self, query: str) -> list[dict]:
        """Search emails."""
        return await self.list_inbox(max_results=50, query=query)

    async def get_thread(self, thread_id: str) -> dict:
        """Get an email thread."""
        client = await self._get_client()

        if self.provider == "gmail":
            response = await client.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/threads/{thread_id}"
            )
        elif self.provider == "outlook":
            response = await client.get(
                f"https://graph.microsoft.com/v1.0/me/messages",
                params={"$filter": f"conversationId eq '{thread_id}'"},
            )
        else:
            return {}

        if response.status_code != 200:
            raise Exception(f"Get thread failed: {response.text}")

        return response.json()

    async def create_draft(
        self,
        to: str,
        subject: str,
        body: str,
    ) -> str:
        """Create an email draft."""
        client = await self._get_client()

        if self.provider == "gmail":
            import base64

            email_content = f"To: {to}\r\nSubject: {subject}\r\n\r\n{body}"
            raw = base64.urlsafe_b64encode(email_content.encode()).decode()

            response = await client.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
                json={"message": {"raw": raw}},
            )

        elif self.provider == "outlook":
            response = await client.post(
                "https://graph.microsoft.com/v1.0/me/messages",
                json={
                    "subject": subject,
                    "body": {"contentType": "Text", "content": body},
                    "toRecipients": [{"emailAddress": {"address": to}}],
                },
            )
        else:
            raise ValueError("Draft creation requires Gmail or Outlook provider")

        if response.status_code not in (200, 201):
            raise Exception(f"Create draft failed: {response.text}")

        return response.json().get("id", "")

    async def _search_impl(self, query: str, **kwargs) -> list[dict]:
        """Search implementation."""
        return await self.search_emails(query)

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List implementation."""
        if resource_type == "inbox":
            return await self.list_inbox(
                max_results=filters.get("max_results", 20),
                query=filters.get("query", ""),
            )
        return []
