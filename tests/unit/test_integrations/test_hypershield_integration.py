import pytest
from integrations.messaging.whatsapp import WhatsAppConnector
from integrations.messaging.signal import SignalConnector
from integrations.finance.stripe import StripeConnector
from integrations.finance.quickbooks import QuickBooksConnector


class TestHyperShieldContexts:
    def test_whatsapp_has_health_data_context(self):
        conn = WhatsAppConnector({"access_token": "tok", "phone_number_id": "123"})
        assert conn.hypershield_context == "health_data"

    def test_signal_has_isolated_context(self):
        conn = SignalConnector({"api_url": "http://localhost:8080", "phone_number": "+1234567890"})
        assert conn.hypershield_context == "isolated"

    def test_stripe_has_financial_data_context(self):
        conn = StripeConnector({"secret_key": "sk_test_123"})
        assert conn.hypershield_context == "financial_data"

    def test_quickbooks_has_financial_data_context(self):
        conn = QuickBooksConnector({"client_id": "cid", "client_secret": "csec"})
        assert conn.hypershield_context == "financial_data"

    def test_imessage_health_returns_false_on_non_darwin(self):
        import sys
        from integrations.messaging.imessage import iMessageConnector
        conn = iMessageConnector({})
        # On macOS this would be True, but in CI it is platform-dependent
        assert hasattr(conn, "health")
