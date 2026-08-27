"""
BaseConnector — foundation for all HyperClaw platform connectors.
ConnectorRegistry — central registry for connector discovery and lookup.
"""

from __future__ import annotations

import importlib
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, AsyncIterator

logger = logging.getLogger("hyperclaw.integrations")


class ConnectorCapability(str, Enum):
    """Capabilities that a connector may support."""

    SEND_MESSAGE = "send_message"
    RECEIVE_MESSAGE = "receive_message"
    READ_DATA = "read_data"
    WRITE_DATA = "write_data"
    DELETE_DATA = "delete_data"
    LIST_DATA = "list_data"
    STREAM = "stream"
    FILE_UPLOAD = "file_upload"
    FILE_DOWNLOAD = "file_download"
    WEBHOOKS = "webhooks"
    OAUTH = "oauth"
    SEARCH = "search"
    NOTIFY = "notify"
    CALENDAR = "calendar"
    ACTION = "action"


@dataclass
class InboundMessage:
    """Message received from an external platform."""

    message_id: str
    platform: str
    sender_id: str
    sender_name: str
    content: str
    thread_id: str | None = None
    channel_id: str | None = None
    attachments: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)
    received_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class OutboundMessage:
    """Message to be sent to an external platform."""

    content: str
    platform: str
    recipient_id: str
    thread_id: str | None = None
    channel_id: str | None = None
    attachments: list[dict] = field(default_factory=list)
    format: str = "text"  # "text" | "markdown" | "html" | "blocks"


@dataclass
class ConnectorInfo:
    """Metadata about a connector's capabilities and limits."""

    connector_id: str
    platform: str
    category: str  # "messaging" | "productivity" | "enterprise" | etc.
    capabilities: frozenset
    auth_type: str  # "api_key" | "oauth2" | "webhook_token" | "bearer"
    rate_limit_per_minute: int = 60
    supports_threads: bool = False
    supports_attachments: bool = False
    supports_reactions: bool = False


class ConnectorAuthError(Exception):
    """Authentication failed for connector."""

    pass


class ConnectorRateLimitError(Exception):
    """Rate limit exceeded for connector."""

    pass


class ConnectorUnavailableError(Exception):
    """Connector service is unavailable."""

    pass


class CapabilityNotSupportedError(Exception):
    """Requested capability is not supported by this connector."""

    pass


class BaseConnector(ABC):
    """
    Abstract base class for all HyperClaw platform connectors.

    Subclasses must implement:
    - info property: returns ConnectorInfo
    - health(): async check if connector is operational

    Optional implementations depending on capabilities:
    - _send_impl(): for SEND_MESSAGE
    - _receive_impl(): for RECEIVE_MESSAGE
    - _read_impl(): for READ_DATA
    - _write_impl(): for WRITE_DATA
    - _delete_impl(): for DELETE_DATA
    - _list_impl(): for LIST_DATA
    - _search_impl(): for SEARCH
    - _action_impl(): for ACTION
    """

    # Optional HyperShield security context
    hypershield_context: str | None = None

    @property
    @abstractmethod
    def info(self) -> ConnectorInfo:
        """Return connector metadata."""
        ...

    @property
    def connector_id(self) -> str:
        """Shorthand for info.connector_id."""
        return self.info.connector_id

    def supports(self, cap: ConnectorCapability) -> bool:
        """Check if this connector supports a capability."""
        return cap in self.info.capabilities

    def require(self, cap: ConnectorCapability) -> None:
        """Raise if capability not supported."""
        if not self.supports(cap):
            raise CapabilityNotSupportedError(
                f"{self.connector_id} does not support {cap}"
            )

    @abstractmethod
    async def health(self) -> bool:
        """Check if connector is operational."""
        ...

    # ── Messaging ──────────────────────────────────────────────────────────

    async def send(self, message: OutboundMessage) -> str:
        """Send a message to the platform. Returns message ID."""
        self.require(ConnectorCapability.SEND_MESSAGE)
        return await self._send_impl(message)

    async def _send_impl(self, message: OutboundMessage) -> str:
        raise NotImplementedError

    async def receive(self) -> AsyncIterator[InboundMessage]:
        """Receive messages from the platform."""
        self.require(ConnectorCapability.RECEIVE_MESSAGE)
        async for msg in self._receive_impl():
            yield msg

    async def _receive_impl(self) -> AsyncIterator[InboundMessage]:
        raise NotImplementedError
        yield  # make it an async generator

    # ── Data Operations ────────────────────────────────────────────────────

    async def read(self, resource_id: str, **kwargs) -> dict:
        """Read a resource from the platform."""
        self.require(ConnectorCapability.READ_DATA)
        return await self._read_impl(resource_id, **kwargs)

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        raise NotImplementedError

    async def write(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Write/create a resource on the platform."""
        self.require(ConnectorCapability.WRITE_DATA)
        return await self._write_impl(resource_type, data, **kwargs)

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        raise NotImplementedError

    async def delete(self, resource_id: str, **kwargs) -> bool:
        """Delete a resource from the platform."""
        self.require(ConnectorCapability.DELETE_DATA)
        return await self._delete_impl(resource_id, **kwargs)

    async def _delete_impl(self, resource_id: str, **kwargs) -> bool:
        raise NotImplementedError

    async def list(
        self, resource_type: str, filters: dict | None = None, **kwargs
    ) -> list[dict]:
        """List resources of a given type."""
        self.require(ConnectorCapability.LIST_DATA)
        return await self._list_impl(resource_type, filters or {}, **kwargs)

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        raise NotImplementedError

    async def search(self, query: str, **kwargs) -> list[dict]:
        """Search for resources."""
        self.require(ConnectorCapability.SEARCH)
        return await self._search_impl(query, **kwargs)

    async def _search_impl(self, query: str, **kwargs) -> list[dict]:
        raise NotImplementedError

    async def action(self, action_name: str, params: dict | None = None) -> dict:
        """Execute a platform-specific action."""
        self.require(ConnectorCapability.ACTION)
        return await self._action_impl(action_name, params or {})

    async def _action_impl(self, action_name: str, params: dict) -> dict:
        raise NotImplementedError


class ConnectorRegistry:
    """Central registry of all HyperClaw connectors."""

    def __init__(self) -> None:
        self._connectors: dict[str, BaseConnector] = {}

    def register(self, connector: BaseConnector) -> None:
        """Register a connector instance."""
        self._connectors[connector.connector_id] = connector

    def unregister(self, connector_id: str) -> None:
        """Remove a connector from the registry."""
        self._connectors.pop(connector_id, None)

    def get(self, connector_id: str) -> BaseConnector:
        """Get a connector by ID. Raises KeyError if not found."""
        if connector_id not in self._connectors:
            raise KeyError(f"Connector not found: {connector_id}")
        return self._connectors[connector_id]

    def has(self, connector_id: str) -> bool:
        """Check if a connector is registered."""
        return connector_id in self._connectors

    def list_all(self) -> list[BaseConnector]:
        """List all registered connectors."""
        return list(self._connectors.values())

    def list_by_category(self, category: str) -> list[BaseConnector]:
        """List connectors in a specific category."""
        return [c for c in self._connectors.values() if c.info.category == category]

    def list_by_capability(self, cap: ConnectorCapability) -> list[BaseConnector]:
        """List connectors that support a capability."""
        return [c for c in self._connectors.values() if c.supports(cap)]

    def get_messaging_connectors(self) -> list[BaseConnector]:
        """Shorthand for messaging category."""
        return self.list_by_category("messaging")

    @classmethod
    def build_from_config(cls, config_path: str) -> "ConnectorRegistry":
        """
        Reads integrations/config/integrations.yaml.
        Instantiates and registers all enabled connectors.
        Skips connectors with missing credentials without crashing.
        """
        import yaml

        registry = cls()

        try:
            with open(config_path) as f:
                config = yaml.safe_load(f)
        except Exception as e:
            logger.warning(f"Could not load integrations config: {e}")
            return registry

        if not config:
            return registry

        connector_map = _get_connector_map()

        def _resolve_env(value: Any) -> Any:
            """Resolve ${ENV_VAR} references."""
            if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                env_key = value[2:-1]
                return os.environ.get(env_key, "")
            return value

        def _resolve_config(cfg: Any) -> Any:
            """Recursively resolve env vars in config dict."""
            if isinstance(cfg, dict):
                return {k: _resolve_config(v) for k, v in cfg.items()}
            elif isinstance(cfg, list):
                return [_resolve_config(v) for v in cfg]
            else:
                return _resolve_env(cfg)

        def _try_register(
            connector_id: str, connector_class: type, connector_config: dict
        ) -> None:
            resolved = _resolve_config(connector_config)
            try:
                connector = connector_class(resolved)
                registry.register(connector)
                logger.info(f"Registered connector: {connector_id}")
            except Exception as e:
                logger.warning(f"Skipping connector {connector_id}: {e}")

        _walk_config_and_register(config, connector_map, _try_register)

        return registry


def _get_connector_map() -> dict[str, type]:
    """Returns mapping of connector_id -> connector class."""
    connectors: dict[str, type] = {}
    _try_import_connectors(connectors)
    return connectors


def _try_import_connectors(connectors: dict[str, type]) -> None:
    """Try to import all connector classes, skip if import fails."""
    imports = [
        # Messaging
        ("telegram", "integrations.messaging.telegram", "TelegramConnector"),
        ("slack", "integrations.messaging.slack", "SlackConnector"),
        ("discord", "integrations.messaging.discord", "DiscordConnector"),
        ("whatsapp", "integrations.messaging.whatsapp", "WhatsAppConnector"),
        ("teams", "integrations.messaging.teams", "TeamsConnector"),
        ("signal", "integrations.messaging.signal", "SignalConnector"),
        ("imessage", "integrations.messaging.imessage", "iMessageConnector"),
        ("sms", "integrations.messaging.sms", "SMSConnector"),
        ("email", "integrations.messaging.email", "EmailConnector"),
        # Google Workspace
        ("gmail", "integrations.productivity.google.gmail", "GmailConnector"),
        (
            "google_calendar",
            "integrations.productivity.google.calendar",
            "GoogleCalendarConnector",
        ),
        (
            "google_drive",
            "integrations.productivity.google.drive",
            "GoogleDriveConnector",
        ),
        (
            "google_docs",
            "integrations.productivity.google.docs",
            "GoogleDocsConnector",
        ),
        (
            "google_sheets",
            "integrations.productivity.google.sheets",
            "GoogleSheetsConnector",
        ),
        (
            "google_meet",
            "integrations.productivity.google.meet",
            "GoogleMeetConnector",
        ),
        (
            "google_tasks",
            "integrations.productivity.google.tasks",
            "GoogleTasksConnector",
        ),
        # Microsoft 365
        ("outlook", "integrations.productivity.microsoft.outlook", "OutlookConnector"),
        (
            "onedrive",
            "integrations.productivity.microsoft.onedrive",
            "OneDriveConnector",
        ),
        (
            "sharepoint",
            "integrations.productivity.microsoft.sharepoint",
            "SharePointConnector",
        ),
        (
            "microsoft_calendar",
            "integrations.productivity.microsoft.calendar",
            "MicrosoftCalendarConnector",
        ),
        # Productivity
        ("notion", "integrations.productivity.notion", "NotionConnector"),
        ("airtable", "integrations.productivity.airtable", "AirtableConnector"),
        ("trello", "integrations.productivity.trello", "TrelloConnector"),
        ("asana", "integrations.productivity.asana", "AsanaConnector"),
        ("linear", "integrations.productivity.linear", "LinearConnector"),
        ("todoist", "integrations.productivity.todoist", "TodoistConnector"),
        # Enterprise
        ("salesforce", "integrations.enterprise.salesforce", "SalesforceConnector"),
        ("hubspot", "integrations.enterprise.hubspot", "HubSpotConnector"),
        ("jira", "integrations.enterprise.jira", "JiraConnector"),
        ("confluence", "integrations.enterprise.confluence", "ConfluenceConnector"),
        # Developer
        ("github", "integrations.developer.github", "GitHubConnector"),
        ("gitlab", "integrations.developer.gitlab", "GitLabConnector"),
        # Storage
        ("box", "integrations.storage.box", "BoxConnector"),
        ("dropbox", "integrations.storage.dropbox", "DropboxConnector"),
        # Finance
        ("stripe", "integrations.finance.stripe", "StripeConnector"),
        ("quickbooks", "integrations.finance.quickbooks", "QuickBooksConnector"),
        # Communication
        ("zoom", "integrations.communication.zoom", "ZoomConnector"),
        ("twilio", "integrations.communication.twilio", "TwilioConnector"),
        # Automation
        ("zapier", "integrations.automation.zapier", "ZapierConnector"),
        ("make", "integrations.automation.make", "MakeConnector"),
        # Data
        ("supabase", "integrations.data.supabase_connector", "SupabaseConnector"),
        ("postgres", "integrations.data.postgres", "PostgresConnector"),
        (
            "google_analytics",
            "integrations.data.google_analytics",
            "GoogleAnalyticsConnector",
        ),
    ]

    for connector_id, module_path, class_name in imports:
        try:
            mod = importlib.import_module(module_path)
            connectors[connector_id] = getattr(mod, class_name)
        except Exception as e:
            logger.debug(f"Could not import {connector_id}: {e}")


def _walk_config_and_register(
    config: dict,
    connector_map: dict[str, type],
    register_fn: callable,
) -> None:
    """Walk nested config dict, find enabled connectors, register them."""
    for section_key, section_val in config.items():
        if not isinstance(section_val, dict):
            continue

        for connector_id, connector_cfg in section_val.items():
            if not isinstance(connector_cfg, dict):
                continue

            # Direct connector config
            if connector_cfg.get("enabled", False) and connector_id in connector_map:
                register_fn(connector_id, connector_map[connector_id], connector_cfg)
            # Handle nested sections (e.g. google.gmail, microsoft.outlook)
            elif isinstance(connector_cfg, dict):
                for sub_id, sub_cfg in connector_cfg.items():
                    if isinstance(sub_cfg, dict) and sub_cfg.get("enabled", False):
                        # Try sub_id first (e.g., "gmail"), then full path
                        full_id = f"{connector_id}_{sub_id}"
                        cls = connector_map.get(sub_id) or connector_map.get(full_id)
                        if cls:
                            # Merge parent config with sub config
                            merged = {**connector_cfg, **sub_cfg}
                            register_fn(sub_id, cls, merged)
