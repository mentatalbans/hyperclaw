"""
HyperClaw Memory Manager
Handles memory persistence across sessions with database + file fallback.
"""

import asyncio
from collections import Counter
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass, field
import hashlib
import json
import logging
import os
import re
import sqlite3
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("hyperclaw.memory")

# Paths
HYPERCLAW_ROOT = Path(os.environ.get("HYPERCLAW_ROOT", Path.home() / ".hyperclaw"))
MEMORY_PATH = HYPERCLAW_ROOT / "memory"
WORKSPACE_PATH = HYPERCLAW_ROOT / "workspace"


@dataclass
class Memory:
    """A single memory entry."""
    id: str
    content: str
    memory_type: str  # 'episode', 'semantic', 'instinct', 'dream'
    domain: Optional[str] = None
    importance: float = 0.5
    summary: Optional[str] = None
    source: str = "conversation"
    is_core: bool = False
    metadata: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    embedding: Optional[list] = None


class MemoryManager:
    """
    Manages memory persistence across sessions.
    Uses database when available, falls back to file storage.
    """

    def __init__(self, db_pool=None, root: Optional[Path] = None):
        self.db_pool = db_pool
        self.root = Path(root) if root is not None else Path(
            os.environ.get("HYPERCLAW_ROOT", HYPERCLAW_ROOT)
        )
        self.memory_path = self.root / "memory"
        self.workspace_path = self.root / "workspace"
        self._file_cache: dict[str, list[Memory]] = {}
        self._file_memory_lock = asyncio.Lock()
        self._file_import_lock = asyncio.Lock()
        self._conversation_history: dict[str, list[dict]] = {}
        self._embeddings_client = None

    async def initialize(self):
        """Initialize the memory manager."""
        # Ensure directories exist
        self.memory_path.mkdir(parents=True, exist_ok=True)
        (self.memory_path / "daily").mkdir(exist_ok=True)

        # Load file-based memories
        await self._load_file_memories()

        logger.info("Memory manager initialized")

    async def _load_file_memories(self):
        """Load structured memories and existing Markdown context."""
        self._file_cache.clear()
        entries = []
        for path in sorted((self.memory_path / "entries").glob("*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            record["created_at"] = datetime.fromisoformat(record["created_at"])
            entries.append(Memory(**record))
        self._file_cache["entries"] = entries

        # Load instincts
        instincts_file = self.memory_path / "instincts.md"
        if instincts_file.exists():
            content = instincts_file.read_text(encoding="utf-8")
            self._file_cache["instincts"] = self._parse_markdown_list(content, "instinct")

        # Load core episodes
        episodes_file = self.memory_path / "core-episodes.md"
        if episodes_file.exists():
            content = episodes_file.read_text(encoding="utf-8")
            self._file_cache["episodes"] = self._parse_markdown_list(content, "episode")

        # Load working memory
        memory_file = self.workspace_path / "MEMORY.md"
        if memory_file.exists():
            content = memory_file.read_text(encoding="utf-8")
            self._file_cache["working"] = self._parse_markdown_list(content, "semantic")

    def _parse_markdown_list(self, content: str, memory_type: str) -> list[Memory]:
        """Parse markdown bullet points into Memory objects."""
        memories = []
        lines = content.split("\n")

        for i, line in enumerate(lines):
            line = line.strip()
            if line.startswith("- ") or line.startswith("* "):
                text = line[2:].strip()
                if text and not text.startswith("*"):  # Skip formatting markers
                    memories.append(Memory(
                        id=f"file_{memory_type}_{i}",
                        content=text,
                        memory_type=memory_type,
                        source="file",
                        is_core=(memory_type in ["instinct", "episode"]),
                    ))

        return memories

    # =========================================================================
    # CONVERSATION HISTORY
    # =========================================================================

    def get_conversation_history(self, session_id: str, limit: int = 50) -> list[dict]:
        """Get an independent snapshot of a session's recent history."""
        history = self._conversation_history.get(session_id, [])
        return deepcopy(history[-limit:]) if limit > 0 else []

    def add_message(self, session_id: str, role: str, content: str, metadata: dict = None):
        """Add a message to conversation history."""
        if session_id not in self._conversation_history:
            self._conversation_history[session_id] = []

        message = {
            "id": str(uuid.uuid4()),
            "role": role,
            "content": deepcopy(content),
            "timestamp": datetime.now().isoformat(),
            **deepcopy(metadata or {})
        }

        self._conversation_history[session_id].append(message)

        # Trim to max size
        max_history = 100
        if len(self._conversation_history[session_id]) > max_history:
            self._conversation_history[session_id] = self._conversation_history[session_id][-max_history:]

    async def save_conversation(self, session_id: str):
        """Persist conversation to storage."""
        history = deepcopy(self._conversation_history.get(session_id, []))

        if self.db_pool:
            await self._save_conversation_db(session_id, history)
        else:
            await self._save_conversation_file(session_id, history)

    async def append_messages(self, session_id: str, messages: list[dict]) -> None:
        """Persist appended messages before publishing the cache under the caller's session lock."""
        history = deepcopy(self._conversation_history.get(session_id, []))
        for message in messages:
            history.append({
                "id": str(uuid.uuid4()),
                "timestamp": datetime.now().isoformat(),
                **deepcopy(message),
            })
        history = history[-100:]
        if self.db_pool:
            await self._save_conversation_db(session_id, history)
        else:
            await self._save_conversation_file(session_id, history)
        self._conversation_history[session_id] = history

    async def _save_conversation_db(self, session_id: str, history: list[dict]):
        """Save conversation to database."""
        async with self.db_pool.acquire() as conn:
            async with conn.transaction():
                conv = await self._ensure_conversation_db(conn, session_id)
                # Stable IDs make repeated saves idempotent without removing older
                # messages or their existing usage/model columns.
                for msg in history:
                    plain_text = isinstance(msg["content"], str)
                    stored_metadata = {"_hyperclaw_message": {
                        "version": 1,
                        "content_format": "text" if plain_text else "json",
                        "metadata": {key: value for key, value in msg.items()
                                     if key not in {"id", "role", "content", "timestamp"}},
                    }}
                    await conn.execute(
                        """
                        INSERT INTO messages (id, conversation_id, role, content, created_at, metadata)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        msg["id"],
                        conv["id"],
                        msg["role"],
                        msg["content"] if plain_text else json.dumps(msg["content"]),
                        datetime.fromisoformat(msg["timestamp"]),
                        json.dumps(stored_metadata),
                    )

    async def _ensure_conversation_db(self, conn, session_id: str):
        """Find or create a session under the caller's database transaction."""
        # The existing schema does not require unique session IDs.
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", session_id)
        conv = await conn.fetchrow(
            "SELECT id FROM conversations WHERE session_id = $1 ORDER BY created_at LIMIT 1", session_id
        )
        if conv is None:
            conv = await conn.fetchrow(
                """
                INSERT INTO conversations (session_id, channel, last_message_at)
                VALUES ($1, 'api', NOW()) RETURNING id
                """, session_id,
            )
        else:
            await conn.execute(
                "UPDATE conversations SET last_message_at = NOW() WHERE id = $1", conv["id"]
            )
        return conv

    async def _save_conversation_file(self, session_id: str, history: list[dict]):
        """Save conversation to file."""
        self._write_json_atomic(self._conversation_path(session_id), history)

    def _conversation_path(self, session_id: str) -> Path:
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        # A separate directory prevents a legacy basename from impersonating a hash.
        return self.memory_path / "conversations" / "sessions" / f"{digest}.json"

    @staticmethod
    def _write_json_atomic(path: Path, value):
        """Commit a complete JSON document, leaving existing data intact on failure."""
        payload = json.dumps(value, indent=2, ensure_ascii=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, prefix=".pending-", delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    async def load_conversation(self, session_id: str) -> list[dict]:
        """Load conversation from storage."""
        if self.db_pool:
            history = await self._load_conversation_db(session_id)
        else:
            history = await self._load_conversation_file(session_id)
        self._conversation_history[session_id] = deepcopy(history[-100:])
        return deepcopy(self._conversation_history[session_id])

    async def conversation_exists(self, session_id: str) -> bool:
        """Whether a session was persisted, including an empty or reset session."""
        if self.db_pool:
            async with self.db_pool.acquire() as conn:
                return await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM conversations WHERE session_id = $1)", session_id
                )
        return self._existing_conversation_path(session_id) is not None

    async def clear_conversation(self, session_id: str):
        """Persist reset before clearing the cache, including legacy sessions."""
        if self.db_pool:
            async with self.db_pool.acquire() as conn:
                async with conn.transaction():
                    await self._ensure_conversation_db(conn, session_id)
                    await conn.execute(
                        """
                        DELETE FROM messages WHERE conversation_id IN
                            (SELECT id FROM conversations WHERE session_id = $1)
                        """,
                        session_id,
                    )
        else:
            # An empty snapshot takes precedence over any older legacy filename.
            await self._save_conversation_file(session_id, [])
        self._conversation_history[session_id] = []

    async def _load_conversation_db(self, session_id: str) -> list[dict]:
        """Load conversation from database."""
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT m.id, m.role, m.content, m.created_at, m.metadata
                FROM messages m
                JOIN conversations c ON m.conversation_id = c.id
                WHERE c.session_id = $1
                ORDER BY m.created_at DESC
                LIMIT 100
                """,
                session_id
            )
            history = []
            for row in reversed(rows):
                metadata = json.loads(row["metadata"] or "{}")
                content = row["content"]
                envelope = metadata.get("_hyperclaw_message")
                if isinstance(envelope, dict) and envelope.get("version") == 1:
                    metadata = envelope["metadata"]
                    if envelope["content_format"] == "json":
                        content = json.loads(content)
                history.append({
                    **metadata, "id": str(row["id"]), "role": row["role"], "content": content,
                    "timestamp": row["created_at"].isoformat(),
                })
            return history

    async def _load_conversation_file(self, session_id: str) -> list[dict]:
        """Load conversation from file."""
        filepath = self._existing_conversation_path(session_id)
        if filepath is None:
            return []
        return json.loads(filepath.read_text(encoding="utf-8"))

    def _existing_conversation_path(self, session_id: str) -> Optional[Path]:
        filepath = self._conversation_path(session_id)
        if not filepath.exists():
            # Only a bounded, simple basename can reference the previous layout.
            if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,199}", session_id):
                return None
            filepath = self.memory_path / "conversations" / f"{session_id}.json"
        if filepath.is_symlink() or not filepath.exists():
            return None
        return filepath

    # =========================================================================
    # MEMORY OPERATIONS
    # =========================================================================

    async def remember(
        self,
        content: str,
        memory_type: str = "episode",
        domain: str = None,
        importance: float = 0.5,
        is_core: bool = False,
        metadata: dict = None,
        source: str = "conversation",
    ) -> str:
        """Store a new memory."""
        memory = Memory(
            id=str(uuid.uuid4()),
            content=content,
            memory_type=memory_type,
            domain=domain,
            importance=importance,
            is_core=is_core,
            metadata=deepcopy(metadata or {}),
            source=source,
        )

        if self.db_pool:
            await self._store_memory_db(memory)
        else:
            await self._store_memory_file(memory)

        return memory.id

    async def _store_memory_db(self, memory: Memory):
        """Store memory in database."""
        try:
            # Generate embedding if we have a client
            embedding = None
            if self._embeddings_client and memory.content:
                embedding = await self._get_embedding(memory.content)

            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO memories (id, memory_type, content, summary, domain,
                        importance, embedding, source, is_core, metadata, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    """,
                    memory.id,
                    memory.memory_type,
                    memory.content,
                    memory.summary,
                    memory.domain,
                    memory.importance,
                    embedding,
                    memory.source,
                    memory.is_core,
                    json.dumps(memory.metadata),
                    memory.created_at,
                )
        except Exception:
            logger.exception("Failed to store memory in DB")
            raise

    async def _store_memory_file(self, memory: Memory):
        """Commit a complete memory record before making it recallable."""
        record = asdict(memory)
        record["created_at"] = memory.created_at.isoformat()
        async with self._file_memory_lock:
            await self._commit_file_memory_change(
                lambda: self._write_json_atomic(self.memory_path / "entries" / f"{memory.id}.json", record),
                lambda: self._file_cache.setdefault("entries", []).append(deepcopy(memory)),
            )

    async def _commit_file_memory_change(
        self, write: Callable[[], None], publish: Callable[[], None],
    ) -> None:
        """Yield during disk I/O, settling disk and cache before cancellation exits."""
        async def commit() -> None:
            await asyncio.to_thread(write)
            publish()

        pending = asyncio.create_task(commit())
        cancelled = False
        while True:
            try:
                await asyncio.shield(pending)
                break
            except asyncio.CancelledError:
                if pending.cancelled():
                    raise
                # A running filesystem operation cannot be cancelled safely.
                # Keep the mutation lock until it and cache publication finish.
                cancelled = True
            except Exception:
                if cancelled:
                    # Preserve the deadline even if the disk later reports an
                    # error, so the tool loop cannot retry a cancelled write.
                    raise asyncio.CancelledError from None
                raise
        if cancelled:
            raise asyncio.CancelledError

    async def list_memories(
        self, limit: Optional[int] = 20, domain: str = None, *, session_id: str = None,
    ) -> list[Memory]:
        """List independent memory records, newest first; None includes all records."""
        if limit is not None:
            limit = max(0, limit)
        if self.db_pool:
            async with self.db_pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, content, summary, memory_type, domain, importance,
                           source, is_core, metadata, created_at
                    FROM memories
                    WHERE ($1::text IS NULL OR domain = $1)
                    AND ($3::text IS NULL OR COALESCE(metadata->>'session_id', '') IN ('', $3))
                    ORDER BY created_at DESC, id
                    LIMIT $2
                    """, domain, limit, session_id,
                )
            return [Memory(
                id=str(row["id"]), content=row["content"], summary=row["summary"],
                memory_type=row["memory_type"], domain=row["domain"], importance=row["importance"],
                source=row["source"] or "conversation", is_core=row["is_core"],
                metadata=json.loads(row["metadata"] or "{}"), created_at=row["created_at"],
            ) for row in rows]
        memories = [memory for entries in self._file_cache.values() for memory in entries
                    if (domain is None or memory.domain == domain)
                    and (session_id is None or not memory.metadata.get("session_id")
                         or memory.metadata["session_id"] == session_id)]
        memories.sort(key=lambda memory: memory.created_at, reverse=True)
        return deepcopy(memories[:limit])

    async def forget(self, memory_id: str, *, session_id: str = None) -> bool:
        """Delete a stored memory durably before removing its cached record."""
        if self.db_pool:
            try:
                identifier = uuid.UUID(memory_id)
            except ValueError:
                return False
            async with self.db_pool.acquire() as conn:
                deleted = await conn.fetchval(
                    """DELETE FROM memories WHERE id = $1
                    AND ($2::text IS NULL OR COALESCE(metadata->>'session_id', '') IN ('', $2))
                    RETURNING id""", identifier, session_id,
                )
            return deleted is not None
        async with self._file_memory_lock:
            entries = self._file_cache.get("entries", [])
            if not any(memory.id == memory_id and (
                session_id is None or not memory.metadata.get("session_id")
                or memory.metadata["session_id"] == session_id
            ) for memory in entries):
                return False

            def publish() -> None:
                self._file_cache["entries"] = [memory for memory in entries if memory.id != memory_id]

            await self._commit_file_memory_change(
                lambda: (self.memory_path / "entries" / f"{memory_id}.json").unlink(), publish,
            )
        return True

    async def memory_stats(self, *, session_id: str = None) -> dict:
        """Count records in the same store used by remember and recall."""
        memories = await self.list_memories(limit=None, session_id=session_id)
        return {
            "total_memories": len(memories),
            "by_source": dict(Counter(memory.source for memory in memories)),
            "by_domain": dict(Counter(memory.domain for memory in memories if memory.domain)),
        }

    async def import_legacy_vectors(self, path: Path) -> dict:
        """Copy a legacy source once per destination, retaining completion after forget."""
        source = Path(path).expanduser().resolve(strict=True)
        digest = hashlib.sha256(str(source).encode("utf-8")).hexdigest()
        if self.db_pool:
            return await self._import_legacy_vectors_db(source, digest)
        async with self._file_import_lock:
            return await self._import_legacy_vectors_file(source, digest)

    async def _import_legacy_vectors_file(self, source: Path, digest: str) -> dict:
        """Check completion and copy under the manager's whole-import lock."""
        marker = self.memory_path / "imports" / f"legacy-vectors-files-{digest}.json"
        if marker.exists():
            return {"imported": 0, "already_imported": True}
        rows = await asyncio.to_thread(self._read_legacy_vectors, source)
        existing = {memory.id: memory for memory in await self.list_memories(limit=None)}
        imported = 0
        for row in rows:
            memory = self._legacy_vector_memory(source, row)
            if memory.id in existing:
                if existing[memory.id].metadata.get("legacy_vector_import") != memory.metadata["legacy_vector_import"]:
                    raise ValueError(f"Legacy memory ID collision: {memory.id}")
                continue
            await self._store_memory_file(memory)
            existing[memory.id] = memory
            imported += 1
        self._write_json_atomic(marker, {
            "source": str(source), "destination": "files", "completed_at": datetime.now().isoformat(),
        })
        return {"imported": imported, "already_imported": False}

    async def _import_legacy_vectors_db(self, source: Path, digest: str) -> dict:
        """Commit copied rows and target-owned completion on the same connection."""
        from asyncpg import InsufficientPrivilegeError

        async with self.db_pool.acquire() as conn:
            async with conn.transaction():
                schema = await conn.fetchval(
                    "SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE c.oid = 'memories'::regclass"
                )
                quoted_schema = '"' + schema.replace('"', '""') + '"'
                memories_table = f"{quoted_schema}.memories"
                marker_table = f"{quoted_schema}.hyperclaw_memory_imports"
                lock = int.from_bytes(hashlib.sha256(
                    f"hyperclaw-memory-imports:{schema}".encode("utf-8")
                ).digest()[:8], "big", signed=True)
                # Serialize explicit imports, including first-time marker-table creation.
                await conn.execute("SELECT pg_advisory_xact_lock($1)", lock)
                try:
                    await conn.execute(f"""
                        CREATE TABLE IF NOT EXISTS {marker_table} (
                            source_digest TEXT PRIMARY KEY,
                            completed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                except InsufficientPrivilegeError as exc:
                    raise PermissionError(
                        "Legacy database import requires permission to create "
                        "hyperclaw_memory_imports in the memories schema"
                    ) from exc
                if await conn.fetchval(f"SELECT 1 FROM {marker_table} WHERE source_digest = $1", digest):
                    return {"imported": 0, "already_imported": True}
                rows = await asyncio.to_thread(self._read_legacy_vectors, source)
                imported = 0
                for row in rows:
                    memory = self._legacy_vector_memory(source, row)
                    existing = await conn.fetchrow(
                        f"SELECT metadata FROM {memories_table} WHERE id = $1", memory.id,
                    )
                    if existing is not None:
                        metadata = json.loads(existing["metadata"] or "{}")
                        if metadata.get("legacy_vector_import") != memory.metadata["legacy_vector_import"]:
                            raise ValueError(f"Legacy memory ID collision: {memory.id}")
                        continue
                    # Legacy vectors lack reliable model provenance. Follow the
                    # destination's current embedding policy instead of copying them.
                    embedding = await self._get_embedding(memory.content) if self._embeddings_client else None
                    await conn.execute(f"""
                        INSERT INTO {memories_table} (id, memory_type, content, summary, domain,
                            importance, embedding, source, is_core, metadata, created_at)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    """, memory.id, memory.memory_type, memory.content, memory.summary,
                        memory.domain, memory.importance, embedding, memory.source,
                        memory.is_core, json.dumps(memory.metadata), memory.created_at,
                    )
                    imported += 1
                await conn.execute(f"INSERT INTO {marker_table} (source_digest) VALUES ($1)", digest)
                return {"imported": imported, "already_imported": False}

    @staticmethod
    def _legacy_vector_memory(source: Path, row: dict) -> Memory:
        provenance = {"source": str(source), "id": row["id"]}
        identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(provenance, sort_keys=True)))
        metadata = json.loads(row["metadata"] or "{}")
        if "legacy_vector_import" in metadata:
            raise ValueError("Legacy metadata already uses the reserved legacy_vector_import key")
        return Memory(
            id=identifier, content=row["content"], memory_type="semantic",
            domain=row["domain"], source=row["source"] or "conversation",
            metadata={**metadata, "legacy_vector_import": provenance},
            created_at=datetime.fromtimestamp(row["created_at"]),
        )

    @staticmethod
    def _read_legacy_vectors(path: Path) -> list[dict]:
        """Read an explicitly selected source without creating or modifying SQLite files."""
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(
                "SELECT id, content, source, domain, metadata, created_at FROM embeddings ORDER BY created_at, id"
            )]
        finally:
            connection.close()

    async def recall(
        self,
        query: str,
        limit: int = 5,
        memory_type: str = None,
        domain: str = None,
        min_importance: float = 0.0,
        *, session_id: str = None,
    ) -> list[Memory]:
        """Recall relevant memories."""
        if self.db_pool:
            return await self._recall_db(query, limit, memory_type, domain, min_importance, session_id)
        else:
            return await self._recall_file(query, limit, memory_type, domain, min_importance, session_id)

    async def _recall_db(
        self,
        query: str,
        limit: int,
        memory_type: str,
        domain: str,
        min_importance: float,
        session_id: str = None,
    ) -> list[Memory]:
        """Recall from database with semantic search."""
        try:
            # Get embedding for query
            query_embedding = None
            if self._embeddings_client:
                query_embedding = await self._get_embedding(query)

            async with self.db_pool.acquire() as conn:
                if query_embedding:
                    # Semantic search
                    rows = await conn.fetch(
                        """
                        SELECT id, content, summary, memory_type, domain, importance,
                               source, is_core, metadata, created_at,
                               1 - (embedding <=> $1::vector) as similarity
                        FROM memories
                        WHERE embedding IS NOT NULL
                        AND importance >= $2
                        AND ($3::text IS NULL OR memory_type = $3)
                        AND ($4::text IS NULL OR domain = $4)
                        AND ($6::text IS NULL OR COALESCE(metadata->>'session_id', '') IN ('', $6))
                        ORDER BY embedding <=> $1::vector
                        LIMIT $5
                        """,
                        query_embedding,
                        min_importance,
                        memory_type,
                        domain,
                        limit,
                        session_id,
                    )
                else:
                    # Fallback to text search
                    rows = await conn.fetch(
                        """
                        SELECT id, content, summary, memory_type, domain, importance,
                               source, is_core, metadata, created_at, 0.5 as similarity
                        FROM memories
                        WHERE content ILIKE $1
                        AND importance >= $2
                        AND ($3::text IS NULL OR memory_type = $3)
                        AND ($4::text IS NULL OR domain = $4)
                        AND ($6::text IS NULL OR COALESCE(metadata->>'session_id', '') IN ('', $6))
                        ORDER BY importance DESC, created_at DESC
                        LIMIT $5
                        """,
                        f"%{query}%",
                        min_importance,
                        memory_type,
                        domain,
                        limit,
                        session_id,
                    )

                return [
                    Memory(
                        id=str(r["id"]),
                        content=r["content"],
                        memory_type=r["memory_type"],
                        domain=r["domain"],
                        importance=r["importance"],
                        summary=r["summary"],
                        source=r["source"] or "conversation",
                        is_core=r["is_core"],
                        metadata=json.loads(r["metadata"] or "{}"),
                        created_at=r["created_at"],
                    )
                    for r in rows
                ]
        except Exception:
            logger.exception("Failed to recall from DB")
            raise

    async def _recall_file(
        self,
        query: str,
        limit: int,
        memory_type: str,
        domain: str,
        min_importance: float = 0.0,
        session_id: str = None,
    ) -> list[Memory]:
        """Recall from file cache with keyword matching."""
        results = []
        query_lower = query.lower()
        query_words = set(query_lower.split())

        for memories in self._file_cache.values():
            for memory in memories:
                if memory_type and memory.memory_type != memory_type:
                    continue
                if domain and memory.domain != domain:
                    continue
                if memory.importance < min_importance:
                    continue
                if session_id is not None and memory.metadata.get("session_id") not in (None, "", session_id):
                    continue

                # Simple relevance scoring
                content_lower = memory.content.lower()
                matches = sum(1 for word in query_words if word in content_lower)

                if matches > 0:
                    results.append((matches / len(query_words), memory))

        # Sort by score and return top results
        results.sort(key=lambda result: result[0], reverse=True)
        return [deepcopy(memory) for _, memory in results[:max(0, limit)]]

    async def _get_embedding(self, text: str) -> list[float]:
        """Get embedding for text."""
        # This would use OpenAI or Anthropic embeddings API
        # For now, return None to use text-based search
        return None

    # =========================================================================
    # CONTEXT LOADING
    # =========================================================================

    def get_system_context(self) -> str:
        """Get full system context from workspace files."""
        context_parts = []

        # Load workspace files
        files_to_load = [
            ("CLAUDE.md", self.workspace_path.parent / "CLAUDE.md"),
            ("persona.md", Path(os.environ.get("PERSONA_FILE") or self.workspace_path / "persona.md")),
            ("SOUL.md", self.workspace_path / "SOUL.md"),
            ("IDENTITY.md", self.workspace_path / "IDENTITY.md"),
            ("ASSISTANT.md", self.workspace_path / "ASSISTANT.md"),
            ("USER.md", self.workspace_path / "USER.md"),
            ("MEMORY.md", self.workspace_path / "MEMORY.md"),
        ]

        for name, path in files_to_load:
            if path.exists():
                try:
                    content = path.read_text(encoding="utf-8")
                    context_parts.append(f"## {name}\n{content}\n")
                except Exception as e:
                    logger.warning(f"Failed to load {name}: {e}")

        # Add instincts
        instincts = self._file_cache.get("instincts", [])
        if instincts:
            context_parts.append("## Behavioral Instincts")
            for inst in instincts[:20]:
                context_parts.append(f"- {inst.content}")
            context_parts.append("")

        # Add recent core episodes
        episodes = self._file_cache.get("episodes", []) + [
            memory for memory in self._file_cache.get("entries", []) if memory.is_core
        ]
        if episodes:
            context_parts.append("## Key Facts (Core Memory)")
            for ep in episodes[-10:]:
                context_parts.append(f"- {ep.content}")
            context_parts.append("")

        # Add current date
        context_parts.append(f"\nCurrent date/time: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

        return "\n".join(context_parts)

    def get_working_memory(self) -> str:
        """Get current working memory content."""
        memory_file = self.workspace_path / "MEMORY.md"
        if memory_file.exists():
            return memory_file.read_text(encoding="utf-8")
        return ""

    async def update_working_memory(self, section: str, content: str):
        """Update a section in working memory."""
        memory_file = self.workspace_path / "MEMORY.md"

        try:
            if memory_file.exists():
                current = memory_file.read_text(encoding="utf-8")
            else:
                current = "# MEMORY.md - Working Memory\n"

            # Update or append section
            section_pattern = rf"(## {re.escape(section)}.*?)(?=\n## |\Z)"
            new_section = f"## {section}\n{content}\n"

            if re.search(section_pattern, current, re.DOTALL):
                updated = re.sub(section_pattern, new_section, current, flags=re.DOTALL)
            else:
                updated = current.rstrip() + f"\n\n{new_section}"

            # Add timestamp
            updated = re.sub(
                r"\*Last updated:.*\*",
                f"*Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
                updated
            )

            memory_file.write_text(updated, encoding="utf-8")

        except Exception as e:
            logger.error(f"Failed to update working memory: {e}")

    # =========================================================================
    # DAILY LOG
    # =========================================================================

    async def log_to_daily(self, entry: str, category: str = "note"):
        """Add entry to today's daily log."""
        today = datetime.now().strftime("%Y-%m-%d")
        daily_file = self.memory_path / "daily" / f"{today}.md"

        try:
            if not daily_file.exists():
                daily_file.parent.mkdir(exist_ok=True)
                daily_file.write_text(f"# Daily Log - {today}\n\n")

            timestamp = datetime.now().strftime("%H:%M")
            with open(daily_file, "a", encoding="utf-8") as f:
                f.write(f"- [{timestamp}] [{category}] {entry}\n")

        except Exception as e:
            logger.error(f"Failed to write daily log: {e}")


# ============================================================================
# SINGLETON
# ============================================================================

_memory_manager: Optional[MemoryManager] = None


async def get_memory_manager(db_pool=None) -> MemoryManager:
    """Get or create memory manager singleton."""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager(db_pool)
        await _memory_manager.initialize()
    return _memory_manager


def get_memory_manager_sync() -> MemoryManager:
    """Synchronous getter - assumes already initialized."""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager()
        asyncio.run(_memory_manager.initialize())
    return _memory_manager
