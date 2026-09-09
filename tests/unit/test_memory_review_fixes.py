"""Memory review regressions against canonical runtime and temporary storage."""

import asyncio
import json
import sqlite3
import threading

import asyncpg
import httpx
import pytest
import pytest_asyncio

from hyperclaw.memory_manager import MemoryManager
from tests.unit.test_adapter_runtime import bind_runtime, make_bridge
from tests.unit.test_runtime import wire_message
from tests.unit.test_memory_persistence import memory_database  # noqa: F401
from tests.unit.test_state_persistence import temporary_postgres  # noqa: F401

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("cancel", [False, True], ids=["write-error", "cancelled"])
async def test_bridge_attachment_retry_keeps_all_cached_history(tmp_path, monkeypatch, cancel):
    # Restoring the default 50-message view loses the first 30 records and IDs.
    monkeypatch.setenv("HYPERCLAW_ROOT", str(tmp_path))
    app = await bind_runtime(
        monkeypatch, tmp_path, lambda request: httpx.Response(200, json=wire_message("reply")),
    )
    bridge = make_bridge(monkeypatch)
    monkeypatch.setattr("hyperclaw.tui_bridge._session_histories", {})
    memory = app._memory
    session_id = "bridge:891"
    for index in range(80):
        memory.add_message(session_id, "user" if index % 2 == 0 else "assistant", f"message {index}")
    await memory.save_conversation(session_id)
    previous = memory.get_conversation_history(session_id, limit=100)
    attachment = [{"type": "text", "text": "synthetic attachment"}]
    bridge.add_to_history(891, "user", attachment)
    entered = asyncio.Event()

    async def fail_save(session, history):
        if cancel:
            entered.set()
            await asyncio.Event().wait()
        raise OSError("synthetic storage failure")

    try:
        with monkeypatch.context() as failure:
            failure.setattr(memory, "_save_conversation_file", fail_save)
            if cancel:
                turn = asyncio.create_task(bridge.execute("inspect attachment", 891))
                await asyncio.wait_for(entered.wait(), timeout=5)
                turn.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await turn
            else:
                result = await bridge.execute("inspect attachment", 891)
                assert result["success"] is False
            assert memory.get_conversation_history(session_id, limit=100) == previous
            assert bridge.get_session_history(891)[-1]["content"] == attachment
            fresh = MemoryManager(root=tmp_path)
            await fresh.initialize()
            assert await fresh.load_conversation(session_id) == previous
        result = await bridge.execute("inspect attachment", 891)
        assert result["success"] is True
        fresh = MemoryManager(root=tmp_path)
        await fresh.initialize()
        durable = await fresh.load_conversation(session_id)
        assert durable[:80] == previous
        assert [message["content"] for message in durable[80:]] == [
            attachment, "inspect attachment", "reply",
        ]
    finally:
        await app.shutdown()


async def test_transactional_append_preserves_supplied_ids_and_detaches_content(tmp_path):
    manager = MemoryManager(root=tmp_path)
    await manager.initialize()
    message = {
        "id": "b5963671-2060-453e-ab9e-b2b878fe4b36", "role": "user",
        "content": [{"type": "text", "text": "attachment"}],
        "timestamp": "2026-09-08T01:02:03", "source": {"name": "synthetic"},
    }
    await manager.append_messages("session", [message])
    message["content"][0]["text"] = "caller mutation"
    message["source"]["name"] = "caller mutation"
    fresh = MemoryManager(root=tmp_path)
    await fresh.initialize()
    assert await fresh.load_conversation("session") == [{
        "id": "b5963671-2060-453e-ab9e-b2b878fe4b36", "role": "user",
        "content": [{"type": "text", "text": "attachment"}],
        "timestamp": "2026-09-08T01:02:03", "source": {"name": "synthetic"},
    }]


@pytest.fixture(params=["file", pytest.param("postgres", marks=pytest.mark.postgres)])
def memory_dsn(request):
    return request.getfixturevalue("memory_database") if request.param == "postgres" else None


@pytest_asyncio.fixture
async def memory(tmp_path, memory_dsn):
    pool = await asyncpg.create_pool(memory_dsn) if memory_dsn else None
    manager = MemoryManager(db_pool=pool, root=tmp_path)
    await manager.initialize()
    try:
        yield manager
    finally:
        if pool is not None:
            await pool.close()


def reject_legacy_executor(name, inputs):
    raise AssertionError(f"Canonical memory tool escaped to legacy executor: {name}")


async def test_memory_tools_share_runtime_storage_and_remain_durable(memory):
    # A legacy handler can report success while the canonical runtime recalls nothing.
    from hyperclaw.memory_tools import bind_memory_tools

    execute = bind_memory_tools(memory, reject_legacy_executor)
    stored = await execute("memory_store", {
        "content": "The observatory door is blue", "source": "observation", "domain": "astronomy",
    })
    memories = await memory.recall("observatory")
    assert len(memories) == 1
    memory_id = memories[0].id
    assert memory_id in stored
    await memory.remember("The library closes at noon", domain="local", source="research")
    listed = json.loads(await execute("memory_list", {"domain": "astronomy", "limit": 1}))
    assert [item["id"] for item in listed] == [memory_id]
    assert listed[0]["source"] == "observation"
    assert json.loads(await execute("memory_stats", {})) == {
        "total_memories": 2,
        "by_source": {"observation": 1, "research": 1},
        "by_domain": {"astronomy": 1, "local": 1},
    }
    fresh = MemoryManager(db_pool=memory.db_pool, root=memory.root)
    await fresh.initialize()
    restarted_execute = bind_memory_tools(fresh, reject_legacy_executor)
    searched = json.loads(await restarted_execute("memory_search", {
        "query": "observatory", "domain": "astronomy", "limit": 1,
    }))
    assert [item["id"] for item in searched] == [memory_id]
    assert searched[0]["content"] == "The observatory door is blue"
    assert await restarted_execute("memory_search", {"query": "observatory", "domain": "local"}) == "No memories found"
    assert await restarted_execute("memory_forget", {"memory_id": memory_id}) == "Deleted 1 memory"
    final = MemoryManager(db_pool=memory.db_pool, root=memory.root)
    await final.initialize()
    assert await final.recall("observatory") == []
    assert [item.content for item in await final.list_memories()] == ["The library closes at noon"]


async def test_memory_tools_forget_by_query_and_handle_empty_matches(memory):
    from hyperclaw.memory_tools import bind_memory_tools

    execute = bind_memory_tools(memory, reject_legacy_executor)
    await memory.remember("The observatory door is blue")
    await memory.remember("The library closes at noon")
    result = await execute("memory_forget", {"query": "observatory"})
    assert "Deleted" in result
    assert await memory.recall("observatory") == []
    assert len(await memory.recall("library")) == 1
    assert await execute("memory_forget", {"query": "missing"}) == "No matching memory found"
    assert await execute("memory_forget", {}) == "Provide memory_id or query"
    assert await execute("memory_forget", {"memory_id": "missing"}) == "Memory not found"
    assert await execute("memory_list", {"limit": 0}) == "No memories stored"


async def test_memory_list_is_recent_filtered_and_independent(memory):
    # Returning a mutable cache reference would let list callers rewrite stored facts.
    first = await memory.remember("first observatory fact", domain="astronomy")
    second = await memory.remember("second observatory fact", domain="astronomy", metadata={"tags": ["original"]})
    await memory.remember("library fact", domain="local")
    listed = await memory.list_memories(limit=2, domain="astronomy")
    assert [item.id for item in listed] == [second, first]
    listed[0].content = "caller mutation"
    listed[0].metadata["tags"].append("caller mutation")
    fresh = await memory.list_memories(limit=1, domain="astronomy")
    assert fresh[0].content == "second observatory fact"
    assert fresh[0].metadata == {"tags": ["original"]}


@pytest.mark.parametrize("is_async", [False, True])
async def test_memory_adapter_preserves_other_tools_and_offloads_sync_work(tmp_path, is_async):
    from hyperclaw.memory_tools import bind_memory_tools

    owner_thread = threading.get_ident()

    def sync_execute(name, inputs):
        assert threading.get_ident() != owner_thread
        return f"{name}: {inputs['value']}"

    async def async_execute(name, inputs):
        assert threading.get_ident() == owner_thread
        await asyncio.sleep(0)
        return f"{name}: {inputs['value']}"

    execute = bind_memory_tools(MemoryManager(root=tmp_path), async_execute if is_async else sync_execute)
    assert await execute("synthetic_other_tool", {"value": "payload"}) == "synthetic_other_tool: payload"


async def test_failed_memory_delete_remains_recallable(tmp_path, monkeypatch):
    from pathlib import Path

    memory = MemoryManager(root=tmp_path)
    await memory.initialize()
    memory_id = await memory.remember("The observatory door is blue")

    def fail_unlink(path, *args, **kwargs):
        raise OSError("synthetic deletion failure")

    with monkeypatch.context() as failure:
        failure.setattr(Path, "unlink", fail_unlink)
        with pytest.raises(OSError, match="synthetic deletion failure"):
            await memory.forget(memory_id)
    assert [item.id for item in await memory.recall("observatory")] == [memory_id]
    fresh = MemoryManager(root=tmp_path)
    await fresh.initialize()
    assert [item.id for item in await fresh.recall("observatory")] == [memory_id]


def legacy_vectors(path, records=None):
    """Build the shipped legacy schema without invoking its provider or global paths."""
    with sqlite3.connect(path) as connection:
        connection.execute("""
            CREATE TABLE embeddings (
                id TEXT PRIMARY KEY, content_hash TEXT UNIQUE, content TEXT NOT NULL,
                embedding TEXT NOT NULL, source TEXT DEFAULT 'manual', domain TEXT,
                metadata TEXT DEFAULT '{}', created_at REAL NOT NULL
            )
        """)
        connection.executemany(
            "INSERT INTO embeddings VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            records or [(
                "abcd1234", "synthetic-content-hash", "The observatory door is blue",
                "[0.1, 0.2]", "observation", "astronomy", '{"tags": ["original"]}', 1700000000.0,
            )],
        )


async def test_explicit_legacy_import_is_non_destructive_and_forget_survives_restart(memory, tmp_path):
    from hyperclaw.memory_tools import bind_memory_tools

    source = tmp_path / "legacy-vectors.db"
    legacy_vectors(source)
    original = source.read_bytes()
    assert await memory.recall("observatory") == []
    result = await memory.import_legacy_vectors(source)
    assert result == {"imported": 1, "already_imported": False}
    recalled = await memory.recall("observatory")
    assert len(recalled) == 1
    imported = recalled[0]
    assert imported.source == "observation"
    assert imported.domain == "astronomy"
    assert imported.metadata["tags"] == ["original"]
    assert imported.metadata["legacy_vector_import"]["id"] == "abcd1234"
    assert imported.metadata["legacy_vector_import"]["source"] == str(source.resolve())
    assert imported.created_at.timestamp() == 1700000000.0
    assert imported.embedding is None
    execute = bind_memory_tools(memory, reject_legacy_executor)
    assert [item["id"] for item in json.loads(await execute("memory_search", {"query": "observatory"}))] == [imported.id]
    assert await memory.import_legacy_vectors(source) == {"imported": 0, "already_imported": True}
    assert await execute("memory_forget", {"memory_id": imported.id}) == "Deleted 1 memory"
    fresh = MemoryManager(db_pool=memory.db_pool, root=memory.root)
    await fresh.initialize()
    assert await fresh.import_legacy_vectors(source) == {"imported": 0, "already_imported": True}
    assert await fresh.recall("observatory") == []
    assert source.read_bytes() == original


async def test_legacy_import_partial_failure_retries_without_duplicate_ids(tmp_path, monkeypatch):
    from hyperclaw import memory_manager as memory_module

    source = tmp_path / "legacy-vectors.db"
    legacy_vectors(source, [
        ("first", "hash-first", "first observatory fact", "[]", "manual", None, "{}", 1700000000.0),
        ("second", "hash-second", "second observatory fact", "[]", "manual", None, "{}", 1700000001.0),
    ])
    memory = MemoryManager(root=tmp_path / "canonical")
    await memory.initialize()
    replace = memory_module.os.replace
    writes = 0

    def fail_second_write(source_path, destination):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("synthetic import failure")
        return replace(source_path, destination)

    with monkeypatch.context() as failure:
        failure.setattr(memory_module.os, "replace", fail_second_write)
        with pytest.raises(OSError, match="synthetic import failure"):
            await memory.import_legacy_vectors(source)
    first = await memory.list_memories(limit=None)
    assert len(first) == 1
    fresh = MemoryManager(root=memory.root)
    await fresh.initialize()
    assert await fresh.import_legacy_vectors(source) == {"imported": 1, "already_imported": False}
    assert {item.content for item in await fresh.list_memories(limit=None)} == {
        "first observatory fact", "second observatory fact",
    }
    assert (await fresh.recall("first"))[0].id == first[0].id


async def test_legacy_import_rejects_canonical_id_collision_without_overwriting(memory, tmp_path, monkeypatch):
    from uuid import UUID
    from hyperclaw import memory_manager as memory_module

    source = tmp_path / "legacy-vectors.db"
    legacy_vectors(source)
    original_id = await memory.remember("canonical observatory fact")
    monkeypatch.setattr(memory_module.uuid, "uuid5", lambda namespace, name: UUID(original_id))
    with pytest.raises(ValueError, match="collision"):
        await memory.import_legacy_vectors(source)
    assert [item.content for item in await memory.list_memories(limit=None)] == ["canonical observatory fact"]


@pytest.mark.parametrize("database_first", [False, True], ids=["file-to-db", "db-to-file"])
async def test_legacy_import_completion_is_scoped_to_file_or_database(memory_database, tmp_path, database_first):
    source = tmp_path / "legacy-vectors.db"
    legacy_vectors(source)
    original = source.read_bytes()
    async with asyncpg.create_pool(memory_database, min_size=1, max_size=1) as pool:
        files = MemoryManager(root=tmp_path / "runtime")
        database = MemoryManager(db_pool=pool, root=files.root)
        first, second = (database, files) if database_first else (files, database)
        await first.initialize()
        await second.initialize()
        assert await first.import_legacy_vectors(source) == {"imported": 1, "already_imported": False}
        assert await second.import_legacy_vectors(source) == {"imported": 1, "already_imported": False}
        assert [item.content for item in await second.recall("observatory")] == ["The observatory door is blue"]
        assert await first.import_legacy_vectors(source) == {"imported": 0, "already_imported": True}
    assert source.read_bytes() == original


async def test_database_import_completion_follows_database_across_workspace_roots(memory_database, tmp_path):
    source = tmp_path / "legacy-vectors.db"
    legacy_vectors(source)
    async with asyncpg.create_pool(memory_database, min_size=1, max_size=1) as pool:
        original = MemoryManager(db_pool=pool, root=tmp_path / "original")
        await original.initialize()
        await original.import_legacy_vectors(source)
        imported = (await original.recall("observatory"))[0]
        assert await original.forget(imported.id) is True
        fresh = MemoryManager(db_pool=pool, root=tmp_path / "fresh")
        await fresh.initialize()
        assert await fresh.import_legacy_vectors(source) == {"imported": 0, "already_imported": True}
        assert await fresh.recall("observatory") == []


async def test_legacy_import_completion_is_scoped_to_each_database(memory_database, temporary_postgres, tmp_path):
    from urllib.parse import urlsplit, urlunsplit
    from uuid import uuid4

    first = urlsplit(memory_database)
    second_name = "memory_import_" + uuid4().hex
    admin = await asyncpg.connect(temporary_postgres)
    try:
        await admin.execute(f'CREATE DATABASE "{second_name}" TEMPLATE "{first.path.lstrip("/")}"')
    finally:
        await admin.close()
    second_dsn = urlunsplit(first._replace(path="/" + second_name))
    source = tmp_path / "legacy-vectors.db"
    legacy_vectors(source)
    for dsn in (memory_database, second_dsn):
        async with asyncpg.create_pool(dsn, min_size=1, max_size=1) as pool:
            destination = MemoryManager(db_pool=pool, root=tmp_path / "runtime")
            await destination.initialize()
            assert await destination.import_legacy_vectors(source) == {"imported": 1, "already_imported": False}
            assert [item.content for item in await destination.recall("observatory")] == ["The observatory door is blue"]


async def test_database_import_failure_rolls_back_rows_and_allows_retry(memory_database, tmp_path):
    source = tmp_path / "legacy-vectors.db"
    legacy_vectors(source, [
        ("first", "hash-first", "first observatory fact", "[]", "manual", None, "{}", 1700000000.0),
        ("second", "hash-second", "second observatory fact", "[]", "manual", None, "{}", 1700000001.0),
    ])
    original = source.read_bytes()
    async with asyncpg.create_pool(memory_database, min_size=1, max_size=1) as pool:
        await pool.execute("ALTER TABLE memories ADD CONSTRAINT synthetic_import_failure CHECK (content <> 'second observatory fact')")
        memory = MemoryManager(db_pool=pool, root=tmp_path / "runtime")
        await memory.initialize()
        with pytest.raises(asyncpg.CheckViolationError):
            await asyncio.wait_for(memory.import_legacy_vectors(source), timeout=5)
        assert await memory.list_memories(limit=None) == []
        await pool.execute("ALTER TABLE memories DROP CONSTRAINT synthetic_import_failure")
        fresh = MemoryManager(db_pool=pool, root=memory.root)
        await fresh.initialize()
        assert await fresh.import_legacy_vectors(source) == {"imported": 2, "already_imported": False}
        assert len(await fresh.list_memories(limit=None)) == 2
    assert source.read_bytes() == original


async def test_competing_database_imports_share_completion(memory_database, tmp_path):
    source = tmp_path / "legacy-vectors.db"
    legacy_vectors(source)
    async with asyncpg.create_pool(memory_database, min_size=2, max_size=2) as pool:
        first = MemoryManager(db_pool=pool, root=tmp_path / "first")
        second = MemoryManager(db_pool=pool, root=tmp_path / "second")
        await first.initialize()
        await second.initialize()
        results = await asyncio.wait_for(asyncio.gather(
            first.import_legacy_vectors(source), second.import_legacy_vectors(source),
        ), timeout=5)
        assert sorted(result["imported"] for result in results) == [0, 1]
        assert sum(result["already_imported"] for result in results) == 1
        assert len(await first.list_memories(limit=None)) == 1


@pytest.mark.parametrize("forget", [False, True], ids=["single-copy", "preserve-forget"])
async def test_competing_file_imports_share_completion(tmp_path, monkeypatch, forget):
    source = tmp_path / "legacy-vectors.db"
    legacy_vectors(source)
    memory = MemoryManager(root=tmp_path / "runtime")
    await memory.initialize()
    store = memory._store_memory_file
    entered = asyncio.Event()
    release = asyncio.Event()
    later = asyncio.Event()
    writes = 0

    async def delayed_store(record):
        nonlocal writes
        writes += 1
        if writes == 1:
            entered.set()
            await release.wait()
        elif forget:
            await later.wait()
        await store(record)

    monkeypatch.setattr(memory, "_store_memory_file", delayed_store)
    first = asyncio.create_task(memory.import_legacy_vectors(source))
    await asyncio.wait_for(entered.wait(), timeout=5)
    second = asyncio.create_task(memory.import_legacy_vectors(source))
    try:
        # Overlap the second source read with the first pending write.
        await asyncio.sleep(0.05)
        release.set()
        result = await asyncio.wait_for(first, timeout=5)
        if forget:
            identifier = (await memory.recall("observatory"))[0].id
            assert await memory.forget(identifier) is True
        later.set()
        again = await asyncio.wait_for(second, timeout=5)
        assert result == {"imported": 1, "already_imported": False}
        assert again == {"imported": 0, "already_imported": True}
        fresh = MemoryManager(root=memory.root)
        await fresh.initialize()
        assert await memory.recall("observatory") == await fresh.recall("observatory")
        assert len(await fresh.recall("observatory")) == (0 if forget else 1)
    finally:
        release.set()
        later.set()
        await asyncio.gather(first, second, return_exceptions=True)


async def test_database_import_denied_marker_creation_leaves_no_rows(memory_database, tmp_path):
    from uuid import uuid4

    role = "import_limited_" + uuid4().hex
    admin = await asyncpg.connect(memory_database)
    try:
        await admin.execute(f'CREATE ROLE "{role}" LOGIN')
        await admin.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await admin.execute(f'GRANT SELECT, INSERT ON memories TO "{role}"')
    finally:
        await admin.close()
    source = tmp_path / "legacy-vectors.db"
    legacy_vectors(source)
    original = source.read_bytes()
    restricted_dsn = memory_database.replace("postgres@", role + "@")
    async with asyncpg.create_pool(restricted_dsn, min_size=1, max_size=1) as pool:
        memory = MemoryManager(db_pool=pool, root=tmp_path / "runtime")
        await memory.initialize()
        with pytest.raises(PermissionError, match="create hyperclaw_memory_imports"):
            await memory.import_legacy_vectors(source)
        assert await memory.list_memories(limit=None) == []
    assert source.read_bytes() == original


async def test_bridge_memory_tools_share_canonical_runtime_storage(tmp_path, monkeypatch):
    # Binding an adapter without using it in Orchestrator leaves both directions broken.
    monkeypatch.setenv("HYPERCLAW_ROOT", str(tmp_path))
    requests = 0
    search_result = None

    def respond(request):
        nonlocal requests, search_result
        requests += 1
        if requests in (1, 3):
            name = "memory_store" if requests == 1 else "memory_search"
            inputs = {"content": "The observatory door is blue"} if requests == 1 else {"query": "library"}
            return httpx.Response(200, json=wire_message("", [{
                "type": "tool_use", "id": f"memory-{requests}", "name": name, "input": inputs,
            }], "tool_use"))
        if requests == 4:
            search_result = json.loads(request.content)["messages"][-1]["content"][0]["content"]
        return httpx.Response(200, json=wire_message("Done."))

    app = await bind_runtime(monkeypatch, tmp_path, respond)
    bridge = make_bridge(monkeypatch, [
        {"name": name, "input_schema": {"type": "object", "properties": {}}}
        for name in ("memory_store", "memory_search")
    ], reject_legacy_executor)
    try:
        canonical_id = await app.remember("The library closes at noon")
        assert (await bridge.execute("Store this fact", 892))["success"] is True
        fresh = MemoryManager(root=tmp_path)
        await fresh.initialize()
        assert [item.content for item in await fresh.recall("observatory")] == ["The observatory door is blue"]
        assert (await bridge.execute("Find the library hours", 892))["success"] is True
        assert [item["id"] for item in json.loads(search_result)] == [canonical_id]
    finally:
        await app.shutdown()


async def test_memory_tools_respect_session_visibility_for_search_list_stats_and_forget(memory):
    from hyperclaw.memory_tools import bind_memory_tools

    global_id = await memory.remember("global observatory fact")
    own_id = await memory.remember("current observatory fact", metadata={"session_id": "current"})
    other_id = await memory.remember("private observatory fact", metadata={"session_id": "other"}, importance=0.9)
    execute = bind_memory_tools(memory, reject_legacy_executor, session_id="current")
    for name, inputs in [("memory_search", {"query": "observatory", "limit": 2}), ("memory_list", {"limit": 2})]:
        assert {item["id"] for item in json.loads(await execute(name, inputs))} == {global_id, own_id}
    assert json.loads(await execute("memory_stats", {}))["total_memories"] == 2
    assert await execute("memory_forget", {"memory_id": other_id}) == "Memory not found"
    assert await execute("memory_forget", {"query": "private"}) == "No matching memory found"
    assert [item.id for item in await memory.recall("private")] == [other_id]
    assert await execute("memory_forget", {"memory_id": own_id}) == "Deleted 1 memory"
