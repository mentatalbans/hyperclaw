"""TelegramConnector — Telegram Bot API integration."""

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

logger = logging.getLogger("hyperclaw.integrations.telegram")


class TelegramConnector(BaseConnector):
    """
    Telegram Bot API connector.

    Required config:
        - bot_token: Telegram Bot API token
    """

    BASE_URL = "https://api.telegram.org"

    def __init__(self, config: dict) -> None:
        self.config = config
        self.bot_token = config.get("bot_token", "")

        if config.get("enabled", False) and not self.bot_token:
            raise ValueError("TelegramConnector requires bot_token")

        self._last_update_id = 0
        self._client: httpx.AsyncClient | None = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="telegram",
            platform="telegram",
            category="messaging",
            capabilities=frozenset(
                [
                    ConnectorCapability.SEND_MESSAGE,
                    ConnectorCapability.RECEIVE_MESSAGE,
                    ConnectorCapability.FILE_UPLOAD,
                    ConnectorCapability.FILE_DOWNLOAD,
                    ConnectorCapability.WEBHOOKS,
                    ConnectorCapability.NOTIFY,
                    ConnectorCapability.ACTION,
                ]
            ),
            auth_type="api_key",
            rate_limit_per_minute=30,
            supports_threads=True,
            supports_attachments=True,
            supports_reactions=True,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    def _api_url(self, method: str) -> str:
        return f"{self.BASE_URL}/bot{self.bot_token}/{method}"

    async def health(self) -> bool:
        """Check if bot token is valid."""
        try:
            client = await self._get_client()
            response = await client.get(self._api_url("getMe"))
            data = response.json()
            return data.get("ok", False)
        except Exception as e:
            logger.error(f"Telegram health check failed: {e}")
            return False

    async def _send_impl(self, message: OutboundMessage) -> str:
        """Send a message via Telegram."""
        client = await self._get_client()

        payload = {
            "chat_id": message.recipient_id,
            "text": message.content,
        }

        if message.thread_id:
            payload["reply_to_message_id"] = message.thread_id

        if message.format == "markdown":
            payload["parse_mode"] = "MarkdownV2"
        elif message.format == "html":
            payload["parse_mode"] = "HTML"

        response = await client.post(self._api_url("sendMessage"), json=payload)
        data = response.json()

        if not data.get("ok"):
            raise Exception(f"Telegram send failed: {data.get('description')}")

        return str(data["result"]["message_id"])

    async def _receive_impl(self) -> AsyncIterator[InboundMessage]:
        """Poll for updates using long polling."""
        client = await self._get_client()

        params = {
            "offset": self._last_update_id + 1,
            "timeout": 30,
            "allowed_updates": ["message"],
        }

        response = await client.get(self._api_url("getUpdates"), params=params)
        data = response.json()

        if not data.get("ok"):
            return

        for update in data.get("result", []):
            self._last_update_id = update["update_id"]

            msg = update.get("message", {})
            if not msg:
                continue

            sender = msg.get("from", {})
            attachments = []

            # Handle attachments
            for attach_type in ["photo", "document", "video", "audio", "voice"]:
                if attach_type in msg:
                    attach_data = msg[attach_type]
                    if isinstance(attach_data, list):
                        attach_data = attach_data[-1]  # Get largest photo
                    attachments.append({"type": attach_type, "data": attach_data})

            yield InboundMessage(
                message_id=str(msg.get("message_id", "")),
                platform="telegram",
                sender_id=str(sender.get("id", "")),
                sender_name=sender.get("first_name", "")
                + " "
                + sender.get("last_name", ""),
                content=msg.get("text", "") or msg.get("caption", ""),
                thread_id=str(msg.get("reply_to_message", {}).get("message_id", ""))
                or None,
                channel_id=str(msg.get("chat", {}).get("id", "")),
                attachments=attachments,
                raw=msg,
            )

    async def send_photo(
        self,
        chat_id: str,
        photo_url: str,
        caption: str = "",
    ) -> str:
        """Send a photo to a chat."""
        client = await self._get_client()

        payload = {
            "chat_id": chat_id,
            "photo": photo_url,
        }
        if caption:
            payload["caption"] = caption

        response = await client.post(self._api_url("sendPhoto"), json=payload)
        data = response.json()

        if not data.get("ok"):
            raise Exception(f"Telegram sendPhoto failed: {data.get('description')}")

        return str(data["result"]["message_id"])

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: str = "",
    ) -> str:
        """Send a document to a chat."""
        client = await self._get_client()

        with open(file_path, "rb") as f:
            files = {"document": f}
            data = {"chat_id": chat_id}
            if caption:
                data["caption"] = caption

            response = await client.post(
                self._api_url("sendDocument"),
                data=data,
                files=files,
            )

        result = response.json()

        if not result.get("ok"):
            raise Exception(f"Telegram sendDocument failed: {result.get('description')}")

        return str(result["result"]["message_id"])

    async def send_keyboard(
        self,
        chat_id: str,
        text: str,
        buttons: list[list[str]],
    ) -> str:
        """Send a message with an inline keyboard."""
        client = await self._get_client()

        keyboard = {
            "inline_keyboard": [
                [{"text": btn, "callback_data": btn} for btn in row] for row in buttons
            ]
        }

        payload = {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": keyboard,
        }

        response = await client.post(self._api_url("sendMessage"), json=payload)
        data = response.json()

        if not data.get("ok"):
            raise Exception(f"Telegram sendKeyboard failed: {data.get('description')}")

        return str(data["result"]["message_id"])

    async def _action_impl(self, action_name: str, params: dict) -> dict:
        """Execute Telegram-specific actions."""
        client = await self._get_client()

        if action_name == "send_chat_action":
            response = await client.post(
                self._api_url("sendChatAction"),
                json={"chat_id": params["chat_id"], "action": params.get("action", "typing")},
            )
            return response.json()

        elif action_name == "get_chat":
            response = await client.get(
                self._api_url("getChat"),
                params={"chat_id": params["chat_id"]},
            )
            return response.json()

        raise ValueError(f"Unknown action: {action_name}")
