"""Populated v2 migration/restore and public behavior at actual storage boundaries.

Mutations caught: dropped migration rows or widened allowlists, replay of uncertain
writes, success without a committed receipt, stale filesystem authority on restore.
Only temporary roots, loopback providers, and bounded SQLite allocation are used.
"""
import asyncio
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4

import pytest

from hyperclaw.contracts import Approval, RunRequest, ToolCall, canonical
from hyperclaw.store import MIGRATIONS, Store
from tests.integration.test_cli_tools import pending_approval, write_reply
from tests.integration.test_memory import memory_call, search
from tests.integration.test_recovery import wait_for
from tests.integration.test_skills import write_skill
from tests.support.process import Process, events, submit
from tests.support.provider import ProviderStub, Reply

LAUNCHER = Path(__file__).resolve().parents[1] / 'support/storage_daemon.py'
STAMP = '2026-01-01T00:00:00+00:00'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def database_dump(path):
    with closing(sqlite3.connect(path)) as db:
        return list(db.iterdump())


def audit(root):
    with closing(sqlite3.connect(root / 'runtime.sqlite3')) as db:
        assert db.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        return {'integrity': 'ok', 'foreign_keys': [], 'database_sha256': digest(root / 'runtime.sqlite3')}


def evidence(record_property, name, value):
    # Saved in JUnit by test_battery and visible in focused pytest -s logs.
    text = json.dumps(value, sort_keys=True)
    record_property(name, text)
    print(f'{name}: {text}')


@pytest.mark.parametrize('stage,after_effect', [
    ('full_before', False), ('full_after', True),
    ('journal_before', False), ('commit_after', True),
])
def test_storage_failure_never_verifies_or_replays_effect(tmp_path, record_property, stage, after_effect):
    peer = ProviderStub()
    app = Process(tmp_path / 'runtime', peer.url, launcher=LAUNCHER)
    (app.root / 'test-storage-stage').write_text(stage)
    try:
        app.start()
        workspace = app.client.get('/v1/workspace').json()
        app.client.post('/v1/grants', json={'workspace_id': workspace['id'], 'capability': 'write'}).raise_for_status()
        peer.enqueue(write_reply())
        run = submit(app, 'Write answer.txt', tools=['workspace_write'])
        wait_for(lambda: app.client.get('/healthz').status_code == 503, app)
        observed = json.loads((app.root / 'test-storage-observed.json').read_text())
        assert observed['observed_effect'] is after_effect
        if stage.startswith('full'):
            assert observed['sqlite_errorcode'] == sqlite3.SQLITE_FULL == 13
            assert observed['sqlite_errorname'] == 'SQLITE_FULL'
            assert observed['max_page_count'] == observed['page_count']
        elif stage == 'journal_before':
            # SQLite/VFS variants report IOERR_READ or CANTOPEN for this obstruction.
            assert observed['sqlite_errorcode'] & 255 in {sqlite3.SQLITE_IOERR, sqlite3.SQLITE_CANTOPEN}
        else:
            assert observed['controlled_commit_failure'] is True and observed['sql'] == 'COMMIT'
        result = app.client.get(f"/v1/runs/{run['id']}").json()
        assert result['status'] != 'succeeded' and result['verification'] != 'passed'
        assert app.client.get(f"/v1/runs/{run['id']}/receipts").json() == []
        denied = app.client.post('/v1/sessions')
        assert denied.status_code == 503 and denied.json()['error']['code'] == 'storage_failure'
        assert app.client.get('/healthz').json() == {'status': 'unavailable'}
        token = (app.root / 'token').read_text().strip()
        diagnosis = app.diagnostics()
        assert 'Runtime worker stopped' in diagnosis
        assert token not in diagnosis + denied.text
        artifact = app.root / 'workspace/answer.txt'
        assert artifact.exists() is after_effect
        identity = (artifact.stat().st_ino, artifact.stat().st_mtime_ns, digest(artifact)) if after_effect else None
        peer.take_request()
        assert peer.requests.empty()
        app.stop()
        integrity = audit(app.root)
        app.launcher = None
        for _ in range(2):
            app.start()
            recovered = app.client.get(f"/v1/runs/{run['id']}").json()
            assert recovered['status'] == ('uncertain' if after_effect else 'interrupted')
            assert recovered['verification'] != 'passed' and recovered['output'] is None
            receipts = app.client.get(f"/v1/runs/{run['id']}/receipts").json()
            assert [receipt['status'] for receipt in receipts] == (['uncertain'] if after_effect else [])
            replay = events(app, run['id'])
            assert sum(event['kind'] == 'run.finished' for event in replay) == 1
            assert not any(event['kind'] == 'tool.finished' and event['data'].get('status') == 'succeeded' for event in replay)
            if after_effect:
                assert (artifact.stat().st_ino, artifact.stat().st_mtime_ns, digest(artifact)) == identity
                response = app.client.post('/v1/runs', json=run['request'] | {'request_id': 'retry', 'retry_of': run['id']})
                assert response.status_code == 409 and response.json()['error']['code'] == 'invalid_retry'
            else:
                assert not artifact.exists()
            assert peer.requests.empty()
            assert app.client.post('/v1/sessions').status_code == 200
            app.stop()
            audit(app.root)
        evidence(record_property, stage, {'boundary': observed, 'audit': integrity,
                 'public_error': denied.json(), 'diagnosis': diagnosis,
                 'recovered_status': recovered['status'], 'receipts': receipts})
    finally:
        app.stop()
        peer.close()


def seed_schema(app, version):
    """Real migration statements, then only fields/tables present in that schema."""
    from hyperclaw.config import load_settings
    from hyperclaw.execution.workspace import Workspace
    from hyperclaw.execution.policy import Policy
    from hyperclaw.mcp import McpTools, collect, file_manifest
    workspace_path = app.root / 'workspace'
    workspace_path.mkdir(exist_ok=True)
    workspace = Workspace(workspace_path)
    workspace_id = workspace.identity
    workspace.close()
    artifact = workspace_path / 'historical.txt'
    artifact.write_text('recorded artifact')
    document = write_skill(app.root) if version >= 5 else None
    manifest = None
    if version >= 6:
        manager = McpTools(None, load_settings(root=app.root, environ={}), None)
        manifest, files = manager._preview()
        manager._snapshot(manifest, files)
        identity = {}
        assert file_manifest(collect(app.root / 'mcp-docs' / manifest['content_hash'],
                                     identities=identity, public_only=True)) == manifest['files']
        manifest['snapshot_identity'] = identity
    with closing(sqlite3.connect(app.root / 'runtime.sqlite3')) as db:
        db.execute('PRAGMA foreign_keys=ON')
        for index, (_, statements) in enumerate(MIGRATIONS[:version], 1):
            for statement in statements:
                db.execute(statement)
            db.execute('UPDATE schema_version SET version=?', (index,))
        requests = {}
        for name, status in [('completed', 'succeeded'), ('interrupted', 'interrupted'), ('active', 'running')]:
            db.execute('INSERT INTO sessions VALUES (?,3)', (name,))
            payload = {'session_id': name, 'generation': 3, 'request_id': name,
                       'text': f'{name} historical input', 'retry_of': None}
            if version >= 2:
                payload.update(tools=['workspace_write' if name == 'completed' else 'workspace_read'],
                               images=[], context_bytes=65536)
            requests[name] = payload
            db.execute('INSERT INTO runs(id,session_id,generation,request_id,payload_json,status,output,created_at) VALUES (?,?,?,?,?,?,?,?)',
                       (name, name, 3, name, canonical(payload), status, 'historical output' if name == 'completed' else None, STAMP))
            db.execute('INSERT INTO messages(run_id,role,content) VALUES (?,?,?)', (name, 'user', payload['text']))
            if name == 'completed':
                db.execute('INSERT INTO messages(run_id,role,content) VALUES (?,?,?)', (name, 'assistant', 'historical output'))
            for seq, kind, data in [(1, 'run.queued', {}), (2, 'run.started', {})]:
                db.execute('INSERT INTO events VALUES (?,?,?,?,?)', (name, seq, kind, STAMP, canonical(data)))
            if name != 'active':
                db.execute('INSERT INTO events VALUES (?,?,?,?,?)', (name, 3, 'run.finished', STAMP, canonical({'status': status})))
        receipt = None
        if version >= 2:
            artifact_value = {'path': 'historical.txt', 'sha256': digest(artifact), 'size_bytes': 17}
            observed = artifact_value | {'verified': True}
            receipt = {'invocation_id': 'historical-invocation', 'status': 'succeeded',
                       'output': json.dumps(observed), 'artifacts': [artifact_value],
                       'evidence': {'verification': 'passed', 'observed': observed}}
            call = ToolCall(id='historical-call', name='workspace_write', arguments={
                'path': 'historical.txt', 'content': 'recorded artifact'})
            decision = Policy(workspace_id, set()).check(call, ['workspace_write'])
            arguments_hash = hashlib.sha256(canonical(call.arguments).encode()).hexdigest()
            db.execute('INSERT INTO invocations(id,run_id,call_id,call_json,arguments_sha256,policy_sha256,workspace_id,capability,status,approved,receipt_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                       ('historical-invocation', 'completed', call.id, call.model_dump_json(), arguments_hash,
                        decision.sha256, workspace_id, 'write', 'succeeded', 1, canonical(receipt)))
            approval = Approval(id='historical-approval', invocation_id='historical-invocation',
                                run_id='completed', call=call, arguments_sha256=arguments_hash,
                                policy_sha256=decision.sha256, workspace_id=workspace_id,
                                expires_at='2026-01-02T00:00:00+00:00', status='pending')
            db.execute("INSERT INTO approvals VALUES (?,?,?,'approved')",
                       (approval.id, approval.invocation_id, approval.expires_at))
            db.execute("DELETE FROM events WHERE run_id='completed' AND seq=3")
            trail = [
                ('tool.requested', {'invocation_id': approval.invocation_id, 'call': call.model_dump(),
                 'arguments_sha256': arguments_hash, 'policy_sha256': decision.sha256,
                 'workspace_id': workspace_id, 'intent': 'write'}),
                ('approval.required', approval.model_dump()),
                ('approval.decided', {'approval_id': approval.id, 'approved': True}),
                ('run.queued', {'resumed_approval': approval.id}), ('run.started', {}),
                ('tool.started', {'invocation_id': approval.invocation_id}),
                ('tool.finished', receipt),
                ('run.finished', {'status': 'succeeded', 'verification': 'passed'}),
            ]
            for seq, (kind, data) in enumerate(trail, 3):
                db.execute('INSERT INTO events VALUES (?,?,?,?,?)', ('completed', seq, kind, STAMP, canonical(data)))
            db.execute("UPDATE runs SET verification='passed' WHERE id='completed'")
            db.execute('INSERT INTO artifacts VALUES (?,?,?,?)', ('historical-invocation', 'historical.txt', digest(artifact), 17))
            db.execute('INSERT INTO grants VALUES (?,?,?)', (workspace_id, 'write', STAMP))
        if version >= 3:
            db.execute("INSERT INTO schedules VALUES ('historical-schedule','completed',3,'completed historical input',?,NULL,?,?,'completed',NULL)",
                       (STAMP, '["workspace_write"]', canonical({'id': 'historical-schedule', 'session_id': 'completed',
                         'generation': 3, 'input': 'completed historical input', 'next_due_at': STAMP,
                         'interval_seconds': None, 'tools': ['workspace_write']})))
            db.execute("INSERT INTO schedule_occurrences VALUES ('historical-schedule',?,'completed')", (STAMP,))
        if version >= 4:
            for identifier, session, text, supersedes, revision, state in [
                ('old-memory', 'completed', 'Archive marker obsolete plum', None, 1, 'superseded'),
                ('private-memory', 'completed', 'Archive marker corrected cedar', 'old-memory', 2, 'active'),
                ('shared-memory', None, 'Shared marker maple', None, 1, 'active')]:
                db.execute('INSERT INTO memory_records VALUES (?,?,?,?,?,?,?,?,?,?)',
                           (identifier, workspace_id, session, text, 'completed', STAMP, None, supersedes, revision, state))
        if document:
            db.execute('INSERT INTO skill_versions VALUES (?,?,?,?)',
                       (document.name, document.content_hash, document.model_dump_json(), STAMP))
            db.execute('INSERT INTO skill_admissions VALUES (?,?,?)', (document.name, document.content_hash, STAMP))
        if manifest:
            db.execute('INSERT INTO mcp_versions VALUES (?,?,?)', (manifest['sha256'], canonical(manifest), STAMP))
            db.execute("INSERT INTO mcp_admission VALUES ('docs',?,'historical-admission')", (manifest['sha256'],))
        db.commit()
    return {'requests': requests, 'workspace_id': workspace_id, 'receipt': receipt,
            'skill_hash': document.content_hash if document else None,
            'mcp': dict(manifest, admission_id='historical-admission') if manifest else None}


def configured_app(tmp_path, peer, **kwargs):
    app = Process(tmp_path / 'runtime', peer.url, **kwargs)
    source = tmp_path / 'public-docs'
    source.mkdir()
    (source / 'guide.md').write_text('Synthetic documentation retained outside the runtime snapshot.')
    config = app.root / 'config.toml'
    text = config.read_text().replace('mcp_docs_path = ""', 'mcp_docs_path = ' + json.dumps(str(source)))
    config.write_text(text.replace('mcp_docs_image = ""', 'mcp_docs_image = "sha256:' + 'a' * 64 + '"'))
    return app


@pytest.mark.parametrize('version', range(1, 7))
def test_all_prior_v2_schemas_preserve_populated_state_through_daemon_reopens(tmp_path, record_property, version):
    peer = ProviderStub()
    app = configured_app(tmp_path, peer)
    seeded = seed_schema(app, version)
    original_hash = digest(app.root / 'runtime.sqlite3')
    original_dump = database_dump(app.root / 'runtime.sqlite3')
    backup_hashes = None
    baseline = None
    try:
        for opening in range(3):
            app.start()
            state = {}
            for name, request in seeded['requests'].items():
                run = app.client.get(f'/v1/runs/{name}').json()
                assert run['id'] == name and run['request']['generation'] == 3
                assert run['status'] == ('succeeded' if name == 'completed' else 'interrupted')
                assert run['request']['tools'] == ([] if version == 1 else
                    ['workspace_write' if name == 'completed' else 'workspace_read'])
                for key, value in request.items():
                    assert run['request'][key] == value
                assert app.client.post('/v1/runs', json=run['request']).json()['id'] == name
                replay = events(app, name)
                expected_kinds = ['run.queued', 'run.started', 'run.finished']
                if name == 'completed' and version >= 2:
                    expected_kinds = ['run.queued', 'run.started', 'tool.requested', 'approval.required',
                                      'approval.decided', 'run.queued', 'run.started', 'tool.started',
                                      'tool.finished', 'run.finished']
                assert [event['seq'] for event in replay] == list(range(1, len(expected_kinds) + 1))
                assert [event['kind'] for event in replay] == expected_kinds
                assert events(app, name, after=2) == replay[2:]
                state[name] = {'run': run, 'events': replay,
                               'receipts': app.client.get(f'/v1/runs/{name}/receipts').json()}
            assert app.client.get('/v1/sessions/completed').json()['generation'] == 3
            assert app.client.get('/v1/workspace').json()['grants'] == (['write'] if version >= 2 else [])
            if version >= 2:
                receipt = state['completed']['receipts'][0]
                for key, value in seeded['receipt'].items():
                    assert receipt[key] == value
            if version >= 3:
                occurrences = app.client.get('/v1/schedules/historical-schedule/occurrences').json()
                assert len(occurrences) == 1 and occurrences[0]['run_id'] == 'completed'
                state['schedule'] = app.client.get('/v1/schedules/historical-schedule').json()
                state['occurrences'] = occurrences
            if version >= 4:
                session = {'id': 'completed', 'generation': 3}
                current = search(app, peer, session, 'Archive')
                assert [record['id'] for record in current] == ['private-memory']
                assert current[0]['version'] == 2 and current[0]['supersedes'] == 'old-memory'
                assert search(app, peer, session, 'obsolete plum') == []
                assert search(app, peer, {'id': 'interrupted', 'generation': 3}, 'Archive') == []
                assert [record['id'] for record in search(app, peer, {'id': 'interrupted', 'generation': 3}, 'Shared')] == ['shared-memory']
            if version >= 5:
                assert app.client.get('/v1/skills').json()[0]['admitted'] is True
                assert app.client.get('/v1/skills').json()[0]['content_hash'] == seeded['skill_hash']
            if version >= 6:
                assert app.client.get('/v1/mcp').json()['admission'] == seeded['mcp']
            if baseline is None:
                baseline = state
            else:
                assert state == baseline
            assert peer.requests.empty() and not peer.errors
            app.stop()
            checked = audit(app.root)
            with closing(sqlite3.connect(app.root / 'runtime.sqlite3')) as db:
                assert db.execute('SELECT version FROM schema_version').fetchone() == (7,)
                if version >= 2:
                    assert db.execute('SELECT id,status FROM approvals').fetchall() == [('historical-approval', 'approved')]
            backups = list(app.root.glob('backup-*.sqlite3'))
            assert len(backups) == int(version == 1)
            for backup in backups:
                assert database_dump(backup) == original_dump
                with closing(sqlite3.connect(backup)) as db:
                    assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)
                    assert db.execute('SELECT version FROM schema_version').fetchone() == (1,)
            current_backup_hashes = {path.name: digest(path) for path in backups}
            if backup_hashes is None:
                backup_hashes = current_backup_hashes
            else:
                assert current_backup_hashes == backup_hashes
        evidence(record_property, f'schema_{version}', {'input_sha256': original_hash, 'audit': checked,
                 'reopens': 3, 'ids': list(seeded['requests']), 'backup_count': len(backups), 'backup_hashes': backup_hashes})
    finally:
        app.stop()
        peer.close()


def stopped_manifest(root):
    return {str(path.relative_to(root)): digest(path) for path in sorted(root.rglob('*')) if path.is_file()}


def restore_contents(snapshot, root):
    """Restore the complete file set while retaining existing directory/file identities."""
    for path in sorted(root.rglob('*'), key=lambda path: len(path.parts), reverse=True):
        if not (snapshot / path.relative_to(root)).exists():
            path.rmdir() if path.is_dir() else path.unlink()
    for source in sorted(snapshot.rglob('*')):
        target = root / source.relative_to(snapshot)
        if source.is_dir():
            target.mkdir(exist_ok=True)
        elif not target.exists() or digest(target) != digest(source):
            shutil.copy2(source, target)


async def seed_telegram(root):
    from hyperclaw.telegram import TelegramUpdate
    from tests.support.telegram_peer import update
    store = await Store.open(root)
    try:
        for identifier, delivery in [(71, 'sent'), (72, 'uncertain')]:
            payload = update() | {'update_id': identifier}
            normalized = TelegramUpdate.from_payload(payload)
            reserved = await store.telegram_reserve(123456, normalized, True)
            request = RunRequest(session_id=reserved['session_id'], generation=reserved['generation'],
                                 request_id=reserved['request_id'], text=normalized.text, tools=[])
            await store.telegram_prepare(123456, identifier, request)
            run = await store.submit(request)
            await store.next_run()
            await store.finish(run.id, 'succeeded', output='Synthetic delivered answer')
            assert await store.telegram_reconcile(123456, identifier) == run.id
            assert await store.telegram_begin_delivery(123456, identifier, 'terminal')
            await store.telegram_finish_delivery(123456, identifier, 'terminal', delivery,
                                                 message_id=700 if delivery == 'sent' else None)
        return await store.telegram_status()
    finally:
        await store.close()


@pytest.mark.parametrize('replace_identity', [False, True], ids=['retain-identities', 'replace-identities'])
def test_whole_stopped_root_restore_preserves_state_and_rejects_stale_authority(tmp_path, record_property, replace_identity):
    from tests.integration.test_memory import tool_reply
    from tests.support.telegram_peer import TOKEN, TelegramPeer, update
    peer, telegram_peer = ProviderStub(), TelegramPeer()
    app = configured_app(tmp_path, peer, launcher=LAUNCHER.with_name('telegram_daemon.py'))
    config = app.root / 'config.toml'
    config.write_text(config.read_text().replace('telegram_enabled = false', 'telegram_enabled = true')
                      .replace('telegram_allowed_pairs = []', 'telegram_allowed_pairs = ["-10:20"]'))
    (app.root / 'telegram-token').write_text(TOKEN)
    (app.root / 'telegram-token').chmod(0o600)
    (app.root / 'test-telegram-url').write_text(telegram_peer.url)
    (app.root / 'test-telegram-stage').write_text('normal')
    # The peer still holds both old updates: restored cursor and delivery claims
    # must prevent intake/effect/output replay through the enabled real adapter.
    telegram_peer.updates = [update(update_id=71), update(update_id=72)]
    seed_schema(app, 7)
    telegram = asyncio.run(seed_telegram(app.root))
    # Prepare an ordinary public approval before the public grant used below.
    with closing(sqlite3.connect(app.root / 'runtime.sqlite3')) as db:
        db.execute('DELETE FROM grants')
        db.commit()
    try:
        app.start()
        workspace = app.client.get('/v1/workspace').json()
        app.client.post('/v1/grants', json={'workspace_id': workspace['id'], 'capability': 'execute'}).raise_for_status()
        peer.enqueue(write_reply(), Reply(chunks=('Verified artifact complete.',)))
        completed = submit(app, 'Write answer.txt', tools=['workspace_write'])
        completed_approval = pending_approval(app)
        app.client.post(f"/v1/approvals/{completed_approval['id']}/decision", json={
            'approved': True, 'arguments_sha256': completed_approval['arguments_sha256'],
            'policy_sha256': completed_approval['policy_sha256']}).raise_for_status()
        assert events(app, completed['id'])[-1]['data'] == {'status': 'succeeded', 'verification': 'passed'}
        peer.take_request(); peer.take_request()
        peer.enqueue(tool_reply('workspace_write', {'path': 'pending.txt', 'content': 'pending approved effect'}))
        pending = submit(app, 'Write pending.txt', tools=['workspace_write'])
        approval = pending_approval(app)
        peer.take_request()
        receipts = app.client.get(f"/v1/runs/{completed['id']}/receipts").json()
        replay = events(app, completed['id'])
        session = {'id': 'completed', 'generation': 3}
        _, correction = memory_call(app, peer, session, 'memory_correct', {
            'record_id': 'private-memory', 'text': 'Archive marker restored birch'})
        corrected = json.loads(correction['output'])
        assert corrected['version'] == 3 and corrected['supersedes'] == 'private-memory'
        wait_for(lambda: any(method == 'getUpdates' and body['offset'] == 73
                             for method, body in telegram_peer.requests), app)
        telegram_public = app.client.get('/v1/telegram').json()
        assert telegram_public['enabled'] is True
        assert telegram_public['updates'] == telegram['updates']
        assert telegram_public['deliveries'] == telegram['deliveries']
        admission = app.client.get('/v1/mcp').json()['admission']
        app.stop()
        before = stopped_manifest(app.root)
        snapshot = tmp_path / 'whole-root-snapshot'
        shutil.copytree(app.root, snapshot)
        assert stopped_manifest(snapshot) == before
        audit(app.root)
        app.start()
        memory_call(app, peer, session, 'memory_forget', {'record_id': corrected['id']})
        assert app.client.delete('/v1/skills/documentation-answer/admission').status_code == 200
        assert app.client.delete('/v1/mcp/admission').status_code == 200
        assert app.client.post('/v1/sessions/completed/reset', json={'generation': 3}).json()['generation'] == 4
        app.stop()
        (app.root / 'workspace/answer.txt').write_text('working copy changed')
        (app.root / 'only-in-working-copy').write_text('remove on restore')
        if replace_identity:
            app.root.rename(tmp_path / 'discarded-working-root')
            shutil.copytree(snapshot, app.root)
        else:
            restore_contents(snapshot, app.root)
        assert stopped_manifest(app.root) == before
        evidence(record_property, 'snapshot_hashes', before)
        restored_polls = []
        for phase in range(2):
            request_boundary = len(telegram_peer.requests)
            app.start()
            wait_for(lambda: any(method == 'getUpdates' and body['offset'] == 73
                                 for method, body in telegram_peer.requests[request_boundary:]), app)
            restored_polls.append({'reopen': phase + 1, 'request_boundary': request_boundary,
                                   'offset': 73, 'request_index': next(
                                       index for index, (method, body) in enumerate(telegram_peer.requests)
                                       if index >= request_boundary and method == 'getUpdates' and body['offset'] == 73)})
            evidence(record_property, f'restored_telegram_poll_{phase + 1}', restored_polls[-1])
            assert app.client.get(f"/v1/runs/{completed['id']}/receipts").json() == receipts
            assert events(app, completed['id']) == replay
            assert app.client.get('/v1/sessions/completed').json()['generation'] == 3
            assert app.client.get('/v1/telegram').json() == telegram_public
            assert app.client.get('/v1/mcp').json()['admission'] == admission
            assert app.client.get('/v1/skills').json()[0]['admitted'] is True  # Content-bound, not inode-bound.
            selected = app.client.get('/v1/workspace').json()
            assert (selected['id'] == workspace['id']) is (not replace_identity)
            assert selected['grants'] == ([] if replace_identity else ['execute'])
            assert (app.root / 'workspace/answer.txt').read_text() == 'hello'
            assert not (app.root / 'workspace/pending.txt').exists()
            assert search(app, peer, session, 'Archive') == ([] if replace_identity else [corrected])
            assert search(app, peer, session, 'corrected cedar') == []
            other = {'id': 'interrupted', 'generation': 3}
            shared = search(app, peer, other, 'Shared')
            assert [record['id'] for record in shared] == ([] if replace_identity else ['shared-memory'])
            assert search(app, peer, other, 'Archive') == []
            assert app.client.get('/v1/schedules/historical-schedule/occurrences').json()[0]['run_id'] == 'completed'
            if replace_identity:
                refusal = app.client.post('/v1/grants', json={'workspace_id': workspace['id'], 'capability': 'write'})
                assert refusal.status_code == 409
                response = app.client.post('/v1/runs', json={'session_id': 'interrupted', 'generation': 3,
                    'request_id': 'stale-mcp', 'text': 'Read docs', 'tools': ['mcp_docs_read']})
                assert response.status_code == 409 and response.json()['error']['code'] == 'mcp_snapshot_changed'
            if not replace_identity:
                peer.enqueue(Reply())
                admitted_run = submit(app, 'Use restored admissions', request_id=uuid4().hex,
                                      tools=['mcp_docs_read'], skills=['documentation-answer'])
                assert events(app, admitted_run['id'])[-1]['data']['status'] == 'succeeded'
                wire = peer.take_request()
                assert [tool['name'] for tool in wire['tools']] == ['mcp_docs_read']
                assert 'Follow the documentation rules.' in wire['system']
            assert peer.requests.empty()
            app.stop()
            audit(app.root)
        # Pending checkpoints also bind the old workspace; operator approval cannot rebind it.
        app.start()
        if not replace_identity:
            peer.enqueue(Reply(chunks=('Approved pending write complete.',)))
        response = app.client.post(f"/v1/approvals/{approval['id']}/decision", json={
            'approved': True, 'arguments_sha256': approval['arguments_sha256'], 'policy_sha256': approval['policy_sha256']})
        if replace_identity:
            assert response.status_code == 409, response.text
            assert response.json()['error']['code'] == 'approval_changed'
            assert app.client.get(f"/v1/runs/{pending['id']}").json()['status'] == 'waiting_approval'
            assert not (app.root / 'workspace/pending.txt').exists()
            app.client.post(f"/v1/runs/{pending['id']}/cancel").raise_for_status()
        else:
            assert response.status_code == 200, response.text
            assert events(app, pending['id'])[-1]['data']['status'] == 'succeeded'
            assert (app.root / 'workspace/pending.txt').read_text() == 'pending approved effect'
            peer.take_request()
        assert peer.requests.empty() and not peer.errors
        app.stop()
        checked = audit(app.root)
        with closing(sqlite3.connect(app.root / 'runtime.sqlite3')) as db:
            assert db.execute('SELECT next_offset FROM telegram_cursors').fetchone() == (73,)
            assert db.execute("SELECT status FROM telegram_deliveries ORDER BY update_id").fetchall() == [('sent',), ('uncertain',)]
            assert db.execute("SELECT text FROM memory_fts WHERE memory_fts MATCH 'birch'").fetchall() == [('Archive marker restored birch',)]
            assert db.execute("SELECT text FROM memory_fts WHERE memory_fts MATCH 'cedar'").fetchall() == []
        assert not any(method == 'sendMessage' for method, _ in telegram_peer.requests)
        assert TOKEN not in app.diagnostics()
        evidence(record_property, 'restore_result', {'replace_identity': replace_identity,
                 'old_workspace': workspace['id'], 'restored_workspace': selected['id'],
                 'audit': checked, 'telegram_replayed': False,
                 'restored_telegram_polls': restored_polls,
                 'telegram_poll_offsets': [body['offset'] for method, body in telegram_peer.requests if method == 'getUpdates'],
                 'memory_visible': not replace_identity})
    finally:
        app.stop()
        peer.close()
        telegram_peer.close()


def startup_child(app, launcher=None):
    argv = [sys.executable, *([str(launcher)] if launcher else ['-m', 'hyperclaw']),
            '--root', str(app.root), 'serve', '--port', '0']
    log = (app.root.parent / 'startup-child.log').open('w+')
    child = subprocess.Popen(argv, cwd=app.root.parent, env={'PATH': os.environ['PATH']},
                             stdout=log, stderr=subprocess.STDOUT)
    return child, log


def test_migration_sigkill_rolls_back_and_preserves_usable_original_backup(tmp_path, record_property):
    peer = ProviderStub()
    app = configured_app(tmp_path, peer)
    seed_schema(app, 1)
    original = digest(app.root / 'runtime.sqlite3')
    original_dump = database_dump(app.root / 'runtime.sqlite3')
    (app.root / 'test-storage-stage').write_text('migration_interrupt')
    child, log = startup_child(app, LAUNCHER)
    try:
        deadline = time.monotonic() + 10
        while not (app.root / 'test-storage-observed.json').exists():
            assert child.poll() is None
            assert time.monotonic() < deadline, 'Migration boundary not reached'
            time.sleep(.01)
        observed = json.loads((app.root / 'test-storage-observed.json').read_text())
        assert observed['in_transaction'] is True and observed['sql'] == 'DROP TABLE events'
        child.kill(); child.wait(timeout=5)
        # First read rolls back the hot journal left by SIGKILL.
        checked = audit(app.root)
        assert checked['database_sha256'] == original
        backups = list(app.root.glob('backup-v1-*.sqlite3'))
        assert len(backups) == 1 and database_dump(backups[0]) == original_dump
        backup_hash = digest(backups[0])
        for _ in range(2):
            app.start()
            assert app.client.get('/v1/runs/completed').json()['output'] == 'historical output'
            assert app.client.get('/v1/runs/active').json()['status'] == 'interrupted'
            assert events(app, 'completed', after=2)[0]['seq'] == 3
            app.stop()
            audit(app.root)
        assert len(list(app.root.glob('backup-v1-*.sqlite3'))) == 2
        assert digest(backups[0]) == backup_hash
        # The original backup is itself an input for an ordinary daemon upgrade.
        backup_app = Process(tmp_path / 'backup-runtime', peer.url)
        shutil.copy2(backups[0], backup_app.root / 'runtime.sqlite3')
        try:
            backup_app.start()
            assert backup_app.client.get('/v1/runs/completed').json()['output'] == 'historical output'
        finally:
            backup_app.stop()
        assert peer.requests.empty()
        evidence(record_property, 'migration_interruption', {'boundary': observed, 'audit': checked,
                 'original_sha256': original, 'backup_sha256': digest(backups[0]), 'backup_startup': 'usable'})
    finally:
        if child.poll() is None:
            child.kill(); child.wait(timeout=5)
        log.close()
        app.stop()
        peer.close()


def test_newer_schema_refusal_preserves_whole_input_and_releases_lock(tmp_path, record_property):
    peer = ProviderStub()
    app = configured_app(tmp_path, peer)
    seed_schema(app, 7)
    with closing(sqlite3.connect(app.root / 'runtime.sqlite3')) as db:
        db.execute('UPDATE schema_version SET version=8')
        db.commit()
    # Creation of the lock file itself is legitimate setup, not database migration.
    (app.root / 'owner.lock').touch(mode=0o600)
    before = stopped_manifest(app.root)
    results = []
    try:
        for _ in range(2):
            child, log = startup_child(app)
            try:
                code = child.wait(timeout=10)
                log.seek(0); output = log.read()
                assert code != 0 and 'unsupported_schema' in output and 'root_in_use' not in output
                assert (app.root / 'token').read_text().strip() not in output
                assert stopped_manifest(app.root) == before
                results.append({'returncode': code, 'output': output})
            finally:
                if child.poll() is None:
                    child.kill(); child.wait(timeout=5)
                log.close()
        evidence(record_property, 'newer_schema_refusal', {'input_hashes': before, 'attempts': results})
    finally:
        peer.close()
