"""GoogleOAuthBase — shared OAuth2 authentication for Google Workspace connectors."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    pass

logger = logging.getLogger("hyperclaw.integrations.google")


class GoogleOAuthBase:
    """
    Mixin class providing OAuth2 authentication for Google Workspace APIs.

    Config options:
        - credentials_file: Path to service account JSON or OAuth2 credentials
        - access_token: Direct access token (for testing or pre-authenticated)
        - refresh_token: OAuth2 refresh token
        - client_id: OAuth2 client ID (for refresh)
        - client_secret: OAuth2 client secret (for refresh)
    """

    OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"

    def _init_google_auth(self, config: dict) -> None:
        """Initialize Google OAuth configuration."""
        self._credentials_file = config.get("credentials_file", "")
        self._access_token = config.get("access_token", "")
        self._refresh_token = config.get("refresh_token", "")
        self._client_id = config.get("client_id", "")
        self._client_secret = config.get("client_secret", "")
        self._google_client: httpx.AsyncClient | None = None

    def _get_headers(self) -> dict[str, str]:
        """Get authorization headers for Google API requests."""
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    async def _get_google_client(self) -> httpx.AsyncClient:
        """Get or create an authenticated httpx client."""
        if self._google_client is None:
            self._google_client = httpx.AsyncClient(
                timeout=30.0,
                headers=self._get_headers(),
            )
        return self._google_client

    async def _refresh_access_token(self) -> str:
        """Refresh the OAuth2 access token."""
        if not self._refresh_token or not self._client_id or not self._client_secret:
            raise ValueError("Cannot refresh token: missing credentials")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.OAUTH_TOKEN_URL,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": self._refresh_token,
                    "grant_type": "refresh_token",
                },
            )

            if response.status_code != 200:
                raise Exception(f"Token refresh failed: {response.text}")

            data = response.json()
            self._access_token = data.get("access_token", "")

            # Update the client headers
            if self._google_client:
                self._google_client.headers["Authorization"] = f"Bearer {self._access_token}"

            return self._access_token

    def _validate_google_config(self, config: dict) -> None:
        """Validate that required Google credentials are present."""
        if config.get("enabled", False):
            if not config.get("access_token") and not config.get("credentials_file"):
                raise ValueError(
                    "Google connector requires access_token or credentials_file"
                )
