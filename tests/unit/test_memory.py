import asyncio
from datetime import datetime, timedelta, timezone
import sqlite3

from pydantic import ValidationError
import pytest

from hyperclaw import contracts
from hyperclaw.config import ensure_root
from hyperclaw.contracts import (
    Checkpoint,
    Conflict,
    InvalidRequest,
    MemoryCorrectArguments,
    MemoryForgetArguments,
    MemoryRememberArguments,
    MemoryScope,
    MemorySearchArguments,
    Message,
    NotFound,
    RunRequest,
    StorageFailure,
)
from hyperclaw.memory import Memory
from hyperclaw.store import MIGRATIONS, Store


UTC = timezone.utc


class Clock:
    def __init__(self, value=datetime(2030, 1, 1, tzinfo=UTC)):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, **delta):
        self.value += timedelta(**delta)


async def session_scope(store, workspace='workspace-a'):
    session = await store.create_session()
    return MemoryScope(workspace_id=workspace, session_id=session.id)


async def test_remember_search_defaults_shared_visibility_and_scope_before_limit(tmp_path):
    store = await Store.open(tmp_path)
    memory = Memory(store)
    try:
        visible = await session_scope(store)
        other = await session_scope(store)
        foreign_workspace = MemoryScope(workspace_id='workspace-b', session_id=visible.session_id)
        shared = MemoryScope(workspace_id='workspace-a')

        target = await memory.remember(visible, 'The handoff checklist includes the telescope.')
        common = await memory.remember(shared, 'The handoff checklist is posted for everyone.')
        for index in range(8):
            await memory.remember(other, f'handoff checklist {index}')
            await memory.remember(foreign_workspace, f'handoff checklist foreign {index}')

        assert [record.id for record in await memory.search(visible, 'handoff checklist')] == [
            target.id, common.id,
        ]
        assert [record.id for record in await memory.search(shared, 'handoff checklist')] == [common.id]
        assert await memory.search(visible, '!!! "" --') == []
        assert [record.id for record in await memory.search(visible, '"handoff" OR checklist')] == [
            target.id, common.id,
        ]
    finally:
        await store.close()


def test_memory_contracts_validate_utf8_bounds_strict_limits_and_aware_json_times():
    assert MemoryRememberArguments(text='x').scope == 'session'
    assert MemorySearchArguments(query='x').limit == 5
    assert MemoryCorrectArguments(record_id='r', text='x').scope == 'session'
    assert MemoryForgetArguments(record_id='r').scope == 'session'
    normalized = MemoryRememberArguments(text='x', valid_until='2030-01-01T05:30:00+05:30')
    assert normalized.valid_until == datetime(2030, 1, 1, tzinfo=UTC)

    invalid_remember = [
        {'text': ''}, {'text': '   '}, {'text': 'nul\0byte'}, {'text': 'é' * 1025},
        {'text': 'x', 'valid_until': '2030-01-01T00:00:00'},
        {'text': 'x', 'valid_until': 1}, {'text': 'x', 'valid_until': True},
    ]
    for values in invalid_remember:
        with pytest.raises(ValidationError):
            MemoryRememberArguments(**values)
    for query in ['', ' ', 'q' * 1025]:
        with pytest.raises(ValidationError):
            MemorySearchArguments(query=query)
    for limit in [True, 0, 6, 1.0]:
        with pytest.raises(ValidationError):
            MemorySearchArguments(query='x', limit=limit)


async def test_public_boundary_revalidates_constructed_values_and_time_relationship(tmp_path):
    store = await Store.open(tmp_path)
    memory = Memory(store, clock=lambda: datetime(2030, 1, 1, tzinfo=UTC))
    try:
        scope = await session_scope(store)
        malformed = MemoryScope.model_construct(workspace_id='', session_id=scope.session_id)
        with pytest.raises(InvalidRequest):
            await memory.remember(malformed, 'valid')
        with pytest.raises(InvalidRequest):
            await memory.remember(scope, 'x' * 2049)
        with pytest.raises(InvalidRequest):
            await memory.remember(scope, 'valid', valid_until=datetime(2030, 1, 1, tzinfo=UTC))
        with pytest.raises(InvalidRequest):
            await memory.search(scope, 'valid', True)
        with pytest.raises(InvalidRequest):
            await Memory(store, clock=lambda: datetime(2030, 1, 1)).remember(scope, 'valid')
    finally:
        await store.close()


async def test_correction_is_immutable_atomic_and_stale_versions_conflict(tmp_path):
    store = await Store.open(tmp_path)
    memory = Memory(store)
    try:
        scope = await session_scope(store)
        old = await memory.remember(scope, 'The drawer label is juniper 482.')
        new = await memory.correct(old.id, 'The drawer label is cedar 719.', scope)
        assert (new.supersedes, new.version, new.status) == (old.id, 2, 'active')
        assert await memory.search(scope, 'juniper 482') == []
        assert [record.id for record in await memory.search(scope, 'cedar 719')] == [new.id]
        with pytest.raises(Conflict) as caught:
            await memory.correct(old.id, 'stale change', scope)
        assert caught.value.code == 'stale_memory'

        statuses = await store._call(lambda: [tuple(row) for row in store._db.execute(
            'SELECT id,text,status FROM memory_records ORDER BY rowid').fetchall()])
        assert statuses == [
            (old.id, 'The drawer label is juniper 482.', 'superseded'),
            (new.id, 'The drawer label is cedar 719.', 'active'),
        ]
    finally:
        await store.close()


async def test_failed_correction_rolls_back_record_version_and_fts_index(tmp_path):
    store = await Store.open(tmp_path)
    memory = Memory(store)
    try:
        scope = await session_scope(store)
        old = await memory.remember(scope, 'Archive marker is juniper.')
        await store._call(lambda: store._db.execute("""CREATE TRIGGER fail_memory_correction
            BEFORE INSERT ON memory_records WHEN new.supersedes IS NOT NULL BEGIN
            SELECT RAISE(ABORT,'injected'); END"""))

        with pytest.raises(StorageFailure):
            await memory.correct(old.id, 'Archive marker is cedar.', scope)

        assert [record.id for record in await memory.search(scope, 'juniper')] == [old.id]
        assert await memory.search(scope, 'cedar') == []
        assert await store._call(lambda: [tuple(row) for row in store._db.execute(
            'SELECT id,version,status FROM memory_records').fetchall()]) == [
                (old.id, 1, 'active'),
            ]
        assert await store._call(lambda: store._db.execute(
            "INSERT INTO memory_fts(memory_fts) VALUES ('integrity-check')").fetchone()) is None
    finally:
        await store.close()


async def test_simultaneous_corrections_have_one_winner(tmp_path):
    store = await Store.open(tmp_path)
    memory = Memory(store)
    try:
        scope = await session_scope(store)
        old = await memory.remember(scope, 'Original constellation is Lyra.')
        results = await asyncio.gather(
            memory.correct(old.id, 'Current constellation is Orion.', scope),
            memory.correct(old.id, 'Current constellation is Draco.', scope),
            return_exceptions=True,
        )
        winners = [result for result in results if not isinstance(result, BaseException)]
        conflicts = [result for result in results if isinstance(result, Conflict)]
        assert len(winners) == len(conflicts) == 1
        assert [record.id for record in await memory.search(scope, 'Current constellation')] == [winners[0].id]
    finally:
        await store.close()


async def test_forget_is_idempotent_but_scope_is_exact(tmp_path):
    store = await Store.open(tmp_path)
    memory = Memory(store)
    try:
        private = await session_scope(store)
        shared = MemoryScope(workspace_id=private.workspace_id)
        record = await memory.remember(shared, 'Shared ferry booking is lotus.')
        with pytest.raises(NotFound):
            await memory.forget(record.id, private)
        await memory.forget(record.id, shared)
        await memory.forget(record.id, shared)
        assert await memory.search(private, 'Shared ferry booking') == []
        with pytest.raises(Conflict):
            await memory.correct(record.id, 'Shared ferry booking is iris.', shared)
    finally:
        await store.close()


async def test_expiry_boundary_correction_reopen_and_reset_retention(tmp_path):
    clock = Clock()
    store = await Store.open(tmp_path)
    memory = Memory(store, clock=clock)
    scope = await session_scope(store)
    record = await memory.remember(scope, 'Visitor token is topaz.', valid_until=clock() + timedelta(seconds=60))
    assert record.observed_at.isoformat() == '2030-01-01T00:00:00+00:00'
    assert record.valid_until.isoformat() == '2030-01-01T00:01:00+00:00'
    assert await store._call(lambda: tuple(store._db.execute(
        'SELECT observed_at,valid_until FROM memory_records WHERE id=?', (record.id,)).fetchone())) == (
            '2030-01-01T00:00:00.000000+00:00',
            '2030-01-01T00:01:00.000000+00:00',
        )
    clock.advance(seconds=60)
    assert await memory.search(scope, 'Visitor token') == []
    replacement = await memory.correct(record.id, 'Visitor token is amber.', scope)
    await store.reset_session(scope.session_id, 0)
    await store.close()

    reopened = await Store.open(tmp_path)
    try:
        assert [item.id for item in await Memory(reopened, clock=clock).search(scope, 'Visitor token')] == [replacement.id]
    finally:
        await reopened.close()


async def test_source_run_must_match_session_and_checkpoint_workspace(tmp_path):
    store = await Store.open(tmp_path)
    memory = Memory(store)
    try:
        source_scope = await session_scope(store, 'workspace-a')
        other_scope = await session_scope(store, 'workspace-a')
        run = await store.submit(RunRequest(session_id=source_scope.session_id, generation=0,
                                            request_id='source', text='source run'))
        await store.next_run()
        await store.save_checkpoint(run.id, Checkpoint(
            messages=[Message(role='user', content='source run')], workspace_id='workspace-a'))
        sourced = await memory.remember(source_scope, 'Sourced detail.', run.id)
        assert sourced.source_run_id == run.id
        with pytest.raises(InvalidRequest) as wrong_session:
            await memory.remember(other_scope, 'Wrong session.', run.id)
        assert wrong_session.value.code == 'invalid_memory_source'
        with pytest.raises(InvalidRequest) as wrong_workspace:
            await memory.remember(MemoryScope(workspace_id='workspace-b'), 'Wrong workspace.', run.id)
        assert wrong_workspace.value.code == 'invalid_memory_source'
        with pytest.raises(NotFound):
            await memory.remember(source_scope, 'Missing source.', 'missing')
    finally:
        await store.close()


def create_v3_database(root):
    ensure_root(root)
    with sqlite3.connect(root / 'runtime.sqlite3') as db:
        for migration_number, (_, statements) in enumerate(MIGRATIONS[:3], start=1):
            for statement in statements:
                db.execute(statement)
            db.execute('UPDATE schema_version SET version=?', (migration_number,))
        db.execute("INSERT INTO sessions VALUES ('existing',0)")
        request = RunRequest(session_id='existing', generation=0, request_id='historical',
                             text='history', tools=('workspace_read',))
        db.execute("""INSERT INTO runs(
            id,session_id,generation,request_id,payload_json,status,created_at
            ) VALUES ('historical','existing',0,'historical',?,'succeeded','2030-01-01T00:00:00+00:00')""",
                   (contracts.canonical(request.model_dump()),))
        db.execute("INSERT INTO messages(run_id,role,content) VALUES ('historical','user','history')")
        db.execute("INSERT INTO events VALUES ('historical',1,'run.finished','2030-01-01T00:00:00+00:00','{}')")
        db.execute("""INSERT INTO schedules VALUES(
            'historical-schedule','existing',0,'scheduled','2031-01-01T00:00:00+00:00',NULL,
            '[\"workspace_read\"]','{}','active',NULL)""")


async def test_v3_additive_migration_preserves_history_schedule_tools_and_fts_restart(tmp_path):
    create_v3_database(tmp_path)
    store = await Store.open(tmp_path)
    memory = Memory(store)
    try:
        scope = MemoryScope(workspace_id='workspace-a', session_id='existing')
        record = await memory.remember(scope, 'Persistent archive marker is quartz.')
        assert (await store.get_run('historical')).request.tools == ('workspace_read',)
        assert (await store.get_schedule('historical-schedule')).tools == ('workspace_read',)
        assert await store._call(lambda: store._db.execute('SELECT version FROM schema_version').fetchone()[0]) == 4
    finally:
        await store.close()

    reopened = await Store.open(tmp_path)
    try:
        assert [item.id for item in await Memory(reopened).search(scope, 'quartz')] == [record.id]
        assert await reopened._call(lambda: reopened._db.execute(
            "INSERT INTO memory_fts(memory_fts) VALUES ('integrity-check')").fetchone()) is None
    finally:
        await reopened.close()


async def test_failed_schema4_migration_rolls_back_version_tables_and_index_content(tmp_path, monkeypatch):
    import hyperclaw.store as store_module

    create_v3_database(tmp_path)
    original = store_module.MIGRATIONS
    destructive, statements = original[3]
    monkeypatch.setattr(store_module, 'MIGRATIONS', (
        *original[:3], (destructive, (*statements, 'SELECT * FROM injected_missing_table')),
    ))
    with pytest.raises(StorageFailure):
        await Store.open(tmp_path)
    with sqlite3.connect(tmp_path / 'runtime.sqlite3') as db:
        assert db.execute('SELECT version FROM schema_version').fetchone()[0] == 3
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'memory_%' ORDER BY name").fetchall() == []

    monkeypatch.setattr(store_module, 'MIGRATIONS', original)
    reopened = await Store.open(tmp_path)
    try:
        assert await reopened._call(lambda: reopened._db.execute(
            'SELECT version FROM schema_version').fetchone()[0]) == 4
    finally:
        await reopened.close()
