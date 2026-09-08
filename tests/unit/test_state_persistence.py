"""State persistence against a disposable PostgreSQL cluster, when installed.

The fixture starts its own server with TCP disabled. It never uses DATABASE_URL
or contacts an existing database. These tests skip if server binaries are absent.
"""

from datetime import datetime
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
from urllib.parse import urlencode
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from core.hyperstate.schema import HyperState, Task
from core.hyperstate.store import HyperStateStore

pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module")
def temporary_postgres():
    candidates = [
        Path(binary).parent
        for binary in [shutil.which("initdb")]
        if binary
    ]
    candidates.extend(Path("/opt/homebrew/opt").glob("postgresql*/bin"))
    candidates.extend(Path("/usr/lib/postgresql").glob("*/bin"))
    pg_bin = next((path for path in candidates if (path / "initdb").exists()), None)
    if pg_bin is None or (hasattr(os, "geteuid") and os.geteuid() == 0):
        pytest.skip("Disposable PostgreSQL tests need server binaries and a non-root user")

    with tempfile.TemporaryDirectory(prefix="hc-state-pg-", dir="/tmp") as directory:
        root = Path(directory)
        data = root / "data"
        socket = root / "socket"
        socket.mkdir()
        subprocess.run(
            [str(pg_bin / "initdb"), "-D", str(data), "-A", "trust", "-U", "postgres", "--no-locale", "-E", "UTF8"],
            check=True, capture_output=True, text=True, timeout=30,
        )
        subprocess.run(
            [str(pg_bin / "pg_ctl"), "-D", str(data), "-l", str(root / "server.log"),
             "-o", f"-F -k {socket} -c listen_addresses=''", "-w", "start"],
            check=True, capture_output=True, text=True, timeout=30,
        )
        try:
            yield "postgresql://postgres@/postgres?" + urlencode({"host": str(socket)})
        finally:
            subprocess.run(
                [str(pg_bin / "pg_ctl"), "-D", str(data), "-m", "immediate", "-w", "stop"],
                check=True, capture_output=True, text=True, timeout=30,
            )


@pytest_asyncio.fixture
async def state_store(temporary_postgres):
    database = "state_test_" + uuid4().hex
    admin = await asyncpg.connect(temporary_postgres)
    try:
        await admin.execute(f'CREATE DATABASE "{database}"')
    finally:
        await admin.close()
    dsn = temporary_postgres.replace("/postgres?", f"/{database}?")
    store = HyperStateStore(dsn)
    await store.connect()
    connection = await asyncpg.connect(dsn)
    try:
        yield store, connection
    finally:
        await connection.close()
        await store.close()


async def test_create_tables_supports_save_reload_and_version_history(state_store):
    store, _ = state_store
    await store.create_tables()
    state = HyperState(domain="business", task=Task(goal="initial goal"))
    await store.save_state(state)
    state.task.goal = "revised goal"
    state.state_version = 1
    await store.save_state(state)
    await store.create_tables()

    loaded = await store.load_state(state.state_id)
    assert loaded.task.goal == "revised goal"
    assert loaded.state_version == 1
    history = await store.get_state_history(state.state_id)
    assert [entry["state_version"] for entry in history] == [0, 1]
    assert [entry["state_data"]["task"]["goal"] for entry in history] == ["initial goal", "revised goal"]
    assert all(datetime.fromisoformat(entry["recorded_at"]).tzinfo for entry in history)
    assert [entry.state_id for entry in await store.list_states(domain="business")] == [state.state_id]
    await store.archive_state(state.state_id)
    assert await store.list_states(domain="business") == []
    assert (await store.load_state(state.state_id)).task.goal == "revised goal"


async def test_shipped_state_schema_works_without_runtime_table_creation(state_store):
    store, connection = state_store
    # State setup has no pgvector dependency. Only create its external FK target
    # and UUID extension so this test also runs on plain PostgreSQL installations.
    await connection.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    await connection.execute("CREATE TABLE knowledge_nodes (id UUID PRIMARY KEY)")
    schema = (Path(__file__).resolve().parents[2] / "schema" / "init.sql").read_text()
    state_schema = schema.split("-- STATE MANAGEMENT TABLES", 1)[1].split("-- TASK MANAGEMENT TABLES", 1)[0]
    await connection.execute(state_schema)
    legacy_id = await connection.fetchval(
        "INSERT INTO hyperstate (domain, task_goal, state_data) "
        "VALUES ('business', 'legacy goal', '{\"keep\": true}') RETURNING state_id"
    )
    await connection.execute(
        "INSERT INTO state_mutations (state_id, mutation_type, after_state) "
        "VALUES ($1, 'legacy', '{\"keep\": true}')", legacy_id,
    )
    state = HyperState(domain="personal", task=Task(goal="new state"))
    await store.save_state(state)
    await connection.execute(state_schema)
    await store.create_tables()
    assert (await store.load_state(state.state_id)).task.goal == "new state"
    assert (await store.get_state_history(state.state_id))[0]["state_data"]["task"]["goal"] == "new state"
    assert await connection.fetchval("SELECT task_goal FROM hyperstate WHERE state_id = $1", legacy_id) == "legacy goal"
    assert await connection.fetchval("SELECT count(*) FROM state_mutations WHERE state_id = $1", legacy_id) == 1


async def test_existing_canonical_history_is_retained_when_tables_are_initialized(state_store):
    store, connection = state_store
    await store.create_tables()
    state = HyperState(domain="scientific", task=Task(goal="retained history"))
    # This is the history format shipped by the previous create_tables().
    await connection.execute(
        "INSERT INTO hyperstates (state_id, domain, data, created_at, updated_at) "
        "VALUES ($1, $2, $3::jsonb, $4, $4)",
        state.state_id, state.domain, state.model_dump_json(), state.created_at,
    )
    await connection.execute(
        "INSERT INTO hyperstate_history (state_id, state_version, data) VALUES ($1, 0, $2::jsonb)",
        state.state_id, state.model_dump_json(),
    )
    await store.create_tables()
    state.state_version = 1
    await store.save_state(state)
    history = await store.get_state_history(state.state_id)
    assert [entry["state_version"] for entry in history] == [0, 1]
    assert history[0]["state_data"]["task"]["goal"] == "retained history"


async def test_shipped_update_triggers_can_be_initialized_again(state_store):
    _, connection = state_store
    for table in ("knowledge_nodes", "hyperstate", "integration_state"):
        await connection.execute(
            f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, updated_at TIMESTAMPTZ)"
        )
        await connection.execute(f"INSERT INTO {table} VALUES (1, '2000-01-01T00:00:00Z')")
    schema = (Path(__file__).resolve().parents[2] / "schema" / "init.sql").read_text()
    helpers = schema.split("-- Function to update updated_at timestamp", 1)[1].split("-- Function for semantic search on memories", 1)[0]
    await connection.execute(helpers)
    await connection.execute(helpers)
    for table in ("knowledge_nodes", "hyperstate", "integration_state"):
        await connection.execute(f"UPDATE {table} SET id = 2 WHERE id = 1")
        assert await connection.fetchval(f"SELECT updated_at > '2000-01-01' FROM {table}")
