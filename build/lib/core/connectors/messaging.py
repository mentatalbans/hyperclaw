"""
Additional Messaging Platform Connectors.
Integration with Google Chat, Signal, Matrix, iMessage, LINE, IRC, WeChat, Mattermost.
"""
from __future__ import annotations

import os
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ChatMessage:
    """Chat message."""
    id: str
    text: str
    sender: str | None = None
    channel: str | None = None
    timestamp: datetime | None = None
    raw: dict = field(default_factory=dict)


class MessagingConnector(ABC):
    """Abstract base for messaging connectors."""

    name: str = "base"

    @abstractmethod
    async def send_message(self, channel: str, text: str, **kwargs) -> ChatMessage:
        """Send a message."""
        pass

    @abstractmethod
    async def get_messages(self, channel: str, limit: int = 50, **kwargs) -> list[ChatMessage]:
        """Get recent messages from a channel."""
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# GOOGLE CHAT
# ═══════════════════════════════════════════════════════════════════════════════

class GoogleChatConnector(MessagingConnector):
    """Google Chat connector."""

    name = "google_chat"

    def __init__(self, webhook_url: str | None = None, access_token: str | None = None):
        self.webhook_url = webhook_url or os.environ.get("GOOGLE_CHAT_WEBHOOK")
        self.access_token = access_token or os.environ.get("GOOGLE_CHAT_TOKEN")
        self.base_url = "https://chat.googleapis.com/v1"

    async def send_message(self, channel: str, text: str, **kwargs) -> ChatMessage:
        # If webhook URL is provided, use that (simpler)
        if self.webhook_url and not channel:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.webhook_url,
                    json={"text": text},
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()
                return ChatMessage(
                    id=data.get("name", ""),
                    text=text,
                    raw=data,
                )

        # Otherwise use the full API
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/{channel}/messages",
                headers={"Authorization": f"Bearer {self.access_token}"},
                json={"text": text},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return ChatMessage(
                id=data["name"],
                text=text,
                sender=data.get("sender", {}).get("displayName"),
                channel=channel,
                raw=data,
            )

    async def get_messages(self, channel: str, limit: int = 50, **kwargs) -> list[ChatMessage]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/{channel}/messages",
                headers={"Authorization": f"Bearer {self.access_token}"},
                params={"pageSize": limit},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return [
                ChatMessage(
                    id=msg["name"],
                    text=msg.get("text", ""),
                    sender=msg.get("sender", {}).get("displayName"),
                    channel=channel,
                    timestamp=datetime.fromisoformat(msg["createTime"].replace("Z", "+00:00"))
                    if msg.get("createTime") else None,
                    raw=msg,
                )
                for msg in data.get("messages", [])
            ]


# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL (via signal-cli-rest-api)
# ═══════════════════════════════════════════════════════════════════════════════

class SignalConnector(MessagingConnector):
    """Signal messenger connector (via signal-cli-rest-api)."""

    name = "signal"

    def __init__(
        self,
        api_url: str | None = None,
        phone_number: str | None = None,
    ):
        self.api_url = (api_url or os.environ.get("SIGNAL_API_URL", "http://localhost:8080")).rstrip("/")
        self.phone_number = phone_number or os.environ.get("SIGNAL_PHONE_NUMBER")

    async def send_message(self, channel: str, text: str, **kwargs) -> ChatMessage:
        """Send message to a phone number or group."""
        async with httpx.AsyncClient() as client:
            payload = {
                "message": text,
                "number": self.phone_number,
            }

            if channel.startswith("group."):
                payload["recipients"] = [channel]
            else:
                payload["recipients"] = [channel]

            response = await client.post(
                f"{self.api_url}/v2/send",
                json=payload,
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return ChatMessage(
                id=str(data.get("timestamp", "")),
                text=text,
                channel=channel,
                raw=data,
            )

    async def get_messages(self, channel: str, limit: int = 50, **kwargs) -> list[ChatMessage]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.api_url}/v1/receive/{self.phone_number}",
                timeout=30.0,
            )
            response.raise_for_status()
            messages = response.json()

            return [
                ChatMessage(
                    id=str(msg.get("timestamp", "")),
                    text=msg.get("text", ""),
                    sender=msg.get("source"),
                    timestamp=datetime.fromtimestamp(msg["timestamp"] / 1000)
                    if msg.get("timestamp") else None,
                    raw=msg,
                )
                for msg in messages[:limit]
            ]


# ═══════════════════════════════════════════════════════════════════════════════
# MATRIX
# ═══════════════════════════════════════════════════════════════════════════════

class MatrixConnector(MessagingConnector):
    """Matrix protocol connector."""

    name = "matrix"

    def __init__(
        self,
        homeserver: str | None = None,
        access_token: str | None = None,
        user_id: str | None = None,
    ):
        self.homeserver = (homeserver or os.environ.get("MATRIX_HOMESERVER", "https://matrix.org")).rstrip("/")
        self.access_token = access_token or os.environ.get("MATRIX_ACCESS_TOKEN")
        self.user_id = user_id or os.environ.get("MATRIX_USER_ID")

    async def send_message(self, channel: str, text: str, **kwargs) -> ChatMessage:
        import time

        txn_id = str(int(time.time() * 1000))

        async with httpx.AsyncClient() as client:
            response = await client.put(
                f"{self.homeserver}/_matrix/client/r0/rooms/{channel}/send/m.room.message/{txn_id}",
                headers={"Authorization": f"Bearer {self.access_token}"},
                json={
                    "msgtype": "m.text",
                    "body": text,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return ChatMessage(
                id=data["event_id"],
                text=text,
                sender=self.user_id,
                channel=channel,
                raw=data,
            )

    async def get_messages(self, channel: str, limit: int = 50, **kwargs) -> list[ChatMessage]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.homeserver}/_matrix/client/r0/rooms/{channel}/messages",
                headers={"Authorization": f"Bearer {self.access_token}"},
                params={"dir": "b", "limit": limit},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return [
                ChatMessage(
                    id=event["event_id"],
                    text=event.get("content", {}).get("body", ""),
                    sender=event.get("sender"),
                    channel=channel,
                    timestamp=datetime.fromtimestamp(event["origin_server_ts"] / 1000)
                    if event.get("origin_server_ts") else None,
                    raw=event,
                )
                for event in data.get("chunk", [])
                if event.get("type") == "m.room.message"
            ]


# ═══════════════════════════════════════════════════════════════════════════════
# iMESSAGE (via BlueBubbles)
# ═══════════════════════════════════════════════════════════════════════════════

class iMessageConnector(MessagingConnector):
    """iMessage connector via BlueBubbles server."""

    name = "imessage"

    def __init__(
        self,
        server_url: str | None = None,
        password: str | None = None,
    ):
        self.server_url = (server_url or os.environ.get("BLUEBUBBLES_URL", "http://localhost:1234")).rstrip("/")
        self.password = password or os.environ.get("BLUEBUBBLES_PASSWORD")

    async def send_message(self, channel: str, text: str, **kwargs) -> ChatMessage:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.server_url}/api/v1/message/text",
                params={"password": self.password},
                json={
                    "chatGuid": channel,
                    "message": text,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return ChatMessage(
                id=data.get("data", {}).get("guid", ""),
                text=text,
                channel=channel,
                raw=data,
            )

    async def get_messages(self, channel: str, limit: int = 50, **kwargs) -> list[ChatMessage]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.server_url}/api/v1/chat/{channel}/message",
                params={"password": self.password, "limit": limit},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return [
                ChatMessage(
                    id=msg.get("guid", ""),
                    text=msg.get("text", ""),
                    sender=msg.get("handle", {}).get("address"),
                    channel=channel,
                    timestamp=datetime.fromtimestamp(msg["dateCreated"] / 1000)
                    if msg.get("dateCreated") else None,
                    raw=msg,
                )
                for msg in data.get("data", [])
            ]


# ═══════════════════════════════════════════════════════════════════════════════
# LINE
# ═══════════════════════════════════════════════════════════════════════════════

class LINEConnector(MessagingConnector):
    """LINE Messaging API connector."""

    name = "line"

    def __init__(self, channel_access_token: str | None = None):
        self.token = channel_access_token or os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
        self.base_url = "https://api.line.me/v2"

    async def send_message(self, channel: str, text: str, **kwargs) -> ChatMessage:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/bot/message/push",
                headers={"Authorization": f"Bearer {self.token}"},
                json={
                    "to": channel,
                    "messages": [{"type": "text", "text": text}],
                },
                timeout=30.0,
            )
            response.raise_for_status()

            return ChatMessage(
                id="",  # LINE doesn't return message ID for push
                text=text,
                channel=channel,
                raw={},
            )

    async def get_messages(self, channel: str, limit: int = 50, **kwargs) -> list[ChatMessage]:
        # LINE doesn't provide message history API for bots
        return []

    async def reply(self, reply_token: str, text: str) -> ChatMessage:
        """Reply to a webhook event."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/bot/message/reply",
                headers={"Authorization": f"Bearer {self.token}"},
                json={
                    "replyToken": reply_token,
                    "messages": [{"type": "text", "text": text}],
                },
                timeout=30.0,
            )
            response.raise_for_status()

            return ChatMessage(id="", text=text, raw={})


# ═══════════════════════════════════════════════════════════════════════════════
# IRC
# ═══════════════════════════════════════════════════════════════════════════════

class IRCConnector(MessagingConnector):
    """IRC connector (via IRC bouncer or bridge)."""

    name = "irc"

    def __init__(
        self,
        server: str | None = None,
        port: int = 6667,
        nickname: str | None = None,
        password: str | None = None,
    ):
        self.server = server or os.environ.get("IRC_SERVER")
        self.port = int(os.environ.get("IRC_PORT", port))
        self.nickname = nickname or os.environ.get("IRC_NICKNAME", "HyperClaw")
        self.password = password or os.environ.get("IRC_PASSWORD")
        self._socket = None

    async def send_message(self, channel: str, text: str, **kwargs) -> ChatMessage:
        import asyncio

        reader, writer = await asyncio.open_connection(self.server, self.port)

        try:
            # Connect and identify
            if self.password:
                writer.write(f"PASS {self.password}\r\n".encode())
            writer.write(f"NICK {self.nickname}\r\n".encode())
            writer.write(f"USER {self.nickname} 0 * :{self.nickname}\r\n".encode())
            await writer.drain()

            # Wait for connection
            await asyncio.sleep(2)

            # Send message
            writer.write(f"PRIVMSG {channel} :{text}\r\n".encode())
            await writer.drain()

            # Disconnect
            writer.write(b"QUIT\r\n")
            await writer.drain()

        finally:
            writer.close()
            await writer.wait_closed()

        return ChatMessage(id="", text=text, channel=channel, raw={})

    async def get_messages(self, channel: str, limit: int = 50, **kwargs) -> list[ChatMessage]:
        # IRC doesn't have message history (need to use IRC bouncer logs)
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# WECHAT
# ═══════════════════════════════════════════════════════════════════════════════

class WeChatConnector(MessagingConnector):
    """WeChat Official Account connector."""

    name = "wechat"

    def __init__(
        self,
        app_id: str | None = None,
        app_secret: str | None = None,
    ):
        self.app_id = app_id or os.environ.get("WECHAT_APP_ID")
        self.app_secret = app_secret or os.environ.get("WECHAT_APP_SECRET")
        self.access_token: str | None = None
        self.base_url = "https://api.weixin.qq.com/cgi-bin"

    async def _ensure_token(self) -> str:
        if self.access_token:
            return self.access_token

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/token",
                params={
                    "grant_type": "client_credential",
                    "appid": self.app_id,
                    "secret": self.app_secret,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            self.access_token = data["access_token"]
            return self.access_token

    async def send_message(self, channel: str, text: str, **kwargs) -> ChatMessage:
        token = await self._ensure_token()

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/message/custom/send",
                params={"access_token": token},
                json={
                    "touser": channel,
                    "msgtype": "text",
                    "text": {"content": text},
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return ChatMessage(id="", text=text, channel=channel, raw=data)

    async def get_messages(self, channel: str, limit: int = 50, **kwargs) -> list[ChatMessage]:
        # WeChat doesn't provide message history API
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# MATTERMOST
# ═══════════════════════════════════════════════════════════════════════════════

class MattermostConnector(MessagingConnector):
    """Mattermost connector."""

    name = "mattermost"

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
    ):
        self.url = (url or os.environ.get("MATTERMOST_URL", "")).rstrip("/")
        self.token = token or os.environ.get("MATTERMOST_TOKEN")

    async def send_message(self, channel: str, text: str, **kwargs) -> ChatMessage:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.url}/api/v4/posts",
                headers={"Authorization": f"Bearer {self.token}"},
                json={
                    "channel_id": channel,
                    "message": text,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return ChatMessage(
                id=data["id"],
                text=text,
                sender=data.get("user_id"),
                channel=channel,
                timestamp=datetime.fromtimestamp(data["create_at"] / 1000)
                if data.get("create_at") else None,
                raw=data,
            )

    async def get_messages(self, channel: str, limit: int = 50, **kwargs) -> list[ChatMessage]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.url}/api/v4/channels/{channel}/posts",
                headers={"Authorization": f"Bearer {self.token}"},
                params={"per_page": limit},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            posts = [data["posts"][pid] for pid in data.get("order", [])]

            return [
                ChatMessage(
                    id=post["id"],
                    text=post.get("message", ""),
                    sender=post.get("user_id"),
                    channel=channel,
                    timestamp=datetime.fromtimestamp(post["create_at"] / 1000)
                    if post.get("create_at") else None,
                    raw=post,
                )
                for post in posts
            ]


# ═══════════════════════════════════════════════════════════════════════════════
# PROVIDER FACTORY
# ═══════════════════════════════════════════════════════════════════════════════

MESSAGING_CONNECTORS: dict[str, type[MessagingConnector]] = {
    "google_chat": GoogleChatConnector,
    "gchat": GoogleChatConnector,
    "signal": SignalConnector,
    "matrix": MatrixConnector,
    "imessage": iMessageConnector,
    "line": LINEConnector,
    "irc": IRCConnector,
    "wechat": WeChatConnector,
    "mattermost": MattermostConnector,
}


def get_messaging_connector(name: str, **kwargs) -> MessagingConnector:
    """Get a messaging connector by name."""
    name = name.lower()

    if name not in MESSAGING_CONNECTORS:
        available = ", ".join(sorted(MESSAGING_CONNECTORS.keys()))
        raise ValueError(f"Unknown messaging connector: {name}. Available: {available}")

    return MESSAGING_CONNECTORS[name](**kwargs)
