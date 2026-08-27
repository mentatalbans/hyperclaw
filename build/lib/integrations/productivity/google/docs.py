"""GoogleDocsConnector — Google Docs API integration."""

from __future__ import annotations

import logging

import httpx

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
)
from integrations.productivity.google.base import GoogleOAuthBase

logger = logging.getLogger("hyperclaw.integrations.google_docs")


class GoogleDocsConnector(BaseConnector, GoogleOAuthBase):
    """
    Google Docs API connector.

    Required config (via GoogleOAuthBase):
        - access_token or credentials_file
    """

    BASE_URL = "https://docs.googleapis.com/v1"
    DRIVE_URL = "https://www.googleapis.com/drive/v3"

    def __init__(self, config: dict) -> None:
        self.config = config
        self._init_google_auth(config)
        self._validate_google_config(config)

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="google_docs",
            platform="google_docs",
            category="productivity",
            capabilities=frozenset(
                [
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.ACTION,
                ]
            ),
            auth_type="oauth2",
            rate_limit_per_minute=60,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        return await self._get_google_client()

    async def health(self) -> bool:
        """Check if Docs API is accessible."""
        try:
            client = await self._get_client()
            # Use Drive API to check access since Docs doesn't have a simple endpoint
            response = await client.get(
                f"{self.DRIVE_URL}/about",
                params={"fields": "user"},
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Google Docs health check failed: {e}")
            return False

    async def get_document(self, document_id: str) -> dict:
        """Get a document's content and metadata."""
        client = await self._get_client()

        response = await client.get(f"{self.BASE_URL}/documents/{document_id}")

        if response.status_code != 200:
            raise Exception(f"Docs get document failed: {response.text}")

        return response.json()

    async def create_document(
        self,
        title: str,
        content: str = "",
    ) -> dict:
        """Create a new document."""
        client = await self._get_client()

        # Create empty document
        response = await client.post(
            f"{self.BASE_URL}/documents",
            json={"title": title},
        )

        if response.status_code not in (200, 201):
            raise Exception(f"Docs create document failed: {response.text}")

        doc = response.json()

        # If content provided, append it
        if content:
            await self.append_text(doc["documentId"], content)

        return doc

    async def append_text(
        self,
        document_id: str,
        text: str,
    ) -> dict:
        """Append text to a document."""
        client = await self._get_client()

        requests = [
            {
                "insertText": {
                    "location": {"index": 1},
                    "text": text,
                }
            }
        ]

        response = await client.post(
            f"{self.BASE_URL}/documents/{document_id}:batchUpdate",
            json={"requests": requests},
        )

        if response.status_code != 200:
            raise Exception(f"Docs append text failed: {response.text}")

        return response.json()

    async def replace_text(
        self,
        document_id: str,
        find: str,
        replace: str,
    ) -> dict:
        """Replace text in a document."""
        client = await self._get_client()

        requests = [
            {
                "replaceAllText": {
                    "containsText": {"text": find, "matchCase": True},
                    "replaceText": replace,
                }
            }
        ]

        response = await client.post(
            f"{self.BASE_URL}/documents/{document_id}:batchUpdate",
            json={"requests": requests},
        )

        if response.status_code != 200:
            raise Exception(f"Docs replace text failed: {response.text}")

        return response.json()

    async def export_as_pdf(self, document_id: str) -> bytes:
        """Export document as PDF."""
        client = await self._get_client()

        response = await client.get(
            f"{self.DRIVE_URL}/files/{document_id}/export",
            params={"mimeType": "application/pdf"},
        )

        if response.status_code != 200:
            raise Exception(f"Docs export as PDF failed: {response.text}")

        return response.content

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read a document."""
        return await self.get_document(resource_id)

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Create a document."""
        return await self.create_document(
            title=data.get("title", "Untitled"),
            content=data.get("content", ""),
        )

    async def _action_impl(self, action_name: str, params: dict) -> dict:
        """Execute Docs-specific actions."""
        if action_name == "append_text":
            return await self.append_text(
                params["document_id"],
                params["text"],
            )
        elif action_name == "replace_text":
            return await self.replace_text(
                params["document_id"],
                params["find"],
                params["replace"],
            )
        elif action_name == "export_pdf":
            pdf_bytes = await self.export_as_pdf(params["document_id"])
            return {"size": len(pdf_bytes), "format": "pdf"}

        raise ValueError(f"Unknown action: {action_name}")
