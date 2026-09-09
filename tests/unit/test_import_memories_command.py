"""Explicit legacy import preserves facts and does not require a model server."""
import json
import os
from pathlib import Path
import sqlite3
import socket
import subprocess
import sys

import asyncpg
import pytest

from hyperclaw.memory_manager import MemoryManager
from tests.unit.test_memory_persistence import memory_database  # noqa: F401
from tests.unit.test_state_persistence import temporary_postgres  # noqa: F401


def legacy_source(path):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE embeddings (id TEXT PRIMARY KEY, content_hash TEXT, content TEXT, embedding TEXT, source TEXT, domain TEXT, metadata TEXT, created_at REAL)")
        connection.execute("INSERT INTO embeddings VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("old123", "hash", "The example nickname is cobalt-gannet", "[]", "conversation", "demo", json.dumps({"tag": "example"}), 1700000000.0))


@pytest.mark.asyncio
async def test_import_command_makes_legacy_fact_recallable_without_changing_source(tmp_path):
    source = tmp_path / "legacy.db"
    legacy_source(source)
    original = source.read_bytes()
    root = tmp_path / "runtime"
    environment = {**os.environ, "PYTHON_DOTENV_DISABLED": "1", "HYPERCLAW_ROOT": str(root),
                   "HYPERCLAW_ENABLE_DATABASE": "0"}
    command = [sys.executable, "-m", "hyperclaw", "import-memories", str(source)]
    result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    memory = MemoryManager(root=root)
    await memory.initialize()
    recalled = await memory.recall("cobalt-gannet")
    assert [record.content for record in recalled] == ["The example nickname is cobalt-gannet"]
    again = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=15)
    assert again.returncode == 0, again.stderr
    fresh = MemoryManager(root=root)
    await fresh.initialize()
    assert len(await fresh.recall("cobalt-gannet")) == 1
    assert source.read_bytes() == original


def test_import_command_database_outage_does_not_fall_back_to_files(tmp_path):
    source = tmp_path / "legacy.db"
    legacy_source(source)
    original = source.read_bytes()
    root = tmp_path / "runtime"
    # Reserve a loopback port without listening: no existing database is contacted.
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        port = unavailable.getsockname()[1]
        environment = {**os.environ, "PYTHON_DOTENV_DISABLED": "1", "HYPERCLAW_ROOT": str(root),
                       "HYPERCLAW_ENABLE_DATABASE": "1",
                       "DATABASE_URL": f"postgresql://synthetic:synthetic@127.0.0.1:{port}/unavailable"}
        result = subprocess.run(
            [sys.executable, "-m", "hyperclaw", "import-memories", str(source)],
            env=environment, capture_output=True, text=True, timeout=15,
        )
    assert result.returncode != 0
    assert "Memory import failed" in result.stdout
    assert not list((root / "memory" / "entries").glob("*.json"))
    assert not list((root / "memory" / "imports").glob("*.json"))
    assert source.read_bytes() == original


@pytest.mark.asyncio
async def test_import_command_switches_destination_and_preserves_database_forget(tmp_path, memory_database):
    source = tmp_path / "legacy.db"
    legacy_source(source)
    original = source.read_bytes()
    root = tmp_path / "runtime"
    environment = {**os.environ, "PYTHON_DOTENV_DISABLED": "1", "HYPERCLAW_ROOT": str(root),
                   "HYPERCLAW_ENABLE_DATABASE": "0", "DATABASE_URL": memory_database}
    command = [sys.executable, "-m", "hyperclaw", "import-memories", str(source)]
    files = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=15)
    assert files.returncode == 0, files.stderr
    environment["HYPERCLAW_ENABLE_DATABASE"] = "1"
    database = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=15)
    assert database.returncode == 0, database.stderr
    assert json.loads(database.stdout) == {"imported": 1, "already_imported": False}
    async with asyncpg.create_pool(memory_database, min_size=1, max_size=1) as pool:
        memory = MemoryManager(db_pool=pool, root=root)
        await memory.initialize()
        recalled = await memory.recall("cobalt-gannet")
        assert [record.content for record in recalled] == ["The example nickname is cobalt-gannet"]
        assert await memory.forget(recalled[0].id) is True
        again = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=15)
        assert again.returncode == 0, again.stderr
        assert json.loads(again.stdout) == {"imported": 0, "already_imported": True}
        assert await memory.recall("cobalt-gannet") == []
    assert source.read_bytes() == original
