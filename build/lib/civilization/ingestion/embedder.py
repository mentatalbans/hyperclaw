"""
Embedding generation for Civilization Knowledge Layer.
Supports multiple embedding providers with a unified interface.
"""
from __future__ import annotations
import os
import logging
from abc import ABC, abstractmethod
from typing import Any
import hashlib
import json

logger = logging.getLogger(__name__)


class EmbeddingProvider(ABC):
    """Abstract base for embedding providers."""

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        pass

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        pass

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Return the embedding dimension."""
        pass


class OpenAIEmbedder(EmbeddingProvider):
    """OpenAI embedding provider using text-embedding-3-small."""

    def __init__(self, api_key: str | None = None, model: str = "text-embedding-3-small"):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.model = model
        self._dimension = 1536 if "small" in model else 3072

    async def embed(self, text: str) -> list[float]:
        """Generate embedding for a single text using OpenAI."""
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"input": text, "model": self.model},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            return data["data"][0]["embedding"]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        import httpx

        # OpenAI supports batches of up to 2048 inputs
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"input": texts, "model": self.model},
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()
            # Sort by index to maintain order
            sorted_data = sorted(data["data"], key=lambda x: x["index"])
            return [item["embedding"] for item in sorted_data]

    @property
    def dimension(self) -> int:
        return self._dimension


class VoyageEmbedder(EmbeddingProvider):
    """Voyage AI embedding provider."""

    def __init__(self, api_key: str | None = None, model: str = "voyage-2"):
        self.api_key = api_key or os.environ.get("VOYAGE_API_KEY")
        self.model = model
        self._dimension = 1024

    async def embed(self, text: str) -> list[float]:
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.voyageai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"input": text, "model": self.model},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            return data["data"][0]["embedding"]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.voyageai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"input": texts, "model": self.model},
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()
            sorted_data = sorted(data["data"], key=lambda x: x["index"])
            return [item["embedding"] for item in sorted_data]

    @property
    def dimension(self) -> int:
        return self._dimension


class MockEmbedder(EmbeddingProvider):
    """Mock embedder for testing. Generates deterministic embeddings based on text hash."""

    def __init__(self, dimension: int = 1536):
        self._dimension = dimension

    async def embed(self, text: str) -> list[float]:
        """Generate deterministic mock embedding from text hash."""
        hash_bytes = hashlib.sha256(text.encode()).digest()
        # Expand hash to fill dimension
        embedding = []
        for i in range(self._dimension):
            byte_idx = i % len(hash_bytes)
            # Normalize to [-1, 1]
            value = (hash_bytes[byte_idx] / 127.5) - 1.0
            embedding.append(value)
        return embedding

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(text) for text in texts]

    @property
    def dimension(self) -> int:
        return self._dimension


class CivilizationEmbedder:
    """
    Main embedder class for Civilization Knowledge Layer.
    Handles caching, batching, and provider selection.
    """

    def __init__(
        self,
        provider: EmbeddingProvider | None = None,
        cache_enabled: bool = True,
    ):
        self.provider = provider or MockEmbedder()
        self.cache_enabled = cache_enabled
        self._cache: dict[str, list[float]] = {}

    def _cache_key(self, text: str) -> str:
        """Generate cache key for text."""
        return hashlib.sha256(text.encode()).hexdigest()[:32]

    async def embed(self, text: str) -> list[float]:
        """Generate embedding for text with caching."""
        if self.cache_enabled:
            key = self._cache_key(text)
            if key in self._cache:
                return self._cache[key]

        embedding = await self.provider.embed(text)

        if self.cache_enabled:
            self._cache[key] = embedding

        return embedding

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts with caching."""
        if not self.cache_enabled:
            return await self.provider.embed_batch(texts)

        # Check cache for existing embeddings
        results: list[list[float] | None] = [None] * len(texts)
        texts_to_embed: list[tuple[int, str]] = []

        for i, text in enumerate(texts):
            key = self._cache_key(text)
            if key in self._cache:
                results[i] = self._cache[key]
            else:
                texts_to_embed.append((i, text))

        # Embed uncached texts
        if texts_to_embed:
            indices, uncached_texts = zip(*texts_to_embed)
            new_embeddings = await self.provider.embed_batch(list(uncached_texts))

            for idx, embedding, text in zip(indices, new_embeddings, uncached_texts):
                results[idx] = embedding
                self._cache[self._cache_key(text)] = embedding

        return results  # type: ignore

    @property
    def dimension(self) -> int:
        """Return embedding dimension from provider."""
        return self.provider.dimension

    def clear_cache(self) -> None:
        """Clear the embedding cache."""
        self._cache.clear()

    @classmethod
    def create(cls, provider_name: str = "mock", **kwargs) -> "CivilizationEmbedder":
        """Factory method to create embedder with specified provider."""
        providers = {
            "openai": OpenAIEmbedder,
            "voyage": VoyageEmbedder,
            "mock": MockEmbedder,
        }
        provider_cls = providers.get(provider_name, MockEmbedder)
        provider = provider_cls(**kwargs)
        return cls(provider=provider)
