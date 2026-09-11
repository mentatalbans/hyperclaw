"""Public scheduling controls and daemon-owned reservation/recovery."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
import threading
import time

import httpx
import pytest

from hyperclaw.contracts import ScheduleRequest
from hyperclaw.store import Store
from tests.integration.test_cli_tools import cli, pending_approval, write_reply
from tests.integration.test_recovery import wait_for
from tests.support.process import Process, events, submit
from tests.support.provider import ProviderStub, Reply


RECOVERY_LAUNCHER = Path(__file__).resolve().parents[1] / 'support/recovery_daemon.py'
UTC = timezone.utc


@pytest.fixture
def service(tmp_path):
    peer = ProviderStub()
    app = Process(tmp_path / 'runtime', peer.url, timeout=15)
    try:
        app.start()
        yield app, peer
    finally:
        app.stop()
        peer.close()


def schedule_body(session, schedule_id='scheduled', *, due=None, interval_seconds=60,
                  text='scheduled work', tools=()):
    return {
        'id': schedule_id,
        'session_id': session['id'],
        'generation': session['generation'],
        'input': text,
        'next_due_at': (due or datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        'interval_seconds': interval_seconds,
        'tools': list(tools),
    }


def test_http_schedule_controls_are_authenticated_and_use_generation_cas(service):
    app, peer = service
    session = app.client.post('/v1/sessions').json()
    body = schedule_body(session, 'public-controls', tools=('workspace_read',))

    with httpx.Client(base_url=app.url, trust_env=False) as stranger:
        assert stranger.post('/v1/schedules', json=body).status_code == 401
        for path in ('/v1/schedules', '/v1/schedules/public-controls',
                     '/v1/schedules/public-controls/occurrences'):
            assert stranger.get(path).status_code == 401
        for path in ('/v1/schedules/public-controls/pause',
                     '/v1/schedules/public-controls/retarget'):
            assert stranger.post(path, json={}).status_code == 401

    created = app.client.post('/v1/schedules', json=body)
    assert created.status_code == 200, created.text
    schedule = created.json()
    assert schedule['id'] == 'public-controls'
    assert schedule['status'] == 'active'
    assert schedule['tools'] == ['workspace_read']
    assert app.client.get('/v1/schedules').json() == [schedule]
    assert app.client.get('/v1/schedules/public-controls').json() == schedule
    assert app.client.get('/v1/schedules/public-controls/occurrences').json() == []

    paused = app.client.post('/v1/schedules/public-controls/pause').json()
    assert paused['status'] == 'paused' and paused['pause_reason'] == 'operator'
    reset = app.client.post(f"/v1/sessions/{session['id']}/reset", json={'generation': 0}).json()
    stale = app.client.post('/v1/schedules/public-controls/retarget', json={
        'expected_generation': 1,
        'generation': reset['generation'],
    })
    assert stale.status_code == 409 and stale.json()['error']['code'] == 'stale_generation'
    retargeted = app.client.post('/v1/schedules/public-controls/retarget', json={
        'expected_generation': 0,
        'generation': reset['generation'],
    })
    assert retargeted.status_code == 200
    assert retargeted.json()['generation'] == 1
    assert retargeted.json()['status'] == 'active'
    assert peer.requests.empty()


def test_public_schedule_ids_are_always_addressable_as_one_path_segment(service):
    app, peer = service
    session = app.client.post('/v1/sessions').json()
    for invalid_id in ('nested/schedule', 'control\ncharacter', 'percent%encoded', '.', '..'):
        response = app.client.post('/v1/schedules', json=schedule_body(session, invalid_id))
        assert response.status_code == 422
        assert response.json()['error']['code'] == 'invalid_request'

    longest_id = 's' * 256
    created = app.client.post('/v1/schedules', json=schedule_body(session, longest_id))
    assert created.status_code == 200, created.text
    assert app.client.get('/v1/schedules/' + longest_id).json()['id'] == longest_id
    assert peer.requests.empty()


def test_maintenance_reserves_other_session_while_worker_busy_then_runs_approved_tool(service):
    app, peer = service
    gate = threading.Event()
    disconnected = threading.Event()
    peer.enqueue(Reply(gate=gate, disconnected=disconnected), write_reply(), Reply(chunks=('Done.',)))
    active = submit(app, 'occupy the only worker', tools=[])
    wait_for(lambda: app.client.get(f"/v1/runs/{active['id']}").json()['status'] == 'running', app)
    assert peer.take_request()['messages'][-1]['content'] == 'occupy the only worker'

    scheduled_session = app.client.post('/v1/sessions').json()
    created = app.client.post('/v1/schedules', json=schedule_body(
        scheduled_session,
        'approved-tool',
        due=datetime.now(UTC),
        interval_seconds=None,
        text='write once after exact approval',
        tools=('workspace_write',),
    ))
    assert created.status_code == 200, created.text

    def reserved():
        response = app.client.get('/v1/schedules/approved-tool/occurrences')
        if response.status_code == 200 and response.json():
            return response.json()[0]
        return None

    occurrence = wait_for(reserved, app, seconds=5)
    scheduled_run = app.client.get(f"/v1/runs/{occurrence['run_id']}").json()
    assert scheduled_run['status'] == 'queued'
    assert app.client.get(f"/v1/runs/{active['id']}").json()['status'] == 'running'
    assert peer.requests.empty()

    assert app.client.post(f"/v1/runs/{active['id']}/cancel").json()['status'] == 'cancelled'
    assert disconnected.wait(2)
    approval = pending_approval(app)
    decision = {
        'approved': True,
        'arguments_sha256': approval['arguments_sha256'],
        'policy_sha256': approval['policy_sha256'],
    }
    assert app.client.post(f"/v1/approvals/{approval['id']}/decision", json=decision).status_code == 200
    scheduled_events = events(app, occurrence['run_id'])
    assert scheduled_events[0]['data']['schedule_id'] == 'approved-tool'
    assert datetime.fromisoformat(scheduled_events[0]['data']['nominal_due_at']) == datetime.fromisoformat(
        occurrence['nominal_due_at'])
    assert scheduled_events[-1]['data'] == {
        'status': 'succeeded',
        'verification': 'passed',
    }
    active_finished = events(app, active['id'])[-1]
    scheduled_started = next(event for event in scheduled_events if event['kind'] == 'run.started')
    assert datetime.fromisoformat(active_finished['at']) <= datetime.fromisoformat(scheduled_started['at'])
    assert (app.root / 'workspace/answer.txt').read_text() == 'hello'
    requested = peer.take_request()
    assert requested['messages'][-1]['content'] == 'write once after exact approval'
    continued = peer.take_request()
    assert continued['messages'][-1]['content'][0]['tool_use_id'] == 'write-1'
    assert peer.requests.empty()
    assert not peer.errors


def test_cli_schedule_commands_use_daemon_http_and_fetch_current_generations(service):
    app, peer = service
    session = app.client.post('/v1/sessions').json()
    due = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    help_result = cli(app, 'schedule', 'create', '--help')
    assert help_result.returncode == 0, help_result.stderr
    assert re.search(r'schedule create \[OPTIONS\]\s+\{ID\}\s+\{INPUT\}', help_result.stdout)

    created = cli(app, 'schedule', 'create', 'cli-schedule', 'CLI scheduled input',
                  '--session', session['id'], '--due', due, '--interval-seconds', '60',
                  '--tool', 'workspace_read')
    assert created.returncode == 0, created.stderr
    schedule = json.loads(created.stdout)
    assert schedule['generation'] == 0
    assert schedule['tools'] == ['workspace_read']
    assert json.loads(cli(app, 'schedule', 'list').stdout) == [schedule]
    assert json.loads(cli(app, 'schedule', 'inspect', 'cli-schedule').stdout) == schedule
    assert json.loads(cli(app, 'schedule', 'occurrences', 'cli-schedule').stdout) == []

    paused = cli(app, 'schedule', 'pause', 'cli-schedule')
    assert paused.returncode == 0, paused.stderr
    assert json.loads(paused.stdout)['pause_reason'] == 'operator'
    reset = app.client.post(f"/v1/sessions/{session['id']}/reset", json={'generation': 0})
    assert reset.status_code == 200 and reset.json()['generation'] == 1
    stale = cli(app, 'schedule', 'retarget', 'cli-schedule', '--expected-generation', '1')
    assert stale.returncode != 0 and 'stale_generation' in stale.stderr
    retargeted = cli(app, 'schedule', 'retarget', 'cli-schedule', '--expected-generation', '0')
    assert retargeted.returncode == 0, retargeted.stderr
    assert json.loads(retargeted.stdout)['generation'] == 1

    no_tools = cli(app, 'schedule', 'create', 'no-tools', 'plain work',
                   '--session', session['id'], '--due', due, '--no-tools')
    assert no_tools.returncode == 0, no_tools.stderr
    assert json.loads(no_tools.stdout)['tools'] == []
    invalid = cli(app, 'schedule', 'create', 'ambiguous-tools', 'work',
                  '--session', session['id'], '--due', due,
                  '--tool', 'workspace_read', '--no-tools')
    assert invalid.returncode != 0 and 'tool_selection' in invalid.stderr
    assert peer.requests.empty()


async def seed_due_schedule(root):
    store = await Store.open(root)
    try:
        session = await store.create_session()
        request = ScheduleRequest(
            id='crash-atomic',
            session_id=session.id,
            generation=session.generation,
            input='finish after crash recovery',
            next_due_at=datetime.now(UTC) - timedelta(seconds=1),
            interval_seconds=None,
            tools=(),
        )
        await store.create_schedule(request)
    finally:
        await store.close()


@pytest.mark.parametrize('stage', ['before_schedule_commit', 'after_schedule_commit'])
def test_sigkill_around_schedule_enqueue_commit_recovers_exactly_once(tmp_path, stage):
    peer = ProviderStub()
    app = Process(tmp_path / 'runtime', peer.url, timeout=15, launcher=RECOVERY_LAUNCHER)
    asyncio.run(seed_due_schedule(app.root))
    (app.root / 'test-fault.json').write_text(json.dumps({'stage': stage}))
    peer.enqueue(Reply(chunks=('Recovered once.',)))
    try:
        app.start()
        wait_for(lambda: (app.root / 'test-barrier').exists(), app, seconds=5)
        app.kill()
        app.launcher = None
        app.start()

        occurrence = wait_for(
            lambda: (items[0] if (items := app.client.get(
                '/v1/schedules/crash-atomic/occurrences').json()) else None),
            app,
            seconds=5,
        )
        replay = events(app, occurrence['run_id'])
        assert replay[-1]['data'] == {'status': 'succeeded', 'verification': 'not_requested'}
        assert [event['kind'] for event in replay].count('run.queued') == 1
        assert app.client.get('/v1/schedules/crash-atomic/occurrences').json() == [occurrence]
        with sqlite3.connect(app.root / 'runtime.sqlite3') as database:
            counts = (
                database.execute('SELECT count(*) FROM schedule_occurrences').fetchone()[0],
                database.execute('SELECT count(*) FROM runs').fetchone()[0],
                database.execute("SELECT count(*) FROM messages WHERE role='user'").fetchone()[0],
                database.execute("SELECT count(*) FROM events WHERE kind='run.queued'").fetchone()[0],
            )
        assert counts == (1, 1, 1, 1)
        assert peer.requests.qsize() == 1
        assert not peer.errors
    finally:
        app.stop()
        peer.close()


def test_http_and_cli_reject_scheduled_mcp_but_preserve_other_schedules(service):
    app, peer = service
    session = app.client.post('/v1/sessions').json()
    body = schedule_body(session, 'unsupported', tools=('mcp_docs_read',))
    response = app.client.post('/v1/schedules', json=body)
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'mcp_schedule_unsupported'
    assert 'directly submitted run' in response.json()['error']['message']
    invalid_cli = cli(app, 'schedule', 'create', 'unsupported-cli', 'read docs',
                      '--session', session['id'], '--due', body['next_due_at'], '--tool', 'mcp_docs_search')
    assert invalid_cli.returncode != 0
    assert app.client.get('/v1/schedules').json() == []
    valid_cli = cli(app, 'schedule', 'create', 'supported', 'read workspace',
                    '--session', session['id'], '--due', body['next_due_at'], '--tool', 'workspace_read')
    assert valid_cli.returncode == 0, valid_cli.stderr
    assert app.client.get('/v1/schedules').json()[0]['tools'] == ['workspace_read']
    assert 'mcp_docs_' not in cli(app, 'schedule', 'create', '--help').stdout
    assert app.client.get('/healthz').status_code == 200
    assert peer.requests.empty()
