"""HyperClaw Messaging Connectors."""

from integrations.messaging.telegram import TelegramConnector
from integrations.messaging.slack import SlackConnector
from integrations.messaging.discord import DiscordConnector
from integrations.messaging.whatsapp import WhatsAppConnector
from integrations.messaging.teams import TeamsConnector
from integrations.messaging.signal import SignalConnector
from integrations.messaging.imessage import (
    iMessageConnector,
    send_imessage,
    read_imessages,
    imessage_available,
)
from integrations.messaging.sms import SMSConnector
from integrations.messaging.email import EmailConnector

__all__ = [
    "TelegramConnector",
    "SlackConnector",
    "DiscordConnector",
    "WhatsAppConnector",
    "TeamsConnector",
    "SignalConnector",
    "iMessageConnector",
    "SMSConnector",
    "EmailConnector",
    # Standalone iMessage functions
    "send_imessage",
    "read_imessages",
    "imessage_available",
]
