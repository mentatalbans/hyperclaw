import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from integrations.base import OutboundMessage, InboundMessage
from integrations.messaging.telegram import TelegramConnector
from integrations.messaging.slack import SlackConnector
from integrations.messaging.discord import DiscordConnector
from integrations.messaging.whatsapp import WhatsAppConnector
from integrations.messaging.email import EmailConnector


@pytest.fixture
def telegram():
    return TelegramConnector({"bot_token": "test_token"})


@pytest.fixture
def slack():
    return SlackConnector({"bot_token": "xoxb-test"})


@pytest.fixture
def discord():
    return DiscordConnector({"bot_token": "test_token"})


@pytest.fixture
def whatsapp():
    return WhatsAppConnector({"access_token": "test_token", "phone_number_id": "123456"})


@pytest.fixture
def email_connector():
    return EmailConnector({
        "provider": "smtp", "smtp_host": "smtp.gmail.com",
        "smtp_port": 587, "username": "test@test.com", "password": "pass",
        "imap_host": "imap.gmail.com"
    })


class TestTelegramConnector:
    @pytest.mark.asyncio
    async def test_send_formats_message_correctly(self, telegram):
        msg = OutboundMessage(content="Hello World", platform="telegram", recipient_id="12345")
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json = MagicMock(return_value={"ok": True, "result": {"message_id": 99}})
            mock_client.post = AsyncMock(return_value=mock_response)
            # Reset the internal client to use our mock
            telegram._client = mock_client
            result = await telegram.send(msg)
            mock_client.post.assert_called_once()
            call_kwargs = mock_client.post.call_args
            assert "sendMessage" in str(call_kwargs)
            assert "Hello World" in str(call_kwargs)

    def test_has_correct_capabilities(self, telegram):
        from integrations.base import ConnectorCapability
        assert telegram.supports(ConnectorCapability.SEND_MESSAGE)
        assert telegram.supports(ConnectorCapability.RECEIVE_MESSAGE)


class TestSlackConnector:
    @pytest.mark.asyncio
    async def test_send_uses_chat_postmessage(self, slack):
        msg = OutboundMessage(content="Test message", platform="slack", recipient_id="C123456")
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json = MagicMock(return_value={"ok": True, "ts": "1234567890.000001"})
            mock_client.post = AsyncMock(return_value=mock_response)
            slack._client = mock_client
            await slack.send(msg)
            mock_client.post.assert_called_once()
            assert "postMessage" in str(mock_client.post.call_args) or "chat" in str(mock_client.post.call_args)


class TestDiscordConnector:
    @pytest.mark.asyncio
    async def test_send_uses_channel_id(self, discord):
        msg = OutboundMessage(content="Hello Discord", platform="discord", recipient_id="987654321", channel_id="111222333")
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.status_code = 200
            mock_response.json = MagicMock(return_value={"id": "msg123"})
            mock_client.post = AsyncMock(return_value=mock_response)
            discord._client = mock_client
            await discord.send(msg)
            call_url = str(mock_client.post.call_args)
            assert "discord" in call_url.lower() or "channels" in call_url


class TestWhatsAppConnector:
    @pytest.mark.asyncio
    async def test_send_uses_graph_api(self, whatsapp):
        msg = OutboundMessage(content="Hi there", platform="whatsapp", recipient_id="+1234567890")
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.status_code = 200
            mock_response.json = MagicMock(return_value={"messages": [{"id": "wamid.123"}]})
            mock_client.post = AsyncMock(return_value=mock_response)
            whatsapp._client = mock_client
            await whatsapp.send(msg)
            assert mock_client.post.called
            assert "graph.facebook.com" in str(mock_client.post.call_args)

    def test_has_health_data_context(self, whatsapp):
        assert whatsapp.hypershield_context == "health_data"


class TestInboundMessageDataclass:
    def test_defaults(self):
        msg = InboundMessage(
            message_id="m1", platform="test", sender_id="s1",
            sender_name="Test User", content="hello"
        )
        assert msg.thread_id is None
        assert msg.channel_id is None
        assert msg.attachments == []
        assert msg.raw == {}
