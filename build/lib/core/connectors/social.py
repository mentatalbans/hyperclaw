"""
Social Media Connectors.
Integration with Twitter/X, LinkedIn, Instagram, Facebook, YouTube, Bluesky, Mastodon, Reddit, TikTok.
"""
from __future__ import annotations

import os
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class SocialPost:
    """Social media post."""
    id: str
    text: str
    author: str | None = None
    created_at: datetime | None = None
    likes: int = 0
    shares: int = 0
    comments: int = 0
    url: str | None = None
    media: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


class SocialConnector(ABC):
    """Abstract base for social media connectors."""

    name: str = "base"

    @abstractmethod
    async def post(self, text: str, media: list[str] | None = None, **kwargs) -> SocialPost:
        """Create a new post."""
        pass

    @abstractmethod
    async def get_timeline(self, limit: int = 20, **kwargs) -> list[SocialPost]:
        """Get recent timeline posts."""
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# TWITTER/X
# ═══════════════════════════════════════════════════════════════════════════════

class TwitterConnector(SocialConnector):
    """Twitter/X API connector."""

    name = "twitter"

    def __init__(
        self,
        bearer_token: str | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
        access_token: str | None = None,
        access_secret: str | None = None,
    ):
        self.bearer_token = bearer_token or os.environ.get("TWITTER_BEARER_TOKEN")
        self.api_key = api_key or os.environ.get("TWITTER_API_KEY")
        self.api_secret = api_secret or os.environ.get("TWITTER_API_SECRET")
        self.access_token = access_token or os.environ.get("TWITTER_ACCESS_TOKEN")
        self.access_secret = access_secret or os.environ.get("TWITTER_ACCESS_SECRET")
        self.base_url = "https://api.twitter.com/2"

    async def post(self, text: str, media: list[str] | None = None, **kwargs) -> SocialPost:
        # Twitter API v2 requires OAuth 1.0a for posting
        # Using requests-oauthlib in production
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/tweets",
                headers={"Authorization": f"Bearer {self.bearer_token}"},
                json={"text": text},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return SocialPost(
                id=data["data"]["id"],
                text=text,
                url=f"https://twitter.com/i/status/{data['data']['id']}",
                raw=data,
            )

    async def get_timeline(self, limit: int = 20, **kwargs) -> list[SocialPost]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/users/me/timelines/reverse_chronological",
                headers={"Authorization": f"Bearer {self.bearer_token}"},
                params={"max_results": min(limit, 100)},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return [
                SocialPost(
                    id=tweet["id"],
                    text=tweet.get("text", ""),
                    created_at=datetime.fromisoformat(tweet["created_at"].replace("Z", "+00:00"))
                    if tweet.get("created_at") else None,
                    raw=tweet,
                )
                for tweet in data.get("data", [])
            ]

    async def search(self, query: str, limit: int = 20) -> list[SocialPost]:
        """Search tweets."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/tweets/search/recent",
                headers={"Authorization": f"Bearer {self.bearer_token}"},
                params={"query": query, "max_results": min(limit, 100)},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return [
                SocialPost(id=tweet["id"], text=tweet.get("text", ""), raw=tweet)
                for tweet in data.get("data", [])
            ]


# ═══════════════════════════════════════════════════════════════════════════════
# LINKEDIN
# ═══════════════════════════════════════════════════════════════════════════════

class LinkedInConnector(SocialConnector):
    """LinkedIn API connector."""

    name = "linkedin"

    def __init__(self, access_token: str | None = None):
        self.access_token = access_token or os.environ.get("LINKEDIN_ACCESS_TOKEN")
        self.base_url = "https://api.linkedin.com/v2"

    async def post(self, text: str, media: list[str] | None = None, **kwargs) -> SocialPost:
        # Get user URN first
        async with httpx.AsyncClient() as client:
            me_response = await client.get(
                f"{self.base_url}/me",
                headers={"Authorization": f"Bearer {self.access_token}"},
                timeout=30.0,
            )
            me_response.raise_for_status()
            user_urn = f"urn:li:person:{me_response.json()['id']}"

            # Create post
            response = await client.post(
                f"{self.base_url}/ugcPosts",
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "X-Restli-Protocol-Version": "2.0.0",
                },
                json={
                    "author": user_urn,
                    "lifecycleState": "PUBLISHED",
                    "specificContent": {
                        "com.linkedin.ugc.ShareContent": {
                            "shareCommentary": {"text": text},
                            "shareMediaCategory": "NONE",
                        }
                    },
                    "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return SocialPost(id=data["id"], text=text, raw=data)

    async def get_timeline(self, limit: int = 20, **kwargs) -> list[SocialPost]:
        # LinkedIn doesn't provide a public timeline API
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# INSTAGRAM
# ═══════════════════════════════════════════════════════════════════════════════

class InstagramConnector(SocialConnector):
    """Instagram Graph API connector (requires Facebook Business account)."""

    name = "instagram"

    def __init__(self, access_token: str | None = None, account_id: str | None = None):
        self.access_token = access_token or os.environ.get("INSTAGRAM_ACCESS_TOKEN")
        self.account_id = account_id or os.environ.get("INSTAGRAM_ACCOUNT_ID")
        self.base_url = "https://graph.facebook.com/v18.0"

    async def post(self, text: str, media: list[str] | None = None, **kwargs) -> SocialPost:
        if not media:
            raise ValueError("Instagram requires at least one media item")

        async with httpx.AsyncClient() as client:
            # Create media container
            container_response = await client.post(
                f"{self.base_url}/{self.account_id}/media",
                params={
                    "access_token": self.access_token,
                    "image_url": media[0],
                    "caption": text,
                },
                timeout=30.0,
            )
            container_response.raise_for_status()
            container_id = container_response.json()["id"]

            # Publish the container
            publish_response = await client.post(
                f"{self.base_url}/{self.account_id}/media_publish",
                params={
                    "access_token": self.access_token,
                    "creation_id": container_id,
                },
                timeout=30.0,
            )
            publish_response.raise_for_status()
            data = publish_response.json()

            return SocialPost(id=data["id"], text=text, media=media, raw=data)

    async def get_timeline(self, limit: int = 20, **kwargs) -> list[SocialPost]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/{self.account_id}/media",
                params={
                    "access_token": self.access_token,
                    "fields": "id,caption,media_type,media_url,timestamp,like_count,comments_count",
                    "limit": limit,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return [
                SocialPost(
                    id=post["id"],
                    text=post.get("caption", ""),
                    created_at=datetime.fromisoformat(post["timestamp"].replace("Z", "+00:00"))
                    if post.get("timestamp") else None,
                    likes=post.get("like_count", 0),
                    comments=post.get("comments_count", 0),
                    media=[post.get("media_url")] if post.get("media_url") else [],
                    raw=post,
                )
                for post in data.get("data", [])
            ]


# ═══════════════════════════════════════════════════════════════════════════════
# FACEBOOK
# ═══════════════════════════════════════════════════════════════════════════════

class FacebookConnector(SocialConnector):
    """Facebook Graph API connector."""

    name = "facebook"

    def __init__(self, access_token: str | None = None, page_id: str | None = None):
        self.access_token = access_token or os.environ.get("FACEBOOK_ACCESS_TOKEN")
        self.page_id = page_id or os.environ.get("FACEBOOK_PAGE_ID")
        self.base_url = "https://graph.facebook.com/v18.0"

    async def post(self, text: str, media: list[str] | None = None, **kwargs) -> SocialPost:
        async with httpx.AsyncClient() as client:
            endpoint = f"{self.base_url}/{self.page_id}/feed"
            params: dict[str, Any] = {"access_token": self.access_token, "message": text}

            if media:
                params["link"] = media[0]

            response = await client.post(endpoint, params=params, timeout=30.0)
            response.raise_for_status()
            data = response.json()

            return SocialPost(
                id=data["id"],
                text=text,
                url=f"https://facebook.com/{data['id']}",
                raw=data,
            )

    async def get_timeline(self, limit: int = 20, **kwargs) -> list[SocialPost]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/{self.page_id}/feed",
                params={
                    "access_token": self.access_token,
                    "fields": "id,message,created_time,shares,likes.summary(true),comments.summary(true)",
                    "limit": limit,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return [
                SocialPost(
                    id=post["id"],
                    text=post.get("message", ""),
                    created_at=datetime.fromisoformat(post["created_time"].replace("+0000", "+00:00"))
                    if post.get("created_time") else None,
                    likes=post.get("likes", {}).get("summary", {}).get("total_count", 0),
                    shares=post.get("shares", {}).get("count", 0),
                    comments=post.get("comments", {}).get("summary", {}).get("total_count", 0),
                    raw=post,
                )
                for post in data.get("data", [])
            ]


# ═══════════════════════════════════════════════════════════════════════════════
# YOUTUBE
# ═══════════════════════════════════════════════════════════════════════════════

class YouTubeConnector(SocialConnector):
    """YouTube Data API connector."""

    name = "youtube"

    def __init__(self, api_key: str | None = None, access_token: str | None = None):
        self.api_key = api_key or os.environ.get("YOUTUBE_API_KEY")
        self.access_token = access_token or os.environ.get("YOUTUBE_ACCESS_TOKEN")
        self.base_url = "https://www.googleapis.com/youtube/v3"

    async def post(self, text: str, media: list[str] | None = None, **kwargs) -> SocialPost:
        # YouTube posting requires video upload which is more complex
        raise NotImplementedError("YouTube video upload requires resumable upload API")

    async def get_timeline(self, limit: int = 20, **kwargs) -> list[SocialPost]:
        """Get channel's recent videos."""
        channel_id = kwargs.get("channel_id")
        if not channel_id:
            return []

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/search",
                params={
                    "key": self.api_key,
                    "channelId": channel_id,
                    "part": "snippet",
                    "order": "date",
                    "maxResults": limit,
                    "type": "video",
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return [
                SocialPost(
                    id=item["id"]["videoId"],
                    text=item["snippet"]["title"],
                    author=item["snippet"]["channelTitle"],
                    created_at=datetime.fromisoformat(
                        item["snippet"]["publishedAt"].replace("Z", "+00:00")
                    ),
                    url=f"https://youtube.com/watch?v={item['id']['videoId']}",
                    raw=item,
                )
                for item in data.get("items", [])
            ]

    async def search(self, query: str, limit: int = 20) -> list[SocialPost]:
        """Search YouTube videos."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/search",
                params={
                    "key": self.api_key,
                    "q": query,
                    "part": "snippet",
                    "maxResults": limit,
                    "type": "video",
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return [
                SocialPost(
                    id=item["id"]["videoId"],
                    text=item["snippet"]["title"],
                    author=item["snippet"]["channelTitle"],
                    url=f"https://youtube.com/watch?v={item['id']['videoId']}",
                    raw=item,
                )
                for item in data.get("items", [])
            ]


# ═══════════════════════════════════════════════════════════════════════════════
# BLUESKY
# ═══════════════════════════════════════════════════════════════════════════════

class BlueskyConnector(SocialConnector):
    """Bluesky AT Protocol connector."""

    name = "bluesky"

    def __init__(
        self,
        handle: str | None = None,
        password: str | None = None,
        access_jwt: str | None = None,
    ):
        self.handle = handle or os.environ.get("BLUESKY_HANDLE")
        self.password = password or os.environ.get("BLUESKY_PASSWORD")
        self.access_jwt = access_jwt
        self.base_url = "https://bsky.social/xrpc"

    async def _ensure_auth(self) -> str:
        """Ensure we have a valid access token."""
        if self.access_jwt:
            return self.access_jwt

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/com.atproto.server.createSession",
                json={"identifier": self.handle, "password": self.password},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            self.access_jwt = data["accessJwt"]
            return self.access_jwt

    async def post(self, text: str, media: list[str] | None = None, **kwargs) -> SocialPost:
        token = await self._ensure_auth()

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/com.atproto.repo.createRecord",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "repo": self.handle,
                    "collection": "app.bsky.feed.post",
                    "record": {
                        "$type": "app.bsky.feed.post",
                        "text": text,
                        "createdAt": datetime.utcnow().isoformat() + "Z",
                    },
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return SocialPost(id=data["uri"], text=text, raw=data)

    async def get_timeline(self, limit: int = 20, **kwargs) -> list[SocialPost]:
        token = await self._ensure_auth()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/app.bsky.feed.getTimeline",
                headers={"Authorization": f"Bearer {token}"},
                params={"limit": limit},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return [
                SocialPost(
                    id=item["post"]["uri"],
                    text=item["post"]["record"].get("text", ""),
                    author=item["post"]["author"].get("handle"),
                    raw=item,
                )
                for item in data.get("feed", [])
            ]


# ═══════════════════════════════════════════════════════════════════════════════
# MASTODON
# ═══════════════════════════════════════════════════════════════════════════════

class MastodonConnector(SocialConnector):
    """Mastodon API connector."""

    name = "mastodon"

    def __init__(
        self,
        instance_url: str | None = None,
        access_token: str | None = None,
    ):
        self.instance_url = (instance_url or os.environ.get("MASTODON_INSTANCE", "https://mastodon.social")).rstrip("/")
        self.access_token = access_token or os.environ.get("MASTODON_ACCESS_TOKEN")

    async def post(self, text: str, media: list[str] | None = None, **kwargs) -> SocialPost:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.instance_url}/api/v1/statuses",
                headers={"Authorization": f"Bearer {self.access_token}"},
                json={"status": text},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return SocialPost(
                id=data["id"],
                text=text,
                url=data.get("url"),
                raw=data,
            )

    async def get_timeline(self, limit: int = 20, **kwargs) -> list[SocialPost]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.instance_url}/api/v1/timelines/home",
                headers={"Authorization": f"Bearer {self.access_token}"},
                params={"limit": limit},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return [
                SocialPost(
                    id=status["id"],
                    text=status.get("content", ""),
                    author=status["account"]["username"],
                    created_at=datetime.fromisoformat(status["created_at"].replace("Z", "+00:00"))
                    if status.get("created_at") else None,
                    likes=status.get("favourites_count", 0),
                    shares=status.get("reblogs_count", 0),
                    comments=status.get("replies_count", 0),
                    url=status.get("url"),
                    raw=status,
                )
                for status in data
            ]


# ═══════════════════════════════════════════════════════════════════════════════
# REDDIT
# ═══════════════════════════════════════════════════════════════════════════════

class RedditConnector(SocialConnector):
    """Reddit API connector."""

    name = "reddit"

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ):
        self.client_id = client_id or os.environ.get("REDDIT_CLIENT_ID")
        self.client_secret = client_secret or os.environ.get("REDDIT_CLIENT_SECRET")
        self.username = username or os.environ.get("REDDIT_USERNAME")
        self.password = password or os.environ.get("REDDIT_PASSWORD")
        self.access_token: str | None = None
        self.base_url = "https://oauth.reddit.com"

    async def _ensure_auth(self) -> str:
        if self.access_token:
            return self.access_token

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=(self.client_id, self.client_secret),
                data={
                    "grant_type": "password",
                    "username": self.username,
                    "password": self.password,
                },
                headers={"User-Agent": "HyperClaw/1.0"},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            self.access_token = data["access_token"]
            return self.access_token

    async def post(self, text: str, media: list[str] | None = None, **kwargs) -> SocialPost:
        token = await self._ensure_auth()
        subreddit = kwargs.get("subreddit", "test")
        title = kwargs.get("title", text[:100])

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/api/submit",
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "HyperClaw/1.0",
                },
                data={
                    "sr": subreddit,
                    "kind": "self",
                    "title": title,
                    "text": text,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return SocialPost(
                id=data["json"]["data"]["id"],
                text=text,
                url=data["json"]["data"]["url"],
                raw=data,
            )

    async def get_timeline(self, limit: int = 20, **kwargs) -> list[SocialPost]:
        token = await self._ensure_auth()
        subreddit = kwargs.get("subreddit", "all")

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/r/{subreddit}/hot",
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "HyperClaw/1.0",
                },
                params={"limit": limit},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return [
                SocialPost(
                    id=post["data"]["id"],
                    text=post["data"].get("selftext", ""),
                    author=post["data"].get("author"),
                    likes=post["data"].get("ups", 0),
                    comments=post["data"].get("num_comments", 0),
                    url=f"https://reddit.com{post['data']['permalink']}",
                    raw=post["data"],
                )
                for post in data["data"]["children"]
            ]


# ═══════════════════════════════════════════════════════════════════════════════
# TIKTOK
# ═══════════════════════════════════════════════════════════════════════════════

class TikTokConnector(SocialConnector):
    """TikTok API connector (Content Posting API)."""

    name = "tiktok"

    def __init__(self, access_token: str | None = None):
        self.access_token = access_token or os.environ.get("TIKTOK_ACCESS_TOKEN")
        self.base_url = "https://open.tiktokapis.com/v2"

    async def post(self, text: str, media: list[str] | None = None, **kwargs) -> SocialPost:
        # TikTok requires video content
        if not media:
            raise ValueError("TikTok requires video content")

        async with httpx.AsyncClient() as client:
            # Initialize video upload
            response = await client.post(
                f"{self.base_url}/post/publish/content/init/",
                headers={"Authorization": f"Bearer {self.access_token}"},
                json={
                    "post_info": {
                        "title": text[:150],
                        "privacy_level": "PUBLIC_TO_EVERYONE",
                    },
                    "source_info": {
                        "source": "PULL_FROM_URL",
                        "video_url": media[0],
                    },
                },
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()

            return SocialPost(id=data.get("publish_id", ""), text=text, raw=data)

    async def get_timeline(self, limit: int = 20, **kwargs) -> list[SocialPost]:
        # TikTok doesn't provide a timeline API for third-party apps
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# PROVIDER FACTORY
# ═══════════════════════════════════════════════════════════════════════════════

SOCIAL_CONNECTORS: dict[str, type[SocialConnector]] = {
    "twitter": TwitterConnector,
    "x": TwitterConnector,
    "linkedin": LinkedInConnector,
    "instagram": InstagramConnector,
    "facebook": FacebookConnector,
    "youtube": YouTubeConnector,
    "bluesky": BlueskyConnector,
    "mastodon": MastodonConnector,
    "reddit": RedditConnector,
    "tiktok": TikTokConnector,
}


def get_social_connector(name: str, **kwargs) -> SocialConnector:
    """Get a social media connector by name."""
    name = name.lower()

    if name not in SOCIAL_CONNECTORS:
        available = ", ".join(sorted(SOCIAL_CONNECTORS.keys()))
        raise ValueError(f"Unknown social connector: {name}. Available: {available}")

    return SOCIAL_CONNECTORS[name](**kwargs)
