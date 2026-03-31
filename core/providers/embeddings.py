"""
Embedding Provider Abstraction Layer.
Unified interface for text embedding providers.
"""
from __future__ import annotations

import os
import logging
from abc import ABC, abstractmethod

import httpx

logger = logging.getLogger(__name__)


class EmbeddingProvider(ABC):
    """Abstract base for embedding providers."""

    name: str = "base"

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


# ═══════════════════════════════════════════════════════════════════════════════
# OPENAI EMBEDDINGS
# ═══════════════════════════════════════════════════════════════════════════════

class OpenAIEmbedder(EmbeddingProvider):
    """OpenAI text embeddings."""

    name = "openai"

    MODELS = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    def __init__(self, api_key: str | None = None, model: str = "text-embedding-3-small"):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.model = model
        self._dimension = self.MODELS.get(model, 1536)
        self.base_url = "https://api.openai.com/v1"

    async def embed(self, text: str) -> list[float]:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"input": text, "model": self.model},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            return data["data"][0]["embedding"]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
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


# ═══════════════════════════════════════════════════════════════════════════════
# VOYAGE EMBEDDINGS
# ═══════════════════════════════════════════════════════════════════════════════

class VoyageEmbedder(EmbeddingProvider):
    """Voyage AI embeddings."""

    name = "voyage"

    MODELS = {
        "voyage-3": 1024,
        "voyage-3-lite": 512,
        "voyage-code-3": 1024,
        "voyage-2": 1024,
    }

    def __init__(self, api_key: str | None = None, model: str = "voyage-3"):
        self.api_key = api_key or os.environ.get("VOYAGE_API_KEY")
        self.model = model
        self._dimension = self.MODELS.get(model, 1024)
        self.base_url = "https://api.voyageai.com/v1"

    async def embed(self, text: str) -> list[float]:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"input": text, "model": self.model},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            return data["data"][0]["embedding"]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
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


# ═══════════════════════════════════════════════════════════════════════════════
# GEMINI EMBEDDINGS
# ═══════════════════════════════════════════════════════════════════════════════

class GeminiEmbedder(EmbeddingProvider):
    """Google Gemini embeddings."""

    name = "gemini"

    def __init__(self, api_key: str | None = None, model: str = "text-embedding-004"):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.model = model
        self._dimension = 768
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"

    async def embed(self, text: str) -> list[float]:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/models/{self.model}:embedContent",
                params={"key": self.api_key},
                json={"content": {"parts": [{"text": text}]}},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            return data["embedding"]["values"]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        # Gemini doesn't have native batch embedding, so we process sequentially
        results = []
        for text in texts:
            embedding = await self.embed(text)
            results.append(embedding)
        return results

    @property
    def dimension(self) -> int:
        return self._dimension


# ═══════════════════════════════════════════════════════════════════════════════
# MISTRAL EMBEDDINGS
# ═══════════════════════════════════════════════════════════════════════════════

class MistralEmbedder(EmbeddingProvider):
    """Mistral AI embeddings."""

    name = "mistral"

    def __init__(self, api_key: str | None = None, model: str = "mistral-embed"):
        self.api_key = api_key or os.environ.get("MISTRAL_API_KEY")
        self.model = model
        self._dimension = 1024
        self.base_url = "https://api.mistral.ai/v1"

    async def embed(self, text: str) -> list[float]:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"input": [text], "model": self.model},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            return data["data"][0]["embedding"]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
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


# ═══════════════════════════════════════════════════════════════════════════════
# COHERE EMBEDDINGS
# ═══════════════════════════════════════════════════════════════════════════════

class CohereEmbedder(EmbeddingProvider):
    """Cohere embeddings."""

    name = "cohere"

    MODELS = {
        "embed-english-v3.0": 1024,
        "embed-multilingual-v3.0": 1024,
        "embed-english-light-v3.0": 384,
        "embed-multilingual-light-v3.0": 384,
    }

    def __init__(self, api_key: str | None = None, model: str = "embed-english-v3.0"):
        self.api_key = api_key or os.environ.get("COHERE_API_KEY")
        self.model = model
        self._dimension = self.MODELS.get(model, 1024)
        self.base_url = "https://api.cohere.ai/v1"

    async def embed(self, text: str) -> list[float]:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/embed",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "texts": [text],
                    "model": self.model,
                    "input_type": "search_document",
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            return data["embeddings"][0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/embed",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "texts": texts,
                    "model": self.model,
                    "input_type": "search_document",
                },
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()
            return data["embeddings"]

    @property
    def dimension(self) -> int:
        return self._dimension


# ═══════════════════════════════════════════════════════════════════════════════
# JINA EMBEDDINGS
# ═══════════════════════════════════════════════════════════════════════════════

class JinaEmbedder(EmbeddingProvider):
    """Jina AI embeddings."""

    name = "jina"

    def __init__(self, api_key: str | None = None, model: str = "jina-embeddings-v3"):
        self.api_key = api_key or os.environ.get("JINA_API_KEY")
        self.model = model
        self._dimension = 1024
        self.base_url = "https://api.jina.ai/v1"

    async def embed(self, text: str) -> list[float]:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"input": [text], "model": self.model},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            return data["data"][0]["embedding"]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"input": texts, "model": self.model},
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()
            return [item["embedding"] for item in data["data"]]

    @property
    def dimension(self) -> int:
        return self._dimension


# ═══════════════════════════════════════════════════════════════════════════════
# PROVIDER FACTORY
# ═══════════════════════════════════════════════════════════════════════════════

EMBEDDING_PROVIDERS: dict[str, type[EmbeddingProvider]] = {
    "openai": OpenAIEmbedder,
    "voyage": VoyageEmbedder,
    "gemini": GeminiEmbedder,
    "google": GeminiEmbedder,
    "mistral": MistralEmbedder,
    "cohere": CohereEmbedder,
    "jina": JinaEmbedder,
}


def get_embedding_provider(name: str | None = None, **kwargs) -> EmbeddingProvider:
    """Get an embedding provider by name."""
    name = name or os.environ.get("HYPERCLAW_EMBEDDING_PROVIDER", "openai")
    name = name.lower()

    if name not in EMBEDDING_PROVIDERS:
        available = ", ".join(sorted(EMBEDDING_PROVIDERS.keys()))
        raise ValueError(f"Unknown embedding provider: {name}. Available: {available}")

    return EMBEDDING_PROVIDERS[name](**kwargs)
