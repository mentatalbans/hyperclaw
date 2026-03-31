#!/usr/bin/env python3
"""
Assistant Vector Memory — Semantic search over memories using embeddings.
"""

import os
import json
import sqlite3
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
import httpx

# Paths
MEMORY_DIR = Path(__file__).parent
ENGRAM_DB = MEMORY_DIR / "engram.db"
VECTOR_DB = MEMORY_DIR / "vectors.db"

# Embedding config
EMBEDDING_MODEL = "text-embedding-3-small"  # OpenAI, 1536 dims
EMBEDDING_DIM = 1536


def get_openai_key():
    """Get OpenAI API key from environment or .env file."""
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        env_file = MEMORY_DIR.parent / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("OPENAI_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"')
    return key


def init_vector_db():
    """Initialize the vector database."""
    conn = sqlite3.connect(str(VECTOR_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS embeddings (
            id TEXT PRIMARY KEY,
            content_hash TEXT UNIQUE,
            content TEXT NOT NULL,
            embedding TEXT NOT NULL,
            source TEXT DEFAULT 'manual',
            domain TEXT,
            metadata TEXT DEFAULT '{}',
            created_at REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_source ON embeddings(source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_domain ON embeddings(domain)")
    conn.commit()
    conn.close()


def get_embedding(text: str) -> Optional[List[float]]:
    """Get embedding for text using OpenAI API."""
    api_key = get_openai_key()
    if not api_key:
        print("Warning: OPENAI_API_KEY not set, using hash-based pseudo-embedding")
        # Fallback: simple hash-based embedding (not semantic, but works for exact match)
        h = hashlib.sha256(text.encode()).hexdigest()
        # Convert to 1536-dim vector by repeating hash
        pseudo = [int(h[i:i+2], 16) / 255.0 for i in range(0, 64, 2)] * 48
        return pseudo[:EMBEDDING_DIM]

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"input": text[:8000], "model": EMBEDDING_MODEL}
            )
            if resp.status_code == 200:
                return resp.json()["data"][0]["embedding"]
            print(f"Embedding API error: {resp.status_code}")
            return None
    except Exception as e:
        print(f"Embedding error: {e}")
        return None


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Calculate cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def memory_store(
    content: str,
    source: str = "conversation",
    domain: str = None,
    metadata: Dict[str, Any] = None
) -> str:
    """Store a memory with its embedding."""
    init_vector_db()

    # Generate content hash for dedup
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:32]

    # Check if already stored
    conn = sqlite3.connect(str(VECTOR_DB))
    existing = conn.execute(
        "SELECT id FROM embeddings WHERE content_hash = ?",
        (content_hash,)
    ).fetchone()

    if existing:
        conn.close()
        return f"Memory already exists: {existing[0]}"

    # Get embedding
    embedding = get_embedding(content)
    if not embedding:
        conn.close()
        return "Failed to generate embedding"

    # Store
    import uuid
    memory_id = str(uuid.uuid4())[:8]
    now = datetime.now().timestamp()

    conn.execute(
        """INSERT INTO embeddings
           (id, content_hash, content, embedding, source, domain, metadata, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (memory_id, content_hash, content, json.dumps(embedding),
         source, domain, json.dumps(metadata or {}), now)
    )
    conn.commit()
    conn.close()

    return f"Stored memory {memory_id}"


def memory_search(
    query: str,
    limit: int = 5,
    domain: str = None,
    threshold: float = 0.3
) -> List[Dict[str, Any]]:
    """Search memories by semantic similarity."""
    init_vector_db()

    # Get query embedding
    query_embedding = get_embedding(query)
    if not query_embedding:
        return []

    conn = sqlite3.connect(str(VECTOR_DB))

    # Build query
    sql = "SELECT id, content, embedding, source, domain, metadata, created_at FROM embeddings"
    params = []
    if domain:
        sql += " WHERE domain = ?"
        params.append(domain)

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    # Calculate similarities
    results = []
    for row in rows:
        embedding = json.loads(row[2])
        similarity = cosine_similarity(query_embedding, embedding)
        if similarity >= threshold:
            results.append({
                "id": row[0],
                "content": row[1],
                "similarity": round(similarity, 3),
                "source": row[3],
                "domain": row[4],
                "metadata": json.loads(row[5]),
                "created_at": datetime.fromtimestamp(row[6]).isoformat()
            })

    # Sort by similarity
    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results[:limit]


def memory_forget(memory_id: str = None, query: str = None) -> str:
    """Delete a memory by ID or by searching."""
    init_vector_db()
    conn = sqlite3.connect(str(VECTOR_DB))

    if memory_id:
        conn.execute("DELETE FROM embeddings WHERE id = ?", (memory_id,))
        affected = conn.total_changes
        conn.commit()
        conn.close()
        return f"Deleted {affected} memory" if affected else "Memory not found"

    if query:
        # Find and delete matching memories
        results = memory_search(query, limit=1, threshold=0.7)
        if results:
            memory_id = results[0]["id"]
            conn.execute("DELETE FROM embeddings WHERE id = ?", (memory_id,))
            conn.commit()
            conn.close()
            return f"Deleted memory {memory_id}: {results[0]['content'][:50]}..."
        conn.close()
        return "No matching memory found"

    conn.close()
    return "Provide memory_id or query"


def memory_list(limit: int = 20, domain: str = None) -> List[Dict[str, Any]]:
    """List recent memories."""
    init_vector_db()
    conn = sqlite3.connect(str(VECTOR_DB))

    sql = "SELECT id, content, source, domain, created_at FROM embeddings"
    params = []
    if domain:
        sql += " WHERE domain = ?"
        params.append(domain)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    return [{
        "id": row[0],
        "content": row[1][:100] + "..." if len(row[1]) > 100 else row[1],
        "source": row[2],
        "domain": row[3],
        "created_at": datetime.fromtimestamp(row[4]).isoformat()
    } for row in rows]


def sync_engram_to_vectors():
    """Sync existing engram episodes to vector store."""
    init_vector_db()

    # Read episodes from engram.db
    engram_conn = sqlite3.connect(str(ENGRAM_DB))
    episodes = engram_conn.execute(
        "SELECT id, content, domain, source FROM episodes"
    ).fetchall()
    engram_conn.close()

    synced = 0
    for ep_id, content, domain, source in episodes:
        result = memory_store(content, source=source or "engram", domain=domain)
        if "Stored" in result:
            synced += 1
            print(f"Synced: {content[:50]}...")

    return f"Synced {synced} episodes to vector store"


def memory_stats() -> Dict[str, Any]:
    """Get memory statistics."""
    init_vector_db()
    conn = sqlite3.connect(str(VECTOR_DB))

    total = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    by_source = conn.execute(
        "SELECT source, COUNT(*) FROM embeddings GROUP BY source"
    ).fetchall()
    by_domain = conn.execute(
        "SELECT domain, COUNT(*) FROM embeddings GROUP BY domain"
    ).fetchall()

    conn.close()

    return {
        "total_memories": total,
        "by_source": dict(by_source),
        "by_domain": {k: v for k, v in by_domain if k}
    }


# CLI interface
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: vector_memory.py <command> [args]")
        print("Commands: store, search, forget, list, sync, stats")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "store":
        content = " ".join(sys.argv[2:])
        print(memory_store(content))

    elif cmd == "search":
        query = " ".join(sys.argv[2:])
        results = memory_search(query)
        for r in results:
            print(f"[{r['similarity']}] {r['id']}: {r['content'][:80]}...")

    elif cmd == "forget":
        if len(sys.argv) > 2:
            print(memory_forget(memory_id=sys.argv[2]))
        else:
            print("Provide memory_id")

    elif cmd == "list":
        for m in memory_list():
            print(f"{m['id']}: {m['content']}")

    elif cmd == "sync":
        print(sync_engram_to_vectors())

    elif cmd == "stats":
        print(json.dumps(memory_stats(), indent=2))

    else:
        print(f"Unknown command: {cmd}")
