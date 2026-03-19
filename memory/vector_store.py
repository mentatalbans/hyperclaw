"""
HyperMemory Vector Store — pgvector-backed semantic memory for HyperClaw agents.
Stub implementation for v0.1.0-alpha. Full vector operations in v0.2.0.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MemoryEntry:
    id: str
    content: str
    embedding: list[float] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class VectorStore:
    """pgvector-backed semantic memory. Stub for v0.1.0-alpha."""

    async def insert(self, entry: MemoryEntry) -> None:
        raise NotImplementedError("VectorStore will be implemented in v0.2.0")

    async def search(self, query_embedding: list[float], top_k: int = 5) -> list[MemoryEntry]:
        raise NotImplementedError("VectorStore will be implemented in v0.2.0")
