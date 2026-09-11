"""One owned daemon composes all intake paths across policy and crash boundaries.

These cases catch per-channel workers, lost/duplicated committed intake, replayed
side effects, stale authority, and releasing root ownership before owned IO closes.
External peers are synthetic loopback HTTP; all runtime state belongs to tmp_path.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import signal
import sqlite3
import threading

import httpx
import pytest

from tests.integration.test_background import committed_prefix
from tests.integration.test_cli_tools import pending_approval, write_reply
from tests.integration.test_recovery import grant_write, wait_for
from tests.integration.test_scheduling import schedule_body
from tests.integration.test_skills import write_skill
from tests.integration.test_telegram import process_stack  # Shared owned-process fixture.
from tests.support.process import events, submit
from tests.support.provider import Reply
from tests.support.telegram_peer import update
from tests.support.tool_provider import frames, message_end, message_start, tool_block


def telegram_row(app, update_id=1, *, bound=True):
    rows = app.client.get('/v1/telegram').json()['updates']
    return next((row for row in rows if row['update_id'] == update_id
                 and (not bound or row['run_id'])), None)


def delivered(app, count=1):
    rows = app.client.get('/v1/telegram').json()['deliveries']
    return rows if len(rows) == count and all(row['status'] == 'sent' for row in rows) else None


def due_schedule(app, identifier='mixed-schedule'):
    session = app.client.post('/v1/sessions').json()
    body = schedule_body(session, identifier, interval_seconds=None,
                         due=datetime.now(timezone.utc) - timedelta(seconds=1))
    response = app.client.post('/v1/schedules', json=body)
    assert response.status_code == 200, response.text
    return wait_for(lambda: app.client.get(f'/v1/schedules/{identifier}/occurrences').json(), app)[0]


def assert_serial(replays):
    # Compare durable execution intervals, without specifying inter-session order.
    intervals = sorted((next(e['at'] for e in replay if e['kind'] == 'run.started'),
                        replay[-1]['at']) for replay in replays)
    assert all(end <= start for (_, end), (start, _) in zip(intervals, intervals[1:]))
    for replay in replays:
        assert sum(e['kind'] == 'run.queued' for e in replay) == 1
        assert sum(e['kind'] == 'run.finished' for e in replay) == 1


@pytest.mark.parametrize('completion', ['release', 'cancel'])
def test_http_telegram_and_schedule_share_one_worker(process_stack, completion):
    app, telegram, provider = process_stack
    gate, disconnected = threading.Event(), threading.Event()
    app.start()
    provider.enqueue(Reply(gate=gate, disconnected=disconnected), Reply(), Reply())
    active = submit(app, 'HTTP owns the worker', tools=[])
    prefix = committed_prefix(app, active['id'])
    telegram.updates = [update(text='Telegram accepted behind HTTP')]
    row = wait_for(lambda: telegram_row(app), app)
    occurrence = due_schedule(app)
    identifiers = [active['id'], row['run_id'], occurrence['run_id']]
    states = [app.client.get(f'/v1/runs/{identifier}').json() for identifier in identifiers]
    assert [r['status'] for r in states] == ['running', 'queued', 'queued']
    assert provider.requests.qsize() == 1
    for run in states:
        duplicate = app.client.post('/v1/runs', json=run['request'])
        if run['request']['request_id'].startswith('schedule:'):
            assert duplicate.status_code == 422  # Reserved scheduler namespace.
        else:
            assert duplicate.status_code == 202 and duplicate.json()['id'] == run['id']
        assert len(app.client.get(f"/v1/sessions/{run['request']['session_id']}/runs").json()) == 1
    if completion == 'cancel':
        cancelled = app.client.post(f"/v1/runs/{active['id']}/cancel")
        assert cancelled.status_code == 200 and cancelled.json()['status'] == 'cancelled'
        assert disconnected.wait(2), 'Operator cancellation left actual model IO open'
        assert not gate.is_set(), 'Cancellation must disconnect before fixture release'
    else:
        gate.set()
    replays = [events(app, identifier) for identifier in identifiers]
    assert replays[0][:len(prefix)] == prefix
    if completion == 'cancel':
        assert [replay[-1]['data']['status'] for replay in replays] == [
            'cancelled', 'succeeded', 'succeeded']
    else:
        assert all(replay[-1]['data']['status'] == 'succeeded' for replay in replays)
    assert_serial(replays)
    assert app.client.get('/v1/schedules/mixed-schedule/occurrences').json() == [occurrence]
    assert app.client.get('/v1/telegram').json()['updates'] == [row]
    wait_for(lambda: delivered(app), app)
    assert sum(method == 'sendMessage' for method, _ in telegram.requests) == 1
    assert provider.requests.qsize() == 3 and not provider.errors
    if completion == 'cancel':
        repeated = app.client.post(f"/v1/runs/{active['id']}/cancel")
        assert repeated.status_code == 200 and repeated.json()['status'] == 'cancelled'
        app.restart()
        assert [events(app, identifier) for identifier in identifiers] == replays
        assert app.client.get('/v1/schedules/mixed-schedule/occurrences').json() == [occurrence]
        assert app.client.get('/v1/telegram').json()['updates'] == [row]
        assert delivered(app)
        assert sum(method == 'sendMessage' for method, _ in telegram.requests) == 1
        assert provider.requests.qsize() == 3 and not provider.errors
    assert all(app.client.get(f'/v1/runs/{identifier}/receipts').json() == []
               for identifier in identifiers)
    assert app.client.get('/v1/workspace').json()['grants'] == []
    assert list((app.root / 'workspace').iterdir()) == []


@pytest.mark.parametrize('approved', [False, True], ids=['deny-exact', 'approve-exact'])
def test_pending_approval_does_not_block_other_channels(process_stack, approved):
    app, telegram, provider = process_stack
    (app.root / 'test-telegram-stage').write_text('observe-effects')
    app.start()
    provider.enqueue(write_reply())
    waiting = submit(app, 'Write answer only with exact operator approval')
    approval = pending_approval(app)
    assert approval['arguments_sha256'] == hashlib.sha256(
        b'{"content":"hello","path":"answer.txt"}').hexdigest()
    provider.enqueue(Reply(), Reply())
    telegram.updates = [update(text='Telegram during pending approval')]
    row = wait_for(lambda: telegram_row(app), app)
    occurrence = due_schedule(app)
    for identifier in (row['run_id'], occurrence['run_id']):
        assert events(app, identifier)[-1]['data']['status'] == 'succeeded'
    wait_for(lambda: delivered(app), app)
    assert app.client.get(f"/v1/runs/{waiting['id']}").json()['status'] == 'waiting_approval'
    path = app.root / 'workspace/answer.txt'
    assert not path.exists()
    assert app.client.get('/v1/workspace').json()['grants'] == []
    decision = {'approved': approved, 'arguments_sha256': approval['arguments_sha256'],
                'policy_sha256': approval['policy_sha256']}
    changed = app.client.post(f"/v1/approvals/{approval['id']}/decision",
                              json=decision | {'arguments_sha256': '0' * 64})
    assert changed.status_code == 409 and changed.json()['error']['code'] == 'approval_changed'
    assert app.client.get('/v1/approvals').json() == [approval]
    if approved:
        provider.enqueue(Reply())
    response = app.client.post(f"/v1/approvals/{approval['id']}/decision", json=decision)
    assert response.status_code == 200, response.text
    replay = events(app, waiting['id'])
    assert replay[-1]['data']['status'] == ('succeeded' if approved else 'failed')
    receipts = app.client.get(f"/v1/runs/{waiting['id']}/receipts").json()
    assert len(receipts) == (1 if approved else 0)
    if approved:
        assert receipts[0]['status'] == 'succeeded'
    assert path.exists() == approved
    if approved:
        assert path.read_text() == 'hello'
        assert receipts[0]['artifacts'][0]['sha256'] == hashlib.sha256(b'hello').hexdigest()
        path.write_text('operator edit after exact invocation')
    else:
        assert response.json()['error']['code'] == 'approval_denied'
    duplicate = app.client.post(f"/v1/approvals/{approval['id']}/decision", json=decision)
    assert duplicate.status_code == 409
    assert app.client.get('/v1/workspace').json()['grants'] == []
    app.restart()
    assert app.client.get(f"/v1/runs/{waiting['id']}/receipts").json() == receipts
    assert events(app, waiting['id']) == replay
    assert path.read_text() == 'operator edit after exact invocation' if approved else not path.exists()
    observed = app.root / 'test-observed-writes'
    assert observed.read_text().splitlines() == ['answer.txt'] if approved else not observed.exists()
    assert provider.requests.qsize() == (4 if approved else 3)
    assert sum(method == 'sendMessage' for method, _ in telegram.requests) == 1
    assert not provider.errors


def effect_reply():
    return Reply(frames=frames(message_start(), *tool_block(
        0, 'uncertain-write', 'workspace_write',
        ('{"path":"uncertain.txt","content":"real effect before kill"}',)), *message_end()))


@pytest.mark.parametrize('attempt', [1, 2], ids=['running-stream', 'unreceipted-effect'])
@pytest.mark.parametrize('boundary', ['http', 'schedule', 'telegram-submit', 'telegram-bind'])
def test_mixed_intake_survives_process_death(process_stack, boundary, attempt):
    app, telegram, provider = process_stack
    (app.root / 'test-telegram-stage').write_text('composition-' + boundary)
    app.start()
    grant_write(app)
    provider.enqueue(write_reply(), Reply())
    completed = submit(app, 'completed effect must never replay')
    completed_events = events(app, completed['id'])
    completed_receipts = app.client.get(f"/v1/runs/{completed['id']}/receipts").json()
    assert completed_events[-1]['data']['status'] == 'succeeded'
    assert len(completed_receipts) == 1 and completed_receipts[0]['status'] == 'succeeded'
    assert (app.root / 'workspace/answer.txt').read_text() == 'hello'
    (app.root / 'test-composition-arm').touch()
    disconnected = threading.Event()
    if attempt == 1:
        provider.enqueue(Reply(gate=threading.Event(), disconnected=disconnected))
    else:
        (app.root / 'test-composition-hold-effect').touch()
        provider.enqueue(effect_reply())
    active = submit(app, 'active at process death')
    if attempt == 1:
        prefix = committed_prefix(app, active['id'])
    else:
        wait_for(lambda: (app.root / 'test-composition-effect').exists(), app)
        assert (app.root / 'workspace/uncertain.txt').read_text() == 'real effect before kill'
        assert app.client.get(f"/v1/runs/{active['id']}/receipts").json() == []
    queued = submit(app, 'durable HTTP queue', request_id='already-queued', tools=[])
    session = app.client.post('/v1/sessions').json()
    request = {'session_id': session['id'], 'generation': 0, 'request_id': 'mixed-http',
               'text': 'canonical HTTP submission', 'tools': []}
    # HTTP's response is deliberately lost after acceptance at its gate.
    def post_http():
        try:
            with httpx.Client(base_url=app.url, headers=dict(app.client.headers),
                              trust_env=False, timeout=5) as client:
                return client.post('/v1/runs', json=request)
        except httpx.TransportError:
            return None
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(post_http)
        if boundary == 'http':
            wait_for(lambda: (app.root / 'test-composition-intake').exists(), app)
        else:
            assert pending.result(timeout=5).status_code == 202
        occurrence = due_schedule(app)
        telegram.updates = [update(text='canonical Telegram update')]
        wait_for(lambda: telegram_row(app, bound=boundary != 'telegram-submit'), app)
        wait_for(lambda: (app.root / 'test-composition-intake').exists(), app)
        with sqlite3.connect(app.root / 'runtime.sqlite3') as db:
            assert db.execute('SELECT count(*) FROM runs').fetchone()[0] == 6
            assert db.execute('SELECT count(*) FROM schedule_occurrences').fetchone()[0] == 1
            assert db.execute('SELECT count(*) FROM telegram_updates').fetchone()[0] == 1
            telegram_state = db.execute('SELECT status,run_id FROM telegram_updates').fetchone()
            assert telegram_state[0] == ('prepared' if boundary == 'telegram-submit' else 'accepted')
            assert (telegram_state[1] is None) == (boundary == 'telegram-submit')
            assert db.execute(
                "SELECT count(*) FROM runs WHERE status='queued'").fetchone()[0] == 4
            saved_requests = [json.loads(row[0]) for row in db.execute('SELECT payload_json FROM runs')]
        app.kill()
        lost_response = pending.result(timeout=5)
        assert lost_response is None if boundary == 'http' else lost_response.status_code == 202
    if attempt == 1:
        assert disconnected.wait(2), 'SIGKILL left fixture model connection open'
    (app.root / 'test-composition-arm').unlink()
    (app.root / 'test-composition-hold-effect').unlink(missing_ok=True)
    (app.root / 'workspace/answer.txt').write_text('operator preserved completed effect')
    (app.root / 'workspace/uncertain.txt').write_text('operator preserved uncertain effect')
    provider.enqueue(Reply(), Reply(), Reply(), Reply())
    # Redeliver the exact envelope even after its offset committed, like a duplicate
    # transport batch; the journal must suppress both reexecution and resending.
    telegram.responses['getUpdates'] = {'ok': True, 'result': list(telegram.updates)}
    snapshots = None
    for reopen in range(2):
        app.start()
        row = wait_for(lambda: telegram_row(app), app)
        wait_for(lambda: delivered(app), app)
        assert app.client.get('/v1/telegram').json()['error'] is None
        assert app.client.get('/v1/schedules/mixed-schedule/occurrences').json() == [occurrence]
        terminal = app.client.get(f"/v1/runs/{active['id']}").json()
        assert terminal['status'] == ('interrupted' if attempt == 1 else 'uncertain')
        replay = events(app, active['id'])
        if attempt == 1:
            assert replay[:len(prefix)] == prefix
        receipts = app.client.get(f"/v1/runs/{active['id']}/receipts").json()
        assert len(receipts) == (0 if attempt == 1 else 1)
        if receipts:
            assert receipts[0]['status'] == 'uncertain'
            retry = app.client.post('/v1/runs', json=active['request'] | {
                'request_id': 'explicit-retry', 'retry_of': active['id']})
            assert retry.status_code == 409
        assert events(app, queued['id'])[-1]['data']['status'] == 'succeeded'
        for canonical in saved_requests:
            duplicate = app.client.post('/v1/runs', json=canonical)
            assert duplicate.status_code == (422 if canonical['request_id'].startswith('schedule:') else 202), duplicate.text
            runs = app.client.get(f"/v1/sessions/{canonical['session_id']}/runs").json()
            assert len(runs) == 1
            if duplicate.status_code == 202:
                assert duplicate.json()['id'] == runs[0]['id']
            history = events(app, runs[0]['id'])
            assert history[-1]['data']['status'] == (terminal['status'] if runs[0]['id'] == active['id'] else 'succeeded')
            assert sum(e['kind'] == 'run.queued' for e in history) == 1
            assert sum(e['kind'] == 'run.finished' for e in history) == 1
        assert events(app, completed['id']) == completed_events
        assert app.client.get(f"/v1/runs/{completed['id']}/receipts").json() == completed_receipts
        assert (app.root / 'workspace/answer.txt').read_text() == 'operator preserved completed effect'
        assert (app.root / 'workspace/uncertain.txt').read_text() == 'operator preserved uncertain effect'
        current = (replay, receipts, app.client.get('/v1/telegram').json()['deliveries'], row)
        if reopen:
            assert current == snapshots
        snapshots = current
        app.stop()
    assert (app.root / 'test-observed-writes').read_text().splitlines() == (
        ['answer.txt'] if attempt == 1 else ['answer.txt', 'uncertain.txt'])
    assert provider.requests.qsize() == 7
    assert sum(method == 'sendMessage' for method, _ in telegram.requests) == 1
    assert not provider.errors


def test_reset_and_revocation_while_other_channels_are_busy(process_stack):
    app, telegram, provider = process_stack
    source = app.root.parent / 'public-docs'
    source.mkdir()
    (source / 'guide.md').write_text('Synthetic documentation.')
    config = app.root / 'config.toml'
    config.write_text(config.read_text().replace('mcp_docs_path = ""',
        'mcp_docs_path = ' + json.dumps(str(source))).replace('mcp_docs_image = ""',
        'mcp_docs_image = "sha256:' + 'a' * 64 + '"'))
    document = write_skill(app.root)
    app.start()
    assert app.client.post('/v1/skills/documentation-answer/admit',
                           json={'content_hash': document.content_hash}).status_code == 200
    preview = app.client.get('/v1/mcp')
    assert preview.status_code == 200, preview.text
    manifest = preview.json()
    assert app.client.post('/v1/mcp/admit', json={'expected_sha256': manifest['sha256']}).status_code == 200
    gate, disconnected = threading.Event(), threading.Event()
    provider.enqueue(Reply(gate=gate, disconnected=disconnected))
    active = submit(app, 'busy while authority changes', tools=[])
    committed_prefix(app, active['id'])
    queued_skill = submit(app, 'queued selected skill', tools=[], skills=['documentation-answer'])
    queued_mcp = submit(app, 'queued admitted MCP', tools=['mcp_docs_read'])
    telegram.updates = [update(text='queued before pair revocation')]
    accepted = wait_for(lambda: telegram_row(app), app)
    occurrence = due_schedule(app)
    # A photo pauses in actual getFile HTTP IO before its download can start.
    telegram.gates['getFile'] = threading.Event()
    telegram.updates.append(update(2, topic=8, photo=[{'file_id': 'abc', 'width': 1, 'height': 1}]))
    wait_for(lambda: any(method == 'getFile' for method, _ in telegram.requests), app)
    for run in (active, queued_skill, queued_mcp):
        response = app.client.post(f"/v1/sessions/{run['request']['session_id']}/reset", json={'generation': 0})
        assert response.status_code == 409 and response.json()['error']['code'] == 'session_busy'
    idle = app.client.post('/v1/sessions').json()
    assert app.client.post(f"/v1/sessions/{idle['id']}/reset", json={'generation': 0}).status_code == 200
    stale = app.client.post('/v1/runs', json={'session_id': idle['id'], 'generation': 0,
        'request_id': 'stale', 'text': 'must conflict', 'tools': []})
    assert stale.status_code == 409 and stale.json()['error']['code'] == 'stale_generation'
    assert app.client.delete('/v1/skills/documentation-answer/admission').status_code == 200
    assert app.client.delete('/v1/mcp/admission').status_code == 200
    for selection, error in (({'skills': ['documentation-answer'], 'tools': []}, 'skill_not_admitted'),
                             ({'tools': ['mcp_docs_read']}, 'mcp_not_admitted')):
        session = app.client.post('/v1/sessions').json()
        response = app.client.post('/v1/runs', json={'session_id': session['id'], 'generation': 0,
            'request_id': 'revoked', 'text': 'must not execute', **selection})
        assert response.status_code == 409 and response.json()['error']['code'] == error
        assert app.client.get(f"/v1/sessions/{session['id']}/runs").json() == []
    unsupported = app.client.post('/v1/schedules', json=schedule_body(idle, 'unsupported-mcp', tools=['mcp_docs_read']))
    assert unsupported.status_code == 422 and unsupported.json()['error']['code'] == 'mcp_schedule_unsupported'
    assert len(app.client.get('/v1/schedules').json()) == 1
    assert app.client.get('/v1/workspace').json()['grants'] == []
    assert app.client.get('/v1/skills').json()[0]['admitted'] is False
    assert provider.requests.qsize() == 1
    assert not any(method in {'download', 'sendMessage'} for method, _ in telegram.requests)
    # Pair configuration is startup-only: revoke on disk and reopen the owned daemon.
    app.kill()
    assert disconnected.wait(2)
    config.write_text(config.read_text().replace('["-10:20"]', '["-11:20"]'))
    telegram.gates['getFile'].set()
    provider.enqueue(Reply(), Reply())
    app.start()
    for run, error in ((queued_skill, 'skill_not_admitted'), (queued_mcp, 'mcp_not_admitted')):
        assert events(app, run['id'])[-1]['data']['status'] == 'failed'
        assert app.client.get(f"/v1/runs/{run['id']}").json()['error']['code'] == error
        assert app.client.get(f"/v1/runs/{run['id']}/receipts").json() == []
    assert events(app, accepted['run_id'])[-1]['data']['status'] == 'succeeded'
    assert events(app, occurrence['run_id'])[-1]['data']['status'] == 'succeeded'
    def revoked_delivery():
        rows = app.client.get('/v1/telegram').json()['deliveries']
        return rows if rows and rows[0]['status'] == 'failed' else None
    rows = wait_for(revoked_delivery, app)
    assert rows[0]['error'] == 'telegram_authorization_revoked'
    photo = wait_for(lambda: telegram_row(app, 2, bound=False), app)
    assert photo['status'] == 'rejected' and photo['error'] == 'telegram_authorization_revoked'
    assert photo['run_id'] is None
    assert sum(method == 'getFile' for method, _ in telegram.requests) == 1
    assert not any(method in {'download', 'sendMessage'} for method, _ in telegram.requests)
    assert provider.requests.qsize() == 3 and not provider.errors
    assert app.client.get('/v1/workspace').json()['grants'] == []
    assert list((app.root / 'workspace').iterdir()) == []


def test_graceful_shutdown_settles_mixed_io_before_releasing_root(process_stack):
    app, telegram, provider = process_stack
    (app.root / 'test-telegram-stage').write_text('shutdown')
    app.start()
    telegram.send_gate.clear()
    provider.enqueue(Reply())
    telegram.updates = [update(text='delivery held in local HTTP')]
    assert telegram.send_observed.wait(5)
    gate, disconnected = threading.Event(), threading.Event()
    provider.enqueue(Reply(gate=gate, disconnected=disconnected))
    active = submit(app, 'stream held while shutdown starts', tools=[])
    committed_prefix(app, active['id'])
    telegram.gates['getFile'] = threading.Event()
    telegram.updates.append(update(2, topic=8, photo=[{'file_id': 'abc', 'width': 1, 'height': 1}]))
    wait_for(lambda: any(method == 'getFile' for method, _ in telegram.requests), app)
    child = app.process
    try:
        child.terminate()
        wait_for(lambda: (app.root / 'test-cleanup-entered').exists(), app)
        assert child.poll() is None
        assert not (app.root / 'daemon.json').exists()
        with (app.root / 'owner.lock').open('r+') as ownership:
            with pytest.raises(BlockingIOError):
                fcntl.flock(ownership, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert not (app.root / 'test-cleanup-complete').exists()
        assert not disconnected.is_set()
        (app.root / 'test-cleanup-release').touch()
        # Wait for the first SIGTERM to finish; never rescue shutdown with another signal.
        child.wait(timeout=5)
        app.stop()  # Reaps logs/client; its watchdog also fails if intervention was needed.
        assert child.returncode in (0, -signal.SIGTERM)
        assert disconnected.wait(2)
        assert (app.root / 'test-cleanup-complete').exists()
        with (app.root / 'owner.lock').open('r+') as ownership:
            fcntl.flock(ownership, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(ownership, fcntl.LOCK_UN)
        with sqlite3.connect(app.root / 'runtime.sqlite3') as db:
            assert db.execute('SELECT status FROM runs WHERE id=?', (active['id'],)).fetchone()[0] == 'interrupted'
            assert db.execute("SELECT count(*) FROM events WHERE run_id=? AND kind='run.finished'", (active['id'],)).fetchone()[0] == 1
        assert sum(method == 'sendMessage' for method, _ in telegram.requests) == 1
        assert sum(method == 'getFile' for method, _ in telegram.requests) == 1
        assert not any(method == 'download' for method, _ in telegram.requests)
        assert provider.requests.qsize() == 2 and not provider.errors
    finally:
        (app.root / 'test-cleanup-release').touch()
        telegram.send_gate.set()
        telegram.gates['getFile'].set()
