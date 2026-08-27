"""
HyperClaw Integrations Layer — unified connector system for all platforms.
"""

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorRegistry,
    ConnectorInfo,
    InboundMessage,
    OutboundMessage,
    ConnectorAuthError,
    ConnectorRateLimitError,
    ConnectorUnavailableError,
    CapabilityNotSupportedError,
)
from integrations.gateway import HyperClawGateway

__all__ = [
    "BaseConnector",
    "ConnectorCapability",
    "ConnectorRegistry",
    "ConnectorInfo",
    "InboundMessage",
    "OutboundMessage",
    "ConnectorAuthError",
    "ConnectorRateLimitError",
    "ConnectorUnavailableError",
    "CapabilityNotSupportedError",
    "HyperClawGateway",
]
