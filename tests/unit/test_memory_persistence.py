"""Durable memory contracts, exercised only against temporary local storage."""

import asyncio
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from hyperclaw import memory_manager as memory_module
from tests.unit.test_state_persistence import temporary_postgres  # noqa: F401


@pytest.fixture
def storage_root(tmp_path, monkeypatch):
    root = tmp_path / "hyperclaw"
    monkeypatch.setenv("HYPERCLAW_ROOT", str(root))
    monkeypatch.setattr(memory_module, "HYPERCLAW_ROOT", root)
    monkeypatch.setattr(memory_module, "MEMORY_PATH", root / "memory")
    monkeypatch.setattr(memory_module, "WORKSPACE_PATH", root / "workspace")
    return root


async def initialized_manager():
    manager = memory_module.MemoryManager()
    await manager.initialize()
    return manager


def conversation_files(root):
    return [path for path in (root / "memory" / "conversations").rglob("*") if path.is_file()]


def test_remember_is_immediately_recallable(storage_root):
    async def run():
        manager = await initialized_manager()
        memory_id = await manager.remember("The observatory door is blue")
        results = await manager.recall("observatory")
        assert [memory.id for memory in results] == [memory_id]
        assert results[0].content == "The observatory door is blue"

    asyncio.run(run())


def test_remember_preserves_fields_after_restart(storage_root):
    async def run():
        manager = await initialized_manager()
        metadata = {"tags": ["synthetic"], "detail": {"floor": 2}}
        memory_id = await manager.remember(
            "The observatory door is blue",
            memory_type="semantic",
            domain="astronomy",
            importance=0.9,
            is_core=True,
            metadata=metadata,
            source="user_explicit",
        )
        metadata["tags"].append("caller mutation")
        immediate = (await manager.recall("observatory"))[0]
        fresh = await initialized_manager()
        results = await fresh.recall("observatory")
        assert len(results) == 1
        memory = results[0]
        assert memory.id == memory_id
        assert memory.content == "The observatory door is blue"
        assert memory.memory_type == "semantic"
        assert memory.domain == "astronomy"
        assert memory.importance == 0.9
        assert memory.source == "user_explicit"
        assert memory.is_core is True
        assert memory.metadata == {"tags": ["synthetic"], "detail": {"floor": 2}}
        assert memory.created_at == immediate.created_at

    asyncio.run(run())


def test_recall_filters_and_returns_independent_memories(storage_root):
    async def run():
        manager = await initialized_manager()
        memory_id = await manager.remember(
            "observatory map", "semantic", "astronomy", 0.9,
            metadata={"tags": ["original"]},
        )
        await manager.remember("observatory stairs", "episode", "astronomy", 0.2)
        await manager.remember("observatory budget", "semantic", "finance", 0.9)
        results = await manager.recall(
            "observatory", memory_type="semantic", domain="astronomy", min_importance=0.8
        )
        assert [memory.id for memory in results] == [memory_id]
        results[0].content = "mutated"
        results[0].metadata["tags"].append("mutated")
        second = (await manager.recall("observatory map", limit=1))[0]
        assert second.content == "observatory map"
        assert second.metadata == {"tags": ["original"]}
        assert await manager.recall("observatory stairs", min_importance=1.0) == []

    asyncio.run(run())


def test_remember_storage_failure_is_not_acknowledged(storage_root):
    async def run():
        manager = await initialized_manager()
        (storage_root / "memory" / "entries").write_text("not a directory")
        with pytest.raises(OSError):
            await manager.remember("undurable observatory")
        assert await manager.recall("undurable") == []

    asyncio.run(run())


def test_session_history_survives_restart_without_aliasing(storage_root):
    async def run():
        manager = await initialized_manager()
        metadata = {"tool": {"name": "synthetic"}}
        manager.add_message("first", "user", "hello", metadata)
        metadata["tool"]["name"] = "caller mutation"
        manager.add_message("first", "assistant", "welcome")
        manager.add_message("second", "user", "another session")
        await manager.save_conversation("first")
        await manager.save_conversation("second")
        fresh = await initialized_manager()
        history = await fresh.load_conversation("first")
        assert [message["content"] for message in history] == ["hello", "welcome"]
        assert history[0]["tool"] == {"name": "synthetic"}
        history[0]["tool"]["name"] = "loaded mutation"
        history.append({"role": "user", "content": "external append"})
        snapshot = fresh.get_conversation_history("first")
        assert len(snapshot) == 2
        assert snapshot[0]["tool"] == {"name": "synthetic"}
        snapshot[0]["content"] = "snapshot mutation"
        snapshot.append({"role": "user", "content": "duplicate"})
        fresh.add_message("first", "user", "next")
        await fresh.save_conversation("first")
        restarted = await initialized_manager()
        assert [m["content"] for m in await restarted.load_conversation("first")] == [
            "hello", "welcome", "next"
        ]
        assert [m["content"] for m in await restarted.load_conversation("second")] == [
            "another session"
        ]

    asyncio.run(run())


@pytest.mark.parametrize(
    "session_id", ["../escape", "group/topic", "λ" * 300, ""],
    ids=["parent-path", "nested-path", "long-unicode", "empty"],
)
def test_arbitrary_session_ids_stay_inside_conversation_directory(storage_root, session_id):
    async def run():
        manager = await initialized_manager()
        manager.add_message(session_id, "user", "contained")
        await manager.save_conversation(session_id)
        files = conversation_files(storage_root)
        assert len(files) == 1
        assert files[0].is_file()
        assert len(files[0].stem) == 64
        fresh = await initialized_manager()
        assert [m["content"] for m in await fresh.load_conversation(session_id)] == ["contained"]
        assert not (storage_root / "memory" / "escape.json").exists()

    asyncio.run(run())


def test_unsafe_legacy_session_path_is_not_loaded(storage_root):
    async def run():
        manager = await initialized_manager()
        (storage_root / "memory" / "conversations").mkdir()
        outside = storage_root / "memory" / "outside.json"
        outside.write_text(json.dumps([{"role": "user", "content": "outside"}]))
        assert await manager.load_conversation("../outside") == []
        assert await manager.load_conversation(str(outside.with_suffix(""))) == []

    asyncio.run(run())


def test_legacy_session_symlink_is_not_loaded(storage_root):
    async def run():
        manager = await initialized_manager()
        directory = storage_root / "memory" / "conversations"
        directory.mkdir()
        outside = storage_root / "private.json"
        outside.write_text(json.dumps([{"role": "user", "content": "private"}]))
        (directory / "legacy.json").symlink_to(outside)
        assert await manager.load_conversation("legacy") == []
        await manager.clear_conversation("legacy")
        assert "private" in outside.read_text()

    asyncio.run(run())


def test_clear_legacy_conversation_survives_restart(storage_root):
    async def run():
        manager = await initialized_manager()
        directory = storage_root / "memory" / "conversations"
        directory.mkdir()
        history = [{"role": "user", "content": "legacy", "timestamp": "2026-09-08T01:02:03"}]
        (directory / "legacy-session.json").write_text(json.dumps(history))
        assert await manager.load_conversation("legacy-session") == history
        assert manager.get_conversation_history("legacy-session") == history
        await manager.clear_conversation("legacy-session")
        assert manager.get_conversation_history("legacy-session") == []
        fresh = await initialized_manager()
        assert await fresh.load_conversation("legacy-session") == []
        fresh.add_message("legacy-session", "user", "new start")
        await fresh.save_conversation("legacy-session")
        restarted = await initialized_manager()
        assert [m["content"] for m in await restarted.load_conversation("legacy-session")] == [
            "new start"
        ]

    asyncio.run(run())


def test_failed_atomic_save_preserves_previous_conversation(storage_root, monkeypatch):
    async def run():
        manager = await initialized_manager()
        manager.add_message("session", "user", "committed")
        await manager.save_conversation("session")
        manager.add_message("session", "assistant", "uncommitted")

        def fail_replace(source, destination):
            raise OSError("synthetic disk failure")

        with monkeypatch.context() as patch:
            patch.setattr(memory_module.os, "replace", fail_replace)
            with pytest.raises(OSError, match="synthetic disk failure"):
                await manager.save_conversation("session")
        fresh = await initialized_manager()
        assert [m["content"] for m in await fresh.load_conversation("session")] == ["committed"]
        assert len(conversation_files(storage_root)) == 1

    asyncio.run(run())


def test_failed_clear_retains_cached_and_stored_history(storage_root, monkeypatch):
    async def run():
        manager = await initialized_manager()
        manager.add_message("session", "user", "committed")
        await manager.save_conversation("session")

        def fail_replace(source, destination):
            raise OSError("synthetic disk failure")

        with monkeypatch.context() as patch:
            patch.setattr(memory_module.os, "replace", fail_replace)
            with pytest.raises(OSError, match="synthetic disk failure"):
                await manager.clear_conversation("session")
        assert [m["content"] for m in manager.get_conversation_history("session")] == ["committed"]
        fresh = await initialized_manager()
        assert [m["content"] for m in await fresh.load_conversation("session")] == ["committed"]

    asyncio.run(run())


def test_invalid_stored_conversation_raises(storage_root):
    async def run():
        manager = await initialized_manager()
        manager.add_message("session", "user", "committed")
        await manager.save_conversation("session")
        path = conversation_files(storage_root)[0]
        path.write_text("invalid JSON")
        fresh = await initialized_manager()
        with pytest.raises(json.JSONDecodeError):
            await fresh.load_conversation("session")

    asyncio.run(run())


@pytest.mark.parametrize("operation", ["remember", "recall", "save", "load", "clear"])
def test_database_storage_failure_propagates(storage_root, operation):
    class UnavailablePool:
        def acquire(self):
            raise ConnectionError("synthetic unavailable database")

    async def run():
        manager = memory_module.MemoryManager(db_pool=UnavailablePool())
        await manager.initialize()
        manager.add_message("session", "user", "unsaved")
        with pytest.raises(ConnectionError, match="synthetic unavailable database"):
            if operation == "remember":
                await manager.remember("unsaved memory")
            elif operation == "recall":
                await manager.recall("saved memory")
            elif operation == "save":
                await manager.save_conversation("session")
            elif operation == "load":
                await manager.load_conversation("session")
            else:
                await manager.clear_conversation("session")

    asyncio.run(run())


def test_explicit_root_keeps_legacy_markdown_context(storage_root):
    async def run():
        root = storage_root / "explicit"
        (root / "memory").mkdir(parents=True)
        (root / "workspace").mkdir()
        (root / "memory" / "instincts.md").write_text("- Ask for evidence\n")
        (root / "memory" / "core-episodes.md").write_text("- The observatory is nearby\n")
        (root / "workspace" / "MEMORY.md").write_text("- The observatory opens tomorrow\n")
        (root / "workspace" / "SOUL.md").write_text("Be precise")
        manager = memory_module.MemoryManager(root=root)
        await manager.initialize()
        assert len(await manager.recall("observatory")) == 2
        context = manager.get_system_context()
        assert "Ask for evidence" in context
        assert "The observatory is nearby" in context
        assert "Be precise" in context
        assert manager.get_working_memory() == "- The observatory opens tomorrow\n"

    asyncio.run(run())


def test_a_session_cannot_load_another_sessions_digest(storage_root):
    async def run():
        manager = await initialized_manager()
        manager.add_message("first", "user", "private to first")
        await manager.save_conversation("first")
        digest_session = hashlib.sha256(b"first").hexdigest()
        assert await manager.load_conversation(digest_session) == []

    asyncio.run(run())


def test_legacy_digest_filename_cannot_impersonate_new_session(storage_root):
    async def run():
        manager = await initialized_manager()
        directory = storage_root / "memory" / "conversations"
        directory.mkdir()
        digest_session = hashlib.sha256(b"first").hexdigest()
        history = [{"role": "user", "content": "legacy digest session"}]
        (directory / f"{digest_session}.json").write_text(json.dumps(history))
        assert await manager.load_conversation("first") == []
        assert await manager.load_conversation(digest_session) == history

    asyncio.run(run())


def test_new_core_memory_appears_in_system_context(storage_root):
    async def run():
        manager = await initialized_manager()
        await manager.remember("The observatory opens tomorrow", is_core=True)
        assert "The observatory opens tomorrow" in manager.get_system_context()
        fresh = await initialized_manager()
        assert "The observatory opens tomorrow" in fresh.get_system_context()

    asyncio.run(run())


@pytest.fixture
def memory_database(temporary_postgres):
    """Install the shipped memory schema in a new disposable database."""
    database = "memory_test_" + uuid4().hex
    dsn = temporary_postgres.replace("/postgres?", f"/{database}?")

    async def create():
        admin = await asyncpg.connect(temporary_postgres)
        try:
            await admin.execute(f'CREATE DATABASE "{database}"')
        finally:
            await admin.close()
        connection = await asyncpg.connect(dsn)
        try:
            await connection.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
            schema = (Path(__file__).resolve().parents[2] / "schema" / "init.sql").read_text()
            memory_schema = schema.split("-- MEMORY TABLES", 1)[1].split("-- AUDIT & SECURITY", 1)[0]
            memory_schema = memory_schema[memory_schema.index("CREATE TABLE"):]
            # Plain PostgreSQL can validate the text recall path without pgvector.
            memory_schema = memory_schema.replace("vector(1536)", "DOUBLE PRECISION[]")
            memory_schema = "\n".join(
                line for line in memory_schema.splitlines() if "USING ivfflat" not in line
            )
            await connection.execute(memory_schema)
        finally:
            await connection.close()

    asyncio.run(create())
    return dsn


def test_postgres_remember_recall_preserves_metadata(storage_root, memory_database):
    async def run():
        async with asyncpg.create_pool(memory_database, min_size=1, max_size=2) as pool:
            manager = memory_module.MemoryManager(db_pool=pool)
            await manager.initialize()
            memory_id = await manager.remember(
                "The observatory is blue", memory_type="semantic", domain="astronomy",
                importance=0.8, source="user_explicit", is_core=True,
                metadata={"tags": ["synthetic"]},
            )
            fresh = memory_module.MemoryManager(db_pool=pool)
            await fresh.initialize()
            results = await fresh.recall("observatory", memory_type="semantic", domain="astronomy")
            assert len(results) == 1
            memory = results[0]
            assert memory.id == memory_id
            assert memory.source == "user_explicit"
            assert memory.is_core is True
            assert memory.metadata == {"tags": ["synthetic"]}

    asyncio.run(run())


def test_postgres_repeated_save_and_reset_are_durable(storage_root, memory_database):
    async def run():
        async with asyncpg.create_pool(memory_database, min_size=1, max_size=2) as pool:
            manager = memory_module.MemoryManager(db_pool=pool)
            await manager.initialize()
            manager.add_message("session", "user", "first", {"tool": {"name": "synthetic"}})
            await manager.save_conversation("session")
            await manager.save_conversation("session")
            manager.add_message("session", "assistant", "second")
            await manager.save_conversation("session")
            fresh = memory_module.MemoryManager(db_pool=pool)
            await fresh.initialize()
            history = await fresh.load_conversation("session")
            assert [m["content"] for m in history] == ["first", "second"]
            assert history[0]["tool"] == {"name": "synthetic"}
            await fresh.clear_conversation("session")
            restarted = memory_module.MemoryManager(db_pool=pool)
            await restarted.initialize()
            assert await restarted.load_conversation("session") == []

    asyncio.run(run())


def test_postgres_failed_save_keeps_last_committed_snapshot(storage_root, memory_database):
    async def run():
        async with asyncpg.create_pool(memory_database, min_size=1, max_size=2) as pool:
            manager = memory_module.MemoryManager(db_pool=pool)
            await manager.initialize()
            manager.add_message("session", "user", "committed")
            await manager.save_conversation("session")
            manager.add_message("session", "role-too-long-for-the-column", "uncommitted")
            with pytest.raises(asyncpg.StringDataRightTruncationError):
                await manager.save_conversation("session")
            fresh = memory_module.MemoryManager(db_pool=pool)
            await fresh.initialize()
            assert [m["content"] for m in await fresh.load_conversation("session")] == ["committed"]

    asyncio.run(run())


def test_postgres_resume_preserves_older_history_and_message_columns(storage_root, memory_database):
    async def run():
        async with asyncpg.create_pool(memory_database, min_size=1, max_size=2) as pool:
            async with pool.acquire() as connection:
                conversation = await connection.fetchval(
                    "INSERT INTO conversations (session_id, channel) VALUES ('session', 'api') RETURNING id"
                )
                await connection.execute(
                    """
                    INSERT INTO messages (conversation_id, role, content, tokens_in, created_at)
                    SELECT $1, 'user', 'older ' || n, 42, NOW() - ((110 - n) * INTERVAL '1 second')
                    FROM generate_series(1, 110) AS n
                    """, conversation,
                )
            manager = memory_module.MemoryManager(db_pool=pool)
            await manager.initialize()
            assert len(await manager.load_conversation("session")) == 100
            manager.add_message("session", "assistant", "new reply")
            await manager.save_conversation("session")
            await manager.save_conversation("session")
            async with pool.acquire() as connection:
                assert await connection.fetchval("SELECT count(*) FROM messages") == 111
                assert await connection.fetchval("SELECT count(*) FROM messages WHERE tokens_in = 42") == 110
            await manager.clear_conversation("session")
            async with pool.acquire() as connection:
                assert await connection.fetchval("SELECT count(*) FROM messages") == 0

    asyncio.run(run())


def test_conversation_exists_distinguishes_empty_saved_session(storage_root):
    async def run():
        manager = await initialized_manager()
        assert not await manager.conversation_exists("session")
        await manager.save_conversation("session")
        fresh = await initialized_manager()
        assert await fresh.conversation_exists("session")
        await fresh.clear_conversation("new session")
        assert await fresh.conversation_exists("new session")

    asyncio.run(run())


def test_postgres_conversation_exists_includes_reset_session(storage_root, memory_database):
    async def run():
        async with asyncpg.create_pool(memory_database, min_size=1, max_size=2) as pool:
            manager = memory_module.MemoryManager(db_pool=pool)
            await manager.initialize()
            assert not await manager.conversation_exists("session")
            await manager.clear_conversation("session")
            fresh = memory_module.MemoryManager(db_pool=pool)
            await fresh.initialize()
            assert await fresh.conversation_exists("session")
            assert await fresh.load_conversation("session") == []

    asyncio.run(run())


def test_content_blocks_are_independent_file_snapshots(storage_root):
    async def run():
        manager = await initialized_manager()
        blocks = [{"type": "text", "text": "synthetic attachment"}]
        manager.add_message("session", "user", blocks)
        blocks[0]["text"] = "caller mutation"
        await manager.save_conversation("session")
        fresh = await initialized_manager()
        history = await fresh.load_conversation("session")
        assert history[0]["content"] == [{"type": "text", "text": "synthetic attachment"}]

    asyncio.run(run())


def test_postgres_content_blocks_and_json_strings_roundtrip(storage_root, memory_database):
    async def run():
        async with asyncpg.create_pool(memory_database, min_size=1, max_size=2) as pool:
            manager = memory_module.MemoryManager(db_pool=pool)
            await manager.initialize()
            blocks = [{"type": "text", "text": "synthetic attachment"}]
            manager.add_message("session", "user", blocks, {"channel": "telegram"})
            manager.add_message("session", "assistant", '[{"type": "text"}]')
            await manager.save_conversation("session")
            fresh = memory_module.MemoryManager(db_pool=pool)
            await fresh.initialize()
            history = await fresh.load_conversation("session")
            assert history[0]["content"] == blocks
            assert history[0]["channel"] == "telegram"
            assert history[1]["content"] == '[{"type": "text"}]'

    asyncio.run(run())
