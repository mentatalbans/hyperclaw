"""
HyperClaw Memory Manager
Handles memory persistence across sessions with database + file fallback.
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass, field

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

    def __init__(self, db_pool=None):
        self.db_pool = db_pool
        self._file_cache: dict[str, list[Memory]] = {}
        self._conversation_history: dict[str, list[dict]] = {}
        self._embeddings_client = None

    async def initialize(self):
        """Initialize the memory manager."""
        # Ensure directories exist
        MEMORY_PATH.mkdir(parents=True, exist_ok=True)
        (MEMORY_PATH / "daily").mkdir(exist_ok=True)

        # Load file-based memories
        await self._load_file_memories()

        logger.info("Memory manager initialized")

    async def _load_file_memories(self):
        """Load memories from markdown files."""
        # Load instincts
        instincts_file = MEMORY_PATH / "instincts.md"
        if instincts_file.exists():
            content = instincts_file.read_text(encoding="utf-8")
            self._file_cache["instincts"] = self._parse_markdown_list(content, "instinct")

        # Load core episodes
        episodes_file = MEMORY_PATH / "core-episodes.md"
        if episodes_file.exists():
            content = episodes_file.read_text(encoding="utf-8")
            self._file_cache["episodes"] = self._parse_markdown_list(content, "episode")

        # Load working memory
        memory_file = WORKSPACE_PATH / "MEMORY.md"
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
        """Get conversation history for a session."""
        history = self._conversation_history.get(session_id, [])
        return history[-limit:] if len(history) > limit else history

    def add_message(self, session_id: str, role: str, content: str, metadata: dict = None):
        """Add a message to conversation history."""
        if session_id not in self._conversation_history:
            self._conversation_history[session_id] = []

        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **(metadata or {})
        }

        self._conversation_history[session_id].append(message)

        # Trim to max size
        max_history = 100
        if len(self._conversation_history[session_id]) > max_history:
            self._conversation_history[session_id] = self._conversation_history[session_id][-max_history:]

    async def save_conversation(self, session_id: str):
        """Persist conversation to storage."""
        history = self._conversation_history.get(session_id, [])
        if not history:
            return

        if self.db_pool:
            await self._save_conversation_db(session_id, history)
        else:
            await self._save_conversation_file(session_id, history)

    async def _save_conversation_db(self, session_id: str, history: list[dict]):
        """Save conversation to database."""
        try:
            async with self.db_pool.acquire() as conn:
                # Get or create conversation
                conv = await conn.fetchrow(
                    """
                    INSERT INTO conversations (session_id, channel, last_message_at)
                    VALUES ($1, 'api', NOW())
                    ON CONFLICT (session_id) DO UPDATE SET last_message_at = NOW()
                    RETURNING id
                    """,
                    session_id
                )

                # Insert messages
                for msg in history[-10:]:  # Only save recent messages
                    await conn.execute(
                        """
                        INSERT INTO messages (conversation_id, role, content, created_at)
                        VALUES ($1, $2, $3, $4)
                        """,
                        conv["id"],
                        msg["role"],
                        msg["content"],
                        datetime.fromisoformat(msg["timestamp"])
                    )
        except Exception as e:
            logger.error(f"Failed to save conversation to DB: {e}")

    async def _save_conversation_file(self, session_id: str, history: list[dict]):
        """Save conversation to file."""
        try:
            filepath = MEMORY_PATH / "conversations" / f"{session_id}.json"
            filepath.parent.mkdir(exist_ok=True)
            with open(filepath, 'w') as f:
                json.dump(history, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save conversation to file: {e}")

    async def load_conversation(self, session_id: str) -> list[dict]:
        """Load conversation from storage."""
        if self.db_pool:
            return await self._load_conversation_db(session_id)
        else:
            return await self._load_conversation_file(session_id)

    async def _load_conversation_db(self, session_id: str) -> list[dict]:
        """Load conversation from database."""
        try:
            async with self.db_pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT m.role, m.content, m.created_at
                    FROM messages m
                    JOIN conversations c ON m.conversation_id = c.id
                    WHERE c.session_id = $1
                    ORDER BY m.created_at DESC
                    LIMIT 50
                    """,
                    session_id
                )
                history = [
                    {"role": r["role"], "content": r["content"], "timestamp": r["created_at"].isoformat()}
                    for r in reversed(rows)
                ]
                self._conversation_history[session_id] = history
                return history
        except Exception as e:
            logger.error(f"Failed to load conversation from DB: {e}")
            return []

    async def _load_conversation_file(self, session_id: str) -> list[dict]:
        """Load conversation from file."""
        try:
            filepath = MEMORY_PATH / "conversations" / f"{session_id}.json"
            if filepath.exists():
                with open(filepath) as f:
                    history = json.load(f)
                self._conversation_history[session_id] = history
                return history
        except Exception as e:
            logger.error(f"Failed to load conversation from file: {e}")
        return []

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
        metadata: dict = None
    ) -> str:
        """Store a new memory."""
        import uuid

        memory = Memory(
            id=str(uuid.uuid4()),
            content=content,
            memory_type=memory_type,
            domain=domain,
            importance=importance,
            is_core=is_core,
            metadata=metadata or {},
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
        except Exception as e:
            logger.error(f"Failed to store memory in DB: {e}")
            # Fallback to file
            await self._store_memory_file(memory)

    async def _store_memory_file(self, memory: Memory):
        """Store memory in file."""
        try:
            # Append to daily log
            today = datetime.now().strftime("%Y-%m-%d")
            daily_file = MEMORY_PATH / "daily" / f"{today}.md"

            with open(daily_file, "a", encoding="utf-8") as f:
                timestamp = datetime.now().strftime("%H:%M")
                f.write(f"\n- [{timestamp}] [{memory.memory_type}] {memory.content}\n")

            # If core, also append to core-episodes
            if memory.is_core:
                episodes_file = MEMORY_PATH / "core-episodes.md"
                with open(episodes_file, "a", encoding="utf-8") as f:
                    date = datetime.now().strftime("%Y-%m-%d")
                    f.write(f"\n- [{date}] {memory.content}\n")

        except Exception as e:
            logger.error(f"Failed to store memory to file: {e}")

    async def recall(
        self,
        query: str,
        limit: int = 5,
        memory_type: str = None,
        domain: str = None,
        min_importance: float = 0.0
    ) -> list[Memory]:
        """Recall relevant memories."""
        if self.db_pool:
            return await self._recall_db(query, limit, memory_type, domain, min_importance)
        else:
            return await self._recall_file(query, limit, memory_type, domain)

    async def _recall_db(
        self,
        query: str,
        limit: int,
        memory_type: str,
        domain: str,
        min_importance: float
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
                               1 - (embedding <=> $1::vector) as similarity
                        FROM memories
                        WHERE embedding IS NOT NULL
                        AND importance >= $2
                        AND ($3::text IS NULL OR memory_type = $3)
                        AND ($4::text IS NULL OR domain = $4)
                        ORDER BY embedding <=> $1::vector
                        LIMIT $5
                        """,
                        query_embedding,
                        min_importance,
                        memory_type,
                        domain,
                        limit
                    )
                else:
                    # Fallback to text search
                    rows = await conn.fetch(
                        """
                        SELECT id, content, summary, memory_type, domain, importance, 0.5 as similarity
                        FROM memories
                        WHERE content ILIKE $1
                        AND importance >= $2
                        AND ($3::text IS NULL OR memory_type = $3)
                        AND ($4::text IS NULL OR domain = $4)
                        ORDER BY importance DESC, created_at DESC
                        LIMIT $5
                        """,
                        f"%{query}%",
                        min_importance,
                        memory_type,
                        domain,
                        limit
                    )

                return [
                    Memory(
                        id=str(r["id"]),
                        content=r["content"],
                        memory_type=r["memory_type"],
                        domain=r["domain"],
                        importance=r["importance"],
                        summary=r["summary"],
                        metadata={"similarity": r["similarity"]}
                    )
                    for r in rows
                ]
        except Exception as e:
            logger.error(f"Failed to recall from DB: {e}")
            return await self._recall_file(query, limit, memory_type, domain)

    async def _recall_file(
        self,
        query: str,
        limit: int,
        memory_type: str,
        domain: str
    ) -> list[Memory]:
        """Recall from file cache with keyword matching."""
        results = []
        query_lower = query.lower()
        query_words = set(query_lower.split())

        for cache_type, memories in self._file_cache.items():
            for memory in memories:
                if memory_type and memory.memory_type != memory_type:
                    continue
                if domain and memory.domain != domain:
                    continue

                # Simple relevance scoring
                content_lower = memory.content.lower()
                matches = sum(1 for word in query_words if word in content_lower)

                if matches > 0:
                    memory.metadata["score"] = matches / len(query_words)
                    results.append(memory)

        # Sort by score and return top results
        results.sort(key=lambda m: m.metadata.get("score", 0), reverse=True)
        return results[:limit]

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
            ("SOUL.md", WORKSPACE_PATH / "SOUL.md"),
            ("IDENTITY.md", WORKSPACE_PATH / "IDENTITY.md"),
            ("USER.md", WORKSPACE_PATH / "USER.md"),
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
        episodes = self._file_cache.get("episodes", [])
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
        memory_file = WORKSPACE_PATH / "MEMORY.md"
        if memory_file.exists():
            return memory_file.read_text(encoding="utf-8")
        return ""

    async def update_working_memory(self, section: str, content: str):
        """Update a section in working memory."""
        memory_file = WORKSPACE_PATH / "MEMORY.md"

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
        daily_file = MEMORY_PATH / "daily" / f"{today}.md"

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
