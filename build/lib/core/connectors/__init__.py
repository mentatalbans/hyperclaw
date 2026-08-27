"""
HyperClaw Connectors.
Integration connectors for social media, smart home, messaging, productivity, and infrastructure.
"""

from .social import (
    SocialConnector,
    TwitterConnector,
    LinkedInConnector,
    InstagramConnector,
    FacebookConnector,
    YouTubeConnector,
    BlueskyConnector,
    MastodonConnector,
    RedditConnector,
    TikTokConnector,
)

from .smarthome import (
    SmartHomeConnector,
    PhilipsHueConnector,
    HomeAssistantConnector,
    SonosConnector,
    SpotifyConnector,
    MQTTConnector,
    SmartThingsConnector,
)

from .messaging import (
    MessagingConnector,
    GoogleChatConnector,
    SignalConnector,
    MatrixConnector,
    iMessageConnector,
    LINEConnector,
    IRCConnector,
    WeChatConnector,
    MattermostConnector,
)

from .productivity import (
    ProductivityConnector,
    ObsidianConnector,
    AppleNotesConnector,
    AppleCalendarConnector,
    GoogleSheetsConnector,
    GoogleDocsConnector,
    EvernoteConnector,
    OneNoteConnector,
    ClickUpConnector,
    MondayConnector,
)

from .infrastructure import (
    InfrastructureConnector,
    DockerConnector,
    AWSConnector,
    GCPConnector,
    AzureConnector,
    TailscaleConnector,
    SnowflakeConnector,
    BigQueryConnector,
    DatadogConnector,
    PagerDutyConnector,
)

__all__ = [
    # Social
    "SocialConnector",
    "TwitterConnector",
    "LinkedInConnector",
    "InstagramConnector",
    "FacebookConnector",
    "YouTubeConnector",
    "BlueskyConnector",
    "MastodonConnector",
    "RedditConnector",
    "TikTokConnector",
    # Smart Home
    "SmartHomeConnector",
    "PhilipsHueConnector",
    "HomeAssistantConnector",
    "SonosConnector",
    "SpotifyConnector",
    "MQTTConnector",
    "SmartThingsConnector",
    # Messaging
    "MessagingConnector",
    "GoogleChatConnector",
    "SignalConnector",
    "MatrixConnector",
    "iMessageConnector",
    "LINEConnector",
    "IRCConnector",
    "WeChatConnector",
    "MattermostConnector",
    # Productivity
    "ProductivityConnector",
    "ObsidianConnector",
    "AppleNotesConnector",
    "AppleCalendarConnector",
    "GoogleSheetsConnector",
    "GoogleDocsConnector",
    "EvernoteConnector",
    "OneNoteConnector",
    "ClickUpConnector",
    "MondayConnector",
    # Infrastructure
    "InfrastructureConnector",
    "DockerConnector",
    "AWSConnector",
    "GCPConnector",
    "AzureConnector",
    "TailscaleConnector",
    "SnowflakeConnector",
    "BigQueryConnector",
    "DatadogConnector",
    "PagerDutyConnector",
]
