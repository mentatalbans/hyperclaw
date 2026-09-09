import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import threading

import pytest

from hyperclaw.contracts import Conflict, Failure, InvalidRequest, NotFound, RunRequest, StorageFailure, UnsupportedSchema
from hyperclaw.store import Store


async def request(store, text='hello', **changes):
    session = await store.create_session()
    return RunRequest(session_id=session.id, generation=0, request_id='first', text=text, **changes)


async def test_request_deduplication_survives_reopen(tmp_path):
    store = await Store.open(tmp_path)
    req = await request(store)
    first = await store.submit(req)
    assert (await store.submit(req)).id == first.id
    await store.close()
    store = await Store.open(tmp_path)
    try:
        assert (await store.submit(req)).id == first.id
        with pytest.raises(Conflict):
            await store.submit(req.model_copy(update={'text': 'different'}))
        assert [e.kind for e in await store.events(first.id)] == ['run.queued']
    finally:
        await store.close()


async def test_generation_busy_reset_and_success_history(tmp_path):
    store = await Store.open(tmp_path)
    try:
        req = await request(store)
        run = await store.submit(req)
        with pytest.raises(Conflict):
            await store.submit(req.model_copy(update={'request_id': 'second'}))
        with pytest.raises(Conflict):
            await store.reset_session(req.session_id, 0)
        assert (await store.next_run()).id == run.id
        await store.append(run.id, 'model.text', {'text': 'answer'})
        result = await store.finish(run.id, 'succeeded', output='answer')
        assert result.output == 'answer'
        assert (await store.finish(run.id, 'cancelled')).status == 'succeeded'
        events = await store.events(run.id)
        assert [e.seq for e in events] == [1, 2, 3, 4]
        assert events[-1].data == {'status': 'succeeded', 'verification': 'not_requested'}
        assert [m.content for m in await store.history(req.session_id, 0)] == ['hello', 'answer']
        other = await store.create_session()
        assert await store.history(other.id, 0) == []
        assert (await store.reset_session(req.session_id, 0)).generation == 1
        assert await store.history(req.session_id, 1) == []
        with pytest.raises(Conflict):
            await store.submit(req)  # even an idempotent old-generation request is stale
        with pytest.raises(Conflict):
            await store.reset_session(req.session_id, 0)
        assert (await store.get_run(run.id)).output == 'answer'
    finally:
        await store.close()


async def test_recovery_preserves_queued_and_excludes_incomplete_turns(tmp_path):
    store = await Store.open(tmp_path)
    req = await request(store)
    active = await store.submit(req)
    queued = await store.submit(await request(store, 'other'))
    assert (await store.next_run()).id == active.id
    await store.append(active.id, 'model.text', {'text': 'partial'})
    await store.close()
    store = await Store.open(tmp_path)
    try:
        assert [r.id for r in await store.recover()] == [active.id]
        assert await store.recover() == []
        assert (await store.get_run(active.id)).status == 'interrupted'
        assert (await store.get_run(queued.id)).status == 'queued'
        assert await store.history(req.session_id, 0) == []
        assert [e.kind for e in await store.events(active.id)].count('run.finished') == 1
    finally:
        await store.close()


async def test_retry_links_and_terminal_transitions(tmp_path):
    store = await Store.open(tmp_path)
    try:
        req = await request(store)
        first = await store.submit(req)
        with pytest.raises(Conflict):
            await store.submit(req.model_copy(update={'request_id': 'retry', 'retry_of': first.id}))
        with pytest.raises(Conflict):
            await store.finish(first.id, 'succeeded', output='never ran')
        with pytest.raises(InvalidRequest):
            await store.finish(first.id, 'running')
        await store.finish(first.id, 'cancelled')
        retry = await store.submit(req.model_copy(update={'request_id': 'retry', 'retry_of': first.id}))
        assert retry.request.retry_of == first.id
        with pytest.raises(Conflict):
            await store.append(first.id, 'model.text', {'text': 'late'})
        with pytest.raises(Conflict):
            await store.submit(await request(store, retry_of=first.id))
        await store.next_run()
        await store.finish(retry.id, 'failed', error=Failure(code='example', message='Known failure'))
        assert await store.history(req.session_id, 0) == []
    finally:
        await store.close()


async def test_public_input_and_unknown_ids(tmp_path):
    store = await Store.open(tmp_path)
    try:
        for operation in [store.get_run('missing'), store.get_session('missing'), store.events('missing')]:
            with pytest.raises(NotFound):
                await operation
        req = await request(store)
        run = await store.submit(req)
        for after in [-1, 2, 'bad', True]:
            with pytest.raises(Conflict):
                await store.events(run.id, after=after)
        with pytest.raises(InvalidRequest):
            await store.events(run.id, limit=0)
        for changes in [{'text': ''}, {'text': 'é' * 40000}, {'generation': -1}]:
            with pytest.raises(InvalidRequest):
                await store.submit(req.model_copy(update=changes))
        with pytest.raises(InvalidRequest):
            await store.reset_session(req.session_id, -1)
    finally:
        await store.close()


async def test_root_lock_is_held_until_close_in_another_process(tmp_path):
    store = await Store.open(tmp_path)
    script = '''import asyncio, sys
from pathlib import Path
from hyperclaw.store import Store
from hyperclaw.contracts import RootInUse
async def check():
    try:
        store = await Store.open(Path(sys.argv[1]))
    except RootInUse:
        print('root_in_use')
    else:
        print('owned')
        await store.close()
asyncio.run(check())
'''
    def probe():
        return subprocess.run([sys.executable, '-c', script, str(tmp_path)], capture_output=True, text=True, timeout=10)
    result = await asyncio.to_thread(probe)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'root_in_use'
    await store.close()
    result = await asyncio.to_thread(probe)
    assert result.stdout.strip() == 'owned'


async def test_failed_terminal_transaction_rolls_back_every_row(tmp_path):
    store = await Store.open(tmp_path)
    try:
        req = await request(store)
        run = await store.submit(req)
        await store.next_run()
        # Inject a SQLite failure at the final event, after state/message writes.
        await store._call(lambda: store._db.execute("CREATE TRIGGER fail_finish BEFORE INSERT ON events WHEN NEW.kind = 'run.finished' BEGIN SELECT RAISE(ABORT, 'injected'); END"))
        with pytest.raises(StorageFailure):
            await store.finish(run.id, 'succeeded', output='not committed')
        assert (await store.get_run(run.id)).status == 'running'
        assert await store.history(req.session_id, 0) == []
        assert [e.kind for e in await store.events(run.id)] == ['run.queued', 'run.started']
    finally:
        await store.close()


async def test_cancelled_caller_settles_admitted_transaction_before_close(tmp_path, monkeypatch):
    store = await Store.open(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = store._transaction
    def gated(fn):
        def work():
            value = fn()
            entered.set()
            assert release.wait(5)
            return value
        return original(work)
    monkeypatch.setattr(store, '_transaction', gated)
    task = asyncio.create_task(store.create_session())
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    await asyncio.sleep(0.02)
    assert not task.done()
    close = asyncio.create_task(store.close())
    await asyncio.sleep(0.02)
    assert not close.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await close
    with sqlite3.connect(tmp_path / 'runtime.sqlite3') as db:
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0] == 1


async def test_schema_version_pragmas_and_newer_schema_refusal(tmp_path):
    store = await Store.open(tmp_path)
    assert await store._call(lambda: [store._db.execute('PRAGMA ' + p).fetchone()[0] for p in ['foreign_keys', 'journal_mode', 'synchronous', 'busy_timeout']]) == [1, 'delete', 2, 5000]
    await store.close()
    with sqlite3.connect(tmp_path / 'runtime.sqlite3') as db:
        assert db.execute('SELECT version FROM schema_version').fetchone()[0] == 2
        db.execute('UPDATE schema_version SET version=99')
    with pytest.raises(UnsupportedSchema):
        await Store.open(tmp_path)
    # Failed open released ownership (schema failure again, not root_in_use).
    with pytest.raises(UnsupportedSchema):
        await Store.open(tmp_path)


async def test_failed_future_destructive_migration_rolls_back_and_has_backup(tmp_path, monkeypatch):
    import hyperclaw.store as module
    store = await Store.open(tmp_path)
    session = await store.create_session()
    await store.close()
    monkeypatch.setattr(module, 'MIGRATIONS', (*module.MIGRATIONS, (True, (
        'DROP TABLE sessions', 'SELECT * FROM table_that_does_not_exist',
    ))))
    with pytest.raises(StorageFailure):
        await Store.open(tmp_path)
    with sqlite3.connect(tmp_path / 'runtime.sqlite3') as db:
        assert db.execute('SELECT id FROM sessions').fetchone()[0] == session.id
        assert db.execute('SELECT version FROM schema_version').fetchone()[0] == 2
    with sqlite3.connect(next(tmp_path.glob('backup-v2-*.sqlite3'))) as db:
        assert db.execute('SELECT id FROM sessions').fetchone()[0] == session.id


async def test_corrected_migration_can_follow_failed_attempt_without_overwriting_backup(tmp_path, monkeypatch):
    import hyperclaw.store as module
    store = await Store.open(tmp_path)
    session = await store.create_session()
    await store.close()
    initial = module.MIGRATIONS
    monkeypatch.setattr(module, 'MIGRATIONS', (*initial, (True, ('SELECT * FROM missing_table',))))
    with pytest.raises(StorageFailure):
        await Store.open(tmp_path)
    backups = {p: p.read_bytes() for p in tmp_path.glob('backup-*.sqlite3')}
    monkeypatch.setattr(module, 'MIGRATIONS', (*initial, (True, ('CREATE TABLE new_table (id INTEGER)',))))
    reopened = await Store.open(tmp_path)
    try:
        assert (await reopened.get_session(session.id)).generation == 0
        assert all(p.read_bytes() == data for p, data in backups.items())
        assert len(list(tmp_path.glob('backup-*.sqlite3'))) == 2
    finally:
        await reopened.close()
