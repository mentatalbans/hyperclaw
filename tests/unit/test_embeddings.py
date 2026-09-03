"""
Unit tests for core/providers/embeddings.py — BedrockEmbedder and factory.
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, patch

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from core.providers.embeddings import BedrockEmbedder, get_embedding_provider, EMBEDDING_PROVIDERS


def _make_bedrock_embedder(mock_client: MagicMock) -> BedrockEmbedder:
    with patch("boto3.client", return_value=mock_client):
        return BedrockEmbedder()


def _mock_response(embedding: list[float]) -> dict:
    body = MagicMock()
    body.read.return_value = json.dumps({"embedding": embedding}).encode()
    return {"body": body}


class TestBedrockEmbedder:
    @pytest.mark.asyncio
    async def test_embed_returns_1024_floats(self):
        mock_client = MagicMock()
        mock_client.invoke_model.return_value = _mock_response([0.1] * 1024)
        embedder = _make_bedrock_embedder(mock_client)
        result = await embedder.embed("hello world")
        assert len(result) == 1024
        assert all(isinstance(v, float) for v in result)

    @pytest.mark.asyncio
    async def test_embed_calls_correct_model(self):
        mock_client = MagicMock()
        mock_client.invoke_model.return_value = _mock_response([0.0] * 1024)
        embedder = _make_bedrock_embedder(mock_client)
        await embedder.embed("test")
        call_kwargs = mock_client.invoke_model.call_args[1]
        assert call_kwargs["modelId"] == "amazon.titan-embed-text-v2:0"

    @pytest.mark.asyncio
    async def test_embed_sends_dimension_1024(self):
        mock_client = MagicMock()
        mock_client.invoke_model.return_value = _mock_response([0.0] * 1024)
        embedder = _make_bedrock_embedder(mock_client)
        await embedder.embed("test")
        body = json.loads(mock_client.invoke_model.call_args[1]["body"])
        assert body["dimensions"] == 1024

    @pytest.mark.asyncio
    async def test_embed_batch_loops_per_item(self):
        mock_client = MagicMock()
        mock_client.invoke_model.return_value = _mock_response([0.5] * 1024)
        embedder = _make_bedrock_embedder(mock_client)
        results = await embedder.embed_batch(["a", "b", "c"])
        assert len(results) == 3
        assert mock_client.invoke_model.call_count == 3

    def test_dimension_is_1024(self):
        mock_client = MagicMock()
        embedder = _make_bedrock_embedder(mock_client)
        assert embedder.dimension == 1024

    def test_registered_in_providers(self):
        assert "bedrock" in EMBEDDING_PROVIDERS
        assert EMBEDDING_PROVIDERS["bedrock"] is BedrockEmbedder

    def test_factory_returns_bedrock_embedder(self):
        mock_client = MagicMock()
        with patch("boto3.client", return_value=mock_client):
            provider = get_embedding_provider("bedrock")
        assert isinstance(provider, BedrockEmbedder)
        assert provider.dimension == 1024
