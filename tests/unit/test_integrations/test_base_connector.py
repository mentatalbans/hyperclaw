import pytest
from integrations.base import (
    BaseConnector, ConnectorRegistry, ConnectorInfo, ConnectorCapability,
    CapabilityNotSupportedError, InboundMessage, OutboundMessage
)
from datetime import datetime


# Concrete test connector
class MockConnector(BaseConnector):
    def __init__(self):
        self._info = ConnectorInfo(
            connector_id="mock",
            platform="mock",
            category="test",
            capabilities=frozenset({ConnectorCapability.SEND_MESSAGE, ConnectorCapability.READ_DATA}),
            auth_type="api_key"
        )

    @property
    def info(self):
        return self._info

    async def health(self):
        return True


class TestConnectorRegistry:
    def test_register_and_get(self):
        registry = ConnectorRegistry()
        conn = MockConnector()
        registry.register(conn)
        assert registry.get("mock") is conn

    def test_get_missing_raises(self):
        registry = ConnectorRegistry()
        with pytest.raises(KeyError):
            registry.get("nonexistent")

    def test_list_all(self):
        registry = ConnectorRegistry()
        conn = MockConnector()
        registry.register(conn)
        assert conn in registry.list_all()

    def test_list_by_category(self):
        registry = ConnectorRegistry()
        registry.register(MockConnector())
        assert len(registry.list_by_category("test")) == 1
        assert len(registry.list_by_category("other")) == 0

    def test_list_by_capability(self):
        registry = ConnectorRegistry()
        registry.register(MockConnector())
        assert len(registry.list_by_capability(ConnectorCapability.SEND_MESSAGE)) == 1
        assert len(registry.list_by_capability(ConnectorCapability.SEARCH)) == 0

    def test_build_from_config_skips_disabled(self, tmp_path):
        cfg = tmp_path / "integrations.yaml"
        cfg.write_text("""
messaging:
  telegram:
    enabled: false
    bot_token: "testtoken"
""")
        registry = ConnectorRegistry.build_from_config(str(cfg))
        # telegram disabled — should not be in registry
        assert len(registry.list_all()) == 0

    def test_build_from_config_skips_missing_credentials_gracefully(self, tmp_path):
        cfg = tmp_path / "integrations.yaml"
        cfg.write_text("""
messaging:
  telegram:
    enabled: true
    bot_token: ""
""")
        # Should not raise even with missing token
        registry = ConnectorRegistry.build_from_config(str(cfg))
        # May or may not register (connector may skip if no token) — just no crash
        assert registry is not None

    def test_build_from_config_bad_file_no_crash(self, tmp_path):
        registry = ConnectorRegistry.build_from_config("/nonexistent/path.yaml")
        assert registry is not None


class TestBaseConnectorCapabilities:
    def test_require_raises_when_missing(self):
        conn = MockConnector()
        with pytest.raises(CapabilityNotSupportedError):
            conn.require(ConnectorCapability.SEARCH)

    def test_supports_returns_true_for_declared(self):
        conn = MockConnector()
        assert conn.supports(ConnectorCapability.SEND_MESSAGE) is True

    def test_supports_returns_false_for_undeclared(self):
        conn = MockConnector()
        assert conn.supports(ConnectorCapability.SEARCH) is False


class TestDataclasses:
    def test_inbound_message_defaults(self):
        msg = InboundMessage(
            message_id="123", platform="test", sender_id="u1",
            sender_name="User", content="hello"
        )
        assert msg.thread_id is None
        assert msg.attachments == []
        assert isinstance(msg.received_at, datetime)

    def test_outbound_message_defaults(self):
        msg = OutboundMessage(content="hi", platform="test", recipient_id="u1")
        assert msg.format == "text"
        assert msg.attachments == []
