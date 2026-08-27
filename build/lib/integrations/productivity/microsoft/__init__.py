"""HyperClaw Microsoft 365 Connectors."""

from integrations.productivity.microsoft.outlook import OutlookConnector
from integrations.productivity.microsoft.onedrive import OneDriveConnector
from integrations.productivity.microsoft.sharepoint import SharePointConnector
from integrations.productivity.microsoft.calendar import MicrosoftCalendarConnector

__all__ = [
    "OutlookConnector",
    "OneDriveConnector",
    "SharePointConnector",
    "MicrosoftCalendarConnector",
]
