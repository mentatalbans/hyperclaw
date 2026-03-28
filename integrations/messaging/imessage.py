"""iMessageConnector — Apple iMessage integration via AppleScript or BlueBubbles."""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import AsyncIterator

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
    InboundMessage,
    OutboundMessage,
)

logger = logging.getLogger("hyperclaw.integrations.imessage")


class iMessageConnector(BaseConnector):
    """
    iMessage connector.

    Two modes:
    1. Native macOS: Uses AppleScript (requires macOS)
    2. BlueBubbles: Uses BlueBubbles server API (any platform)

    Optional config:
        - bluebubbles_url: BlueBubbles server URL
        - bluebubbles_password: BlueBubbles server password
    """

    def __init__(self, config: dict) -> None:
        self.config = config
        self.bluebubbles_url = config.get("bluebubbles_url", "").rstrip("/")
        self.bluebubbles_password = config.get("bluebubbles_password", "")

        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="imessage",
            platform="imessage",
            category="messaging",
            capabilities=frozenset(
                [
                    ConnectorCapability.SEND_MESSAGE,
                    ConnectorCapability.RECEIVE_MESSAGE,
                    ConnectorCapability.FILE_UPLOAD,
                ]
            ),
            auth_type="api_key" if self.bluebubbles_url else "oauth",
            rate_limit_per_minute=30,
            supports_threads=True,
            supports_attachments=True,
            supports_reactions=True,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def health(self) -> bool:
        """
        Check if iMessage is available.
        Returns True on macOS (native) or if BlueBubbles is reachable.
        """
        # If BlueBubbles is configured, check that
        if self.bluebubbles_url:
            try:
                client = await self._get_client()
                response = await client.get(
                    f"{self.bluebubbles_url}/api/v1/server/info",
                    params={"password": self.bluebubbles_password},
                )
                return response.status_code == 200
            except Exception as e:
                logger.error(f"BlueBubbles health check failed: {e}")
                return False

        # Native macOS check
        return sys.platform == "darwin"

    async def _send_impl(self, message: OutboundMessage) -> str:
        """Send a message via iMessage."""
        if self.bluebubbles_url:
            return await self._send_via_bluebubbles(message)
        else:
            return await self._send_via_applescript(message)

    async def _send_via_applescript(self, message: OutboundMessage) -> str:
        """Send via native macOS AppleScript."""
        if sys.platform != "darwin":
            raise RuntimeError("AppleScript iMessage requires macOS")

        # Escape single quotes in the message
        content = message.content.replace("'", "'\\''")
        recipient = message.recipient_id.replace("'", "'\\''")

        script = f'''
        tell application "Messages"
            set targetService to 1st service whose service type = iMessage
            set targetBuddy to buddy "{recipient}" of targetService
            send "{content}" to targetBuddy
        end tell
        '''

        proc = await asyncio.create_subprocess_exec(
            "osascript",
            "-e",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise Exception(f"AppleScript failed: {stderr.decode()}")

        # AppleScript doesn't return message IDs
        return "applescript_sent"

    async def _send_via_bluebubbles(self, message: OutboundMessage) -> str:
        """Send via BlueBubbles server."""
        client = await self._get_client()

        payload = {
            "chatGuid": f"iMessage;-;{message.recipient_id}",
            "message": message.content,
            "method": "apple-script",
        }

        response = await client.post(
            f"{self.bluebubbles_url}/api/v1/message/text",
            params={"password": self.bluebubbles_password},
            json=payload,
        )

        if response.status_code not in (200, 201):
            raise Exception(f"BlueBubbles send failed: {response.text}")

        data = response.json()
        return data.get("data", {}).get("guid", "")

    async def _receive_impl(self) -> AsyncIterator[InboundMessage]:
        """Receive messages via BlueBubbles."""
        if not self.bluebubbles_url:
            # Native macOS doesn't have a polling API
            return
            yield

        client = await self._get_client()

        response = await client.get(
            f"{self.bluebubbles_url}/api/v1/message",
            params={
                "password": self.bluebubbles_password,
                "limit": 50,
                "sort": "desc",
            },
        )

        if response.status_code != 200:
            return

        data = response.json()

        for msg in data.get("data", []):
            if msg.get("isFromMe"):
                continue

            handle = msg.get("handle", {})

            attachments = []
            for attach in msg.get("attachments", []):
                attachments.append({
                    "type": attach.get("mimeType", ""),
                    "id": attach.get("guid", ""),
                    "filename": attach.get("transferName", ""),
                })

            yield InboundMessage(
                message_id=msg.get("guid", ""),
                platform="imessage",
                sender_id=handle.get("address", ""),
                sender_name=handle.get("address", ""),
                content=msg.get("text", ""),
                thread_id=msg.get("chatGuid"),
                attachments=attachments,
                raw=msg,
            )

    async def send_attachment(
        self,
        recipient: str,
        file_path: str,
        message: str = "",
    ) -> str:
        """Send a file attachment."""
        if self.bluebubbles_url:
            client = await self._get_client()

            with open(file_path, "rb") as f:
                files = {"attachment": f}
                data = {
                    "chatGuid": f"iMessage;-;{recipient}",
                    "message": message,
                }

                response = await client.post(
                    f"{self.bluebubbles_url}/api/v1/message/attachment",
                    params={"password": self.bluebubbles_password},
                    data=data,
                    files=files,
                )

            if response.status_code not in (200, 201):
                raise Exception(f"BlueBubbles attachment failed: {response.text}")

            result = response.json()
            return result.get("data", {}).get("guid", "")

        else:
            # AppleScript attachment sending
            if sys.platform != "darwin":
                raise RuntimeError("AppleScript iMessage requires macOS")

            file_path_escaped = file_path.replace("'", "'\\''")
            recipient_escaped = recipient.replace("'", "'\\''")

            script = f'''
            tell application "Messages"
                set targetService to 1st service whose service type = iMessage
                set targetBuddy to buddy "{recipient_escaped}" of targetService
                set theFile to POSIX file "{file_path_escaped}"
                send theFile to targetBuddy
            end tell
            '''

            proc = await asyncio.create_subprocess_exec(
                "osascript",
                "-e",
                script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                raise Exception(f"AppleScript attachment failed: {stderr.decode()}")

            return "applescript_attachment_sent"
