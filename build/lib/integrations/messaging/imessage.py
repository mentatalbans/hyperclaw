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


# =============================================================================
# Standalone Functions for Direct Use
# =============================================================================

def send_imessage(recipient: str, message: str) -> dict:
    """
    Send an iMessage via AppleScript (macOS only).

    Args:
        recipient: Phone number or Apple ID (e.g., "+15551234567" or "user@icloud.com")
        message: The message text to send

    Returns:
        dict with 'success' and 'message' or 'error' keys
    """
    import subprocess

    if sys.platform != "darwin":
        return {"success": False, "error": "iMessage requires macOS"}

    # Escape for AppleScript
    message_escaped = message.replace('\\', '\\\\').replace('"', '\\"')
    recipient_escaped = recipient.replace('\\', '\\\\').replace('"', '\\"')

    # Try direct send first (works for phone numbers)
    script = f'''
    tell application "Messages"
        send "{message_escaped}" to buddy "{recipient_escaped}" of (service 1 whose service type is iMessage)
    end tell
    '''

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            return {"success": True, "message": f"iMessage sent to {recipient}"}

        # Fallback: try participant-based approach
        script2 = f'''
        tell application "Messages"
            set targetService to 1st account whose service type = iMessage
            set targetBuddy to participant "{recipient_escaped}" of targetService
            send "{message_escaped}" to targetBuddy
        end tell
        '''

        result2 = subprocess.run(
            ["osascript", "-e", script2],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result2.returncode == 0:
            return {"success": True, "message": f"iMessage sent to {recipient}"}

        return {"success": False, "error": result.stderr or result2.stderr}

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "AppleScript timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def read_imessages(recipient: str = None, count: int = 10) -> list:
    """
    Read recent iMessages from the local Messages database.

    Args:
        recipient: Optional phone number or Apple ID to filter by
        count: Number of messages to return (default 10)

    Returns:
        List of message dicts with 'sender', 'text', 'date', 'is_from_me' keys
    """
    import sqlite3
    from pathlib import Path
    from datetime import datetime

    if sys.platform != "darwin":
        return [{"error": "iMessage requires macOS"}]

    db_path = Path.home() / "Library" / "Messages" / "chat.db"

    if not db_path.exists():
        return [{"error": "Messages database not found"}]

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Query messages with handle info
        query = """
            SELECT
                m.text,
                m.is_from_me,
                m.date / 1000000000 + 978307200 as unix_date,
                h.id as handle_id
            FROM message m
            LEFT JOIN handle h ON m.handle_id = h.ROWID
            WHERE m.text IS NOT NULL AND m.text != ''
        """

        if recipient:
            # Normalize phone number for matching
            clean_recipient = ''.join(c for c in recipient if c.isdigit() or c == '+')
            query += f" AND (h.id LIKE '%{clean_recipient[-10:]}%' OR h.id = '{recipient}')"

        query += f" ORDER BY m.date DESC LIMIT {count}"

        cursor.execute(query)
        rows = cursor.fetchall()
        conn.close()

        messages = []
        for text, is_from_me, unix_ts, handle in rows:
            try:
                date_str = datetime.fromtimestamp(unix_ts).strftime("%Y-%m-%d %H:%M:%S")
            except:
                date_str = "unknown"

            messages.append({
                "text": text,
                "sender": "me" if is_from_me else (handle or "unknown"),
                "date": date_str,
                "is_from_me": bool(is_from_me)
            })

        return list(reversed(messages))  # Chronological order

    except Exception as e:
        return [{"error": str(e)}]


def imessage_available() -> bool:
    """Check if iMessage is available on this system."""
    if sys.platform != "darwin":
        return False

    from pathlib import Path
    return (Path.home() / "Library" / "Messages" / "chat.db").exists()


if __name__ == "__main__":
    import sys as _sys
    import json

    if len(_sys.argv) < 2:
        print("Usage: imessage.py <send|read|check> [args]")
        print("  send <recipient> <message>  - Send an iMessage")
        print("  read [recipient] [count]    - Read recent messages")
        print("  check                       - Check if iMessage is available")
        _sys.exit(1)

    cmd = _sys.argv[1]

    if cmd == "send":
        if len(_sys.argv) < 4:
            print("Usage: imessage.py send <recipient> <message>")
            _sys.exit(1)
        recipient = _sys.argv[2]
        message = " ".join(_sys.argv[3:])
        result = send_imessage(recipient, message)
        print(json.dumps(result, indent=2))

    elif cmd == "read":
        recipient = _sys.argv[2] if len(_sys.argv) > 2 else None
        count = int(_sys.argv[3]) if len(_sys.argv) > 3 else 10
        messages = read_imessages(recipient, count)
        print(json.dumps(messages, indent=2))

    elif cmd == "check":
        available = imessage_available()
        print(f"iMessage available: {available}")

    else:
        print(f"Unknown command: {cmd}")
