"""Background ownership and control responsiveness through real daemon HTTP."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
import threading
import time

import httpx
import pytest

from tests.integration.test_cli_tools import pending_approval, write_reply
from tests.integration.test_recovery import wait_for
from tests.support.process import Process, events, sse_events, submit
from tests.support.provider import ProviderStub, Reply

MAINTENANCE_LAUNCHER = Path(__file__).resolve().parents[1] / 'support/maintenance_daemon.py'


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


def committed_prefix(app, run_id):
    prefix = []
    with app.client.stream('GET', f'/v1/runs/{run_id}/events') as response:
        for event in sse_events(response):
            prefix.append(event)
            if event['kind'] == 'model.text':
                return prefix
    raise AssertionError('Expected committed model text before completion')


def test_queued_cancellation_never_dispatches_and_slow_model_controls_respond(service):
    app, peer = service
    gate, disconnected = threading.Event(), threading.Event()
    peer.enqueue(Reply(gate=gate, disconnected=disconnected))
    active = submit(app, 'hold this model request', tools=[])
    prefix = committed_prefix(app, active['id'])
    assert peer.take_request()['messages'][-1]['content'] == 'hold this model request'
    queued = submit(app, 'cancel before dispatch', tools=['workspace_write'])
    assert queued['status'] == 'queued'

    started = time.monotonic()
    assert app.client.get('/healthz').json() == {'status': 'ok'}
    replay = committed_prefix(app, active['id'])
    assert replay == prefix
    assert time.monotonic() - started < 2

    started = time.monotonic()
    cancelled = app.client.post(f"/v1/runs/{queued['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()['status'] == 'cancelled'
    assert time.monotonic() - started < 2
    queued_events = events(app, queued['id'])
    assert [event['kind'] for event in queued_events] == ['run.queued', 'run.finished']
    assert app.client.get(f"/v1/runs/{queued['id']}/receipts").json() == []

    started = time.monotonic()
    assert app.client.post(f"/v1/runs/{active['id']}/cancel").json()['status'] == 'cancelled'
    assert time.monotonic() - started < 3
    assert disconnected.wait(2), 'Cancelling the run left model IO active'
    app.restart()
    assert events(app, queued['id']) == queued_events
    assert peer.requests.empty()
    assert not list((app.root / 'workspace').iterdir())
    assert not peer.errors


def test_concurrent_approval_after_restart_queues_one_resume_behind_active_run(service):
    app, peer = service
    peer.enqueue(write_reply())
    waiting = submit(app, 'write once after approval')
    approval = pending_approval(app)
    peer.take_request()
    app.restart()
    assert app.client.get('/v1/approvals').json() == [approval]

    gate, disconnected = threading.Event(), threading.Event()
    peer.enqueue(Reply(gate=gate, disconnected=disconnected), Reply(chunks=('Approved work finished.',)))
    active = submit(app, 'occupy the only worker', tools=[])
    committed_prefix(app, active['id'])
    assert peer.take_request()['messages'][-1]['content'] == 'occupy the only worker'
    decision = {'approved': True, 'arguments_sha256': approval['arguments_sha256'],
                'policy_sha256': approval['policy_sha256']}
    rendezvous = threading.Barrier(2)

    def approve():
        with httpx.Client(base_url=app.url, headers=dict(app.client.headers), trust_env=False, timeout=5) as client:
            rendezvous.wait(timeout=5)
            response = client.post(f"/v1/approvals/{approval['id']}/decision", json=decision)
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(approve), pool.submit(approve)
        outcomes = [first.result(timeout=10), second.result(timeout=10)]
    assert sorted(code for code, _ in outcomes) == [200, 409]
    assert next(body for code, body in outcomes if code == 409)['error']['code'] == 'approval_not_pending'
    assert app.client.get(f"/v1/runs/{waiting['id']}").json()['status'] == 'queued'
    assert app.client.get(f"/v1/runs/{active['id']}").json()['status'] == 'running'
    assert not (app.root / 'workspace/answer.txt').exists()
    assert peer.requests.empty()

    assert app.client.post(f"/v1/runs/{active['id']}/cancel").json()['status'] == 'cancelled'
    assert disconnected.wait(2)
    resumed_events = events(app, waiting['id'])
    assert resumed_events[-1]['data'] == {'status': 'succeeded', 'verification': 'passed'}
    assert sum(event['kind'] == 'approval.decided' for event in resumed_events) == 1
    starts = [event for event in resumed_events if event['kind'] == 'run.started']
    assert len(starts) == 2
    active_finished = events(app, active['id'])[-1]
    assert datetime.fromisoformat(active_finished['at']) <= datetime.fromisoformat(starts[-1]['at'])
    receipts = app.client.get(f"/v1/runs/{waiting['id']}/receipts").json()
    assert len(receipts) == 1 and receipts[0]['status'] == 'succeeded'
    assert (app.root / 'workspace/answer.txt').read_text() == 'hello'
    continuation = peer.take_request()['messages'][-1]['content']
    assert len(continuation) == 1 and continuation[0]['tool_use_id'] == 'write-1'
    assert peer.requests.empty()
    assert not peer.errors


def test_approval_expires_while_another_session_occupies_worker(tmp_path):
    peer = ProviderStub()
    app = Process(tmp_path / 'runtime', peer.url, timeout=15, launcher=MAINTENANCE_LAUNCHER)
    (app.root / 'test-maintenance-stage').write_text('expiry')
    try:
        app.start()
        peer.enqueue(write_reply())
        waiting = submit(app, 'this approval expires soon')
        with app.client.stream('GET', f"/v1/runs/{waiting['id']}/events") as response:
            for event in sse_events(response):
                if event['kind'] == 'approval.required':
                    break
            else:
                raise AssertionError('Expected pending approval')
        peer.take_request()
        gate, disconnected = threading.Event(), threading.Event()
        peer.enqueue(Reply(gate=gate, disconnected=disconnected))
        active = submit(app, 'keep the worker occupied past expiry', tools=[])
        committed_prefix(app, active['id'])
        peer.take_request()

        # GET approvals also expires them; inspect the run to test daemon maintenance.
        def expired():
            result = app.client.get(f"/v1/runs/{waiting['id']}").json()
            return result if result['status'] == 'failed' else None

        result = wait_for(expired, app, seconds=5)
        assert result['error']['code'] == 'approval_expired'
        assert app.client.get(f"/v1/runs/{active['id']}").json()['status'] == 'running'
        assert not (app.root / 'workspace/answer.txt').exists()
        assert app.client.post(f"/v1/runs/{active['id']}/cancel").json()['status'] == 'cancelled'
        assert disconnected.wait(2)
        assert peer.requests.empty()
        assert not peer.errors
    finally:
        app.stop()
        peer.close()


@pytest.mark.parametrize('stage', ['worker', 'maintenance'])
def test_failed_background_owner_makes_health_and_acceptance_unavailable(tmp_path, stage):
    peer = ProviderStub()
    app = Process(tmp_path / 'runtime', peer.url, launcher=MAINTENANCE_LAUNCHER)
    (app.root / 'test-maintenance-stage').write_text(stage)
    try:
        app.start()
        session = app.client.post('/v1/sessions').json()
        due = datetime.now().astimezone() + timedelta(seconds=3)
        scheduled_before_failure = app.client.post('/v1/schedules', json={
            'id': f'before-{stage}-failure',
            'session_id': session['id'],
            'generation': session['generation'],
            'input': 'must remain unreserved',
            'next_due_at': due.isoformat(),
            'interval_seconds': None,
            'tools': [],
        })
        assert scheduled_before_failure.status_code == 200, scheduled_before_failure.text
        (app.root / 'test-failure-trigger').touch()
        wait_for(lambda: (app.root / 'test-failure-observed').exists(), app, seconds=5)
        wait_for(lambda: app.client.get('/healthz').status_code == 503, app, seconds=5)
        remaining = (due - datetime.now().astimezone()).total_seconds() + .5
        if remaining > 0:
            time.sleep(remaining)
        assert app.client.get(f'/v1/schedules/before-{stage}-failure/occurrences').json() == []
        response = app.client.post('/v1/sessions')
        assert response.status_code == 503 and response.json()['error']['code'] == 'storage_failure'
        scheduled = app.client.post('/v1/schedules', json={
            'id': f'after-{stage}-failure',
            'session_id': session['id'],
            'generation': session['generation'],
            'input': 'must not be accepted',
            'next_due_at': datetime.now().astimezone().isoformat(),
            'interval_seconds': None,
            'tools': [],
        })
        assert scheduled.status_code == 503
        assert scheduled.json()['error']['code'] == 'storage_failure'
        assert peer.requests.empty()
    finally:
        app.stop()
        peer.close()
