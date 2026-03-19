"""MicrosoftOAuthBase — shared OAuth2 authentication for Microsoft 365 connectors."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("hyperclaw.integrations.microsoft")


class MicrosoftOAuthBase:
    """
    Mixin class providing OAuth2 authentication for Microsoft Graph API.

    Config options:
        - access_token: Direct access token
        - client_id: Azure AD app client ID
        - client_secret: Azure AD app client secret
        - tenant_id: Azure AD tenant ID
    """

    GRAPH_URL = "https://graph.microsoft.com/v1.0"
    TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"

    def _init_microsoft_auth(self, config: dict) -> None:
        """Initialize Microsoft OAuth configuration."""
        self._ms_access_token = config.get("access_token", "")
        self._client_id = config.get("client_id", "")
        self._client_secret = config.get("client_secret", "")
        self._tenant_id = config.get("tenant_id", "common")
        self._ms_client: httpx.AsyncClient | None = None

    def _get_headers(self) -> dict[str, str]:
        """Get authorization headers for Microsoft Graph API requests."""
        return {
            "Authorization": f"Bearer {self._ms_access_token}",
            "Content-Type": "application/json",
        }

    async def _get_microsoft_client(self) -> httpx.AsyncClient:
        """Get or create an authenticated httpx client."""
        if self._ms_client is None:
            self._ms_client = httpx.AsyncClient(
                timeout=30.0,
                headers=self._get_headers(),
            )
        return self._ms_client

    async def _get_access_token(self) -> str:
        """Get access token using client credentials flow."""
        if self._ms_access_token:
            return self._ms_access_token

        if not self._client_id or not self._client_secret:
            raise ValueError("Cannot get token: missing client_id or client_secret")

        token_url = self.TOKEN_URL.format(tenant=self._tenant_id)

        async with httpx.AsyncClient() as client:
            response = await client.post(
                token_url,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "scope": "https://graph.microsoft.com/.default",
                    "grant_type": "client_credentials",
                },
            )

            if response.status_code != 200:
                raise Exception(f"Token request failed: {response.text}")

            data = response.json()
            self._ms_access_token = data.get("access_token", "")

            # Update the client headers
            if self._ms_client:
                self._ms_client.headers["Authorization"] = f"Bearer {self._ms_access_token}"

            return self._ms_access_token

    def _validate_microsoft_config(self, config: dict) -> None:
        """Validate that required Microsoft credentials are present."""
        if config.get("enabled", False):
            has_token = bool(config.get("access_token"))
            has_client_creds = bool(
                config.get("client_id") and config.get("client_secret")
            )
            if not has_token and not has_client_creds:
                raise ValueError(
                    "Microsoft connector requires access_token OR (client_id + client_secret)"
                )
