"""Durable approval/receipt transitions must survive reopening and never replay effects."""
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from hyperclaw.contracts import Checkpoint, Conflict, Message, RunRequest, ToolCall, ToolReceipt
from hyperclaw.store import Store


async def running(store):
    session = await store.create_session()
    run = await store.submit(RunRequest(session_id=session.id, generation=0, request_id='one', text='write'))
    await store.next_run()
    await store.save_checkpoint(run.id, Checkpoint(messages=[Message(role='user', content='write')]))
    return run


async def test_approval_survives_reopen_and_binds_exact_call_and_policy(tmp_path):
    store = await Store.open(tmp_path)
    run = await running(store)
    call = ToolCall(id='call-1', name='workspace_write', arguments={'path': 'answer', 'content': 'hello'})
    inv = await store.prepare_invocation(run.id, call, 'policy-a', 'workspace-a', 'write')
    approval = await store.require_approval(inv.id)
    assert 86398 < (datetime.fromisoformat(approval.expires_at)-datetime.now(timezone.utc)).total_seconds() <= 86400
    with pytest.raises(Conflict):
        await store.submit(run.request.model_copy(update={'request_id':'competing'}))
    assert (await store.get_run(run.id)).status == 'waiting_approval'
    with pytest.raises(Conflict):
        await store.reset_session(run.request.session_id, 0)
    with pytest.raises(Conflict):
        await store.prepare_invocation(run.id, call.model_copy(update={'arguments': {'path': 'other'}}), 'policy-a', 'workspace-a', 'write')
    await store.close()
    store = await Store.open(tmp_path)
    try:
        await store.recover()
        assert (await store.get_run(run.id)).status == 'waiting_approval'
        assert (await store.load_checkpoint(run.id)).messages[0].content == 'write'
        with pytest.raises(Conflict):
            await store.decide_approval(approval.id, True, 'changed', 'policy-a')
        resumed = await store.decide_approval(approval.id, True, inv.arguments_sha256, 'policy-a')
        assert resumed.status == 'queued'
        assert (await store.next_run()).id == run.id
        assert (await store.get_invocation(inv.id)).approved
    finally:
        await store.close()


async def test_receipt_is_unique_and_cannot_be_replaced(tmp_path):
    store = await Store.open(tmp_path)
    try:
        run = await running(store)
        call = ToolCall(id='read-1', name='workspace_read', arguments={'path': 'answer'})
        inv = await store.prepare_invocation(run.id, call, 'policy-a', 'workspace-a', 'read')
        await store.mark_invocation_running(inv.id)
        receipt = ToolReceipt(invocation_id=inv.id, status='succeeded', output='hello')
        assert await store.complete_invocation(receipt) == receipt
        assert await store.complete_invocation(receipt) == receipt
        with pytest.raises(Conflict):
            await store.complete_invocation(receipt.model_copy(update={'output': 'other'}))
        assert (await store.prepare_invocation(run.id, call, 'policy-a', 'workspace-a', 'read')).receipt == receipt
    finally:
        await store.close()


async def test_expiry_does_not_grant_authority_and_budget_excludes_wait(tmp_path):
    store = await Store.open(tmp_path)
    try:
        run = await running(store)
        # Charge a measurable active interval without a slow sleep.
        past = (datetime.now(timezone.utc) - timedelta(seconds=17)).isoformat()
        await store._call(lambda: store._db.execute('UPDATE runs SET active_since=? WHERE id=?', (past, run.id)))
        inv = await store.prepare_invocation(run.id, ToolCall(id='cmd', name='command', arguments={'argv': ['true']}), 'p', 'w', 'execute')
        approval = await store.require_approval(inv.id)
        before = await store.elapsed(run.id)
        assert 17 <= before < 19
        await store._call(lambda: store._db.execute('UPDATE approvals SET expires_at=? WHERE id=?', (past, approval.id)))
        await store.expire_approvals()
        assert (await store.get_run(run.id)).status == 'failed'
        assert await store.elapsed(run.id) < before + 0.2
        with pytest.raises(Conflict):
            await store.decide_approval(approval.id, True, inv.arguments_sha256, 'p')
    finally:
        await store.close()


async def test_grants_are_scoped_and_uncertain_runs_cannot_retry(tmp_path):
    store = await Store.open(tmp_path)
    try:
        await store.grant('workspace-a', 'write')
        assert await store.grants('workspace-a') == {'write'}
        assert await store.grants('workspace-b') == set()
        run = await running(store)
        await store.finish(run.id, 'uncertain')
        with pytest.raises(Conflict):
            await store.submit(run.request.model_copy(update={'request_id': 'retry', 'retry_of': run.id}))
    finally:
        await store.close()


async def test_m1_database_migration_preserves_history_and_original_backup(tmp_path):
    import sqlite3
    from hyperclaw.config import ensure_root
    from hyperclaw.store import MIGRATIONS
    ensure_root(tmp_path)
    payload = '{"session_id":"s","generation":0,"request_id":"r","text":"hello","retry_of":null}'
    with sqlite3.connect(tmp_path / 'runtime.sqlite3') as db:
        for statement in MIGRATIONS[0][1]:
            db.execute(statement)
        db.execute('UPDATE schema_version SET version=1')
        db.execute("INSERT INTO sessions VALUES ('s',0)")
        db.execute("INSERT INTO runs VALUES ('r','s',0,'r',?,'succeeded','world',NULL,'2026-09-09T00:00:00+00:00')", (payload,))
        db.execute("INSERT INTO messages(run_id,role,content) VALUES ('r','user','hello'),('r','assistant','world')")
        db.execute("INSERT INTO events VALUES ('r',1,'run.finished','2026-09-09T00:00:00+00:00','{\"status\":\"succeeded\"}')")
    store = await Store.open(tmp_path)
    try:
        migrated = await store.get_run('r')
        assert migrated.output == 'world'
        assert migrated.request.tools == ()
        assert (await store.submit(migrated.request)).id == 'r'
        assert [m.content for m in await store.history('s', 0)] == ['hello', 'world']
        assert (await store.events('r'))[0].data == {'status': 'succeeded'}
        assert await store._call(lambda: store._db.execute('PRAGMA foreign_key_check').fetchall()) == []
        backups = list(tmp_path.glob('backup-v1-*.sqlite3'))
        assert len(backups) == 1
        with sqlite3.connect(backups[0]) as db:
            assert db.execute('SELECT version FROM schema_version').fetchone()[0] == 1
    finally:
        await store.close()
