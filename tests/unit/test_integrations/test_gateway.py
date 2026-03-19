import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from integrations.base import ConnectorRegistry, InboundMessage, OutboundMessage, ConnectorInfo, ConnectorCapability, BaseConnector
from integrations.gateway import HyperClawGateway


class MockMessagingConnector(BaseConnector):
    def __init__(self, connector_id="mock_messaging"):
        self._info = ConnectorInfo(
            connector_id=connector_id,
            platform=connector_id,
            category="messaging",
            capabilities=frozenset({ConnectorCapability.SEND_MESSAGE, ConnectorCapability.RECEIVE_MESSAGE}),
            auth_type="api_key"
        )
        self.sent_messages = []

    @property
    def info(self):
        return self._info

    async def health(self):
        return True

    async def _send_impl(self, message: OutboundMessage) -> str:
        self.sent_messages.append(message)
        return "msg_id_123"


class TestHyperClawGateway:
    def test_gateway_initializes(self):
        registry = ConnectorRegistry()
        gateway = HyperClawGateway(registry)
        assert gateway is not None

    @pytest.mark.asyncio
    async def test_gateway_handle_inbound_calls_nexus(self):
        registry = ConnectorRegistry()
        mock_nexus = AsyncMock()
        mock_nexus.orchestrate = AsyncMock(return_value="Agent response")
        gateway = HyperClawGateway(registry, nexus=mock_nexus)

        msg = InboundMessage(
            message_id="m1", platform="telegram", sender_id="u1",
            sender_name="User", content="Hello agent"
        )
        # Register a connector for reply routing
        conn = MockMessagingConnector("telegram")
        registry.register(conn)

        await gateway.handle_inbound(msg)
        mock_nexus.orchestrate.assert_called_once()

    @pytest.mark.asyncio
    async def test_gateway_reply_uses_originating_platform(self):
        registry = ConnectorRegistry()
        conn = MockMessagingConnector("telegram")
        registry.register(conn)
        gateway = HyperClawGateway(registry)

        original = InboundMessage(
            message_id="m1", platform="telegram", sender_id="u1",
            sender_name="User", content="hello"
        )
        await gateway.reply(original, "Hello back!")
        assert len(conn.sent_messages) == 1
        assert conn.sent_messages[0].content == "Hello back!"
        assert conn.sent_messages[0].platform == "telegram"

    @pytest.mark.asyncio
    async def test_gateway_broadcast_sends_to_all_platforms(self):
        registry = ConnectorRegistry()
        telegram_conn = MockMessagingConnector("telegram")
        slack_conn = MockMessagingConnector("slack")
        registry.register(telegram_conn)
        registry.register(slack_conn)
        gateway = HyperClawGateway(registry)

        await gateway.broadcast(
            "Broadcast message",
            platforms=["telegram", "slack"],
            channel_ids={"telegram": "chat123", "slack": "C123456"}
        )
        assert len(telegram_conn.sent_messages) == 1
        assert len(slack_conn.sent_messages) == 1
        assert telegram_conn.sent_messages[0].content == "Broadcast message"
        assert slack_conn.sent_messages[0].content == "Broadcast message"
