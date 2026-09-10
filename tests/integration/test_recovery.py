"""Real SIGKILL, transport, SQLite and workspace effects; no runtime fault hooks."""
import hashlib
import json
from pathlib import Path
import threading
import time

import pytest

from tests.integration.test_cli_tools import cli, run_id, write_reply
from tests.support.process import Process, events, sse_events, submit
from tests.support.provider import ProviderStub, Reply

LAUNCHER = Path(__file__).resolve().parents[1] / 'support/recovery_daemon.py'


def wait_for(predicate, app, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.01)
    raise AssertionError('Condition timed out\n' + app.diagnostics())


def grant_write(app):
    workspace = app.client.get('/v1/workspace').json()
    assert app.client.post('/v1/grants', json={'workspace_id': workspace['id'], 'capability': 'write'}).status_code == 200


@pytest.mark.parametrize('detach', [False, True])
def test_same_tool_workflow_foreground_and_detached(tmp_path, detach):
    peer = ProviderStub()
    app = Process(tmp_path / 'runtime', peer.url)
    try:
        app.start()
        grant_write(app)
        peer.enqueue(write_reply(), Reply(chunks=('Verified.',)))
        result = cli(app, 'chat', 'Write hello', *(['--detach'] if detach else []))
        assert result.returncode == 0, result.stderr
        identifier = result.stdout.strip() if detach else run_id(result.stderr)
        assert events(app, identifier)[-1]['data'] == {'status': 'succeeded', 'verification': 'passed'}
        receipts = app.client.get(f'/v1/runs/{identifier}/receipts').json()
        assert len(receipts) == 1 and receipts[0]['status'] == 'succeeded'
        assert receipts[0]['artifacts'][0]['sha256'] == hashlib.sha256(b'hello').hexdigest()
        assert (app.root / 'workspace/answer.txt').read_text() == 'hello'
        peer.take_request()
        grouped = peer.take_request()['messages'][-2:]
        assert [message['role'] for message in grouped] == ['assistant', 'user']
        assert grouped[-1]['content'][0]['tool_use_id'] == 'write-1'
        assert peer.requests.empty()
    finally:
        app.stop()
        peer.close()


def test_tool_workflow_continues_after_observer_disconnect(tmp_path):
    peer = ProviderStub()
    app = Process(tmp_path / 'runtime', peer.url)
    gate = threading.Event()
    try:
        app.start()
        grant_write(app)
        peer.enqueue(write_reply(), Reply(chunks=('Verified ', 'after disconnect.'), gate=gate))
        detached = cli(app, 'chat', 'Write hello', '--detach')
        assert detached.returncode == 0, detached.stderr
        identifier = detached.stdout.strip()

        prefix = []
        with app.client.stream('GET', f'/v1/runs/{identifier}/events') as response:
            for event in sse_events(response):
                prefix.append(event)
                if event['kind'] == 'model.text':
                    break
        assert app.client.get(f'/v1/runs/{identifier}').json()['status'] == 'running'
        assert len(app.client.get(f'/v1/runs/{identifier}/receipts').json()) == 1

        gate.set()
        replay = prefix + events(app, identifier, prefix[-1]['seq'])
        assert replay[-1]['data'] == {'status': 'succeeded', 'verification': 'passed'}
        assert [event['kind'] for event in replay].count('tool.finished') == 1
        assert (app.root / 'workspace/answer.txt').read_text() == 'hello'
        peer.take_request()
        grouped = peer.take_request()['messages'][-2:]
        assert [message['role'] for message in grouped] == ['assistant', 'user']
        assert grouped[-1]['content'][0]['tool_use_id'] == 'write-1'
        assert not peer.errors
    finally:
        gate.set()
        app.stop()
        peer.close()


@pytest.mark.parametrize('stage,run_status,receipt_status,effect', [
    ('before_invocation', 'interrupted', None, False),
    ('after_invocation', 'interrupted', 'interrupted', False),
    ('before_effect', 'uncertain', 'uncertain', False),
    ('after_dispatch', 'uncertain', 'uncertain', True),
    ('after_receipt', 'interrupted', 'succeeded', True),
])
def test_process_kill_matrix(tmp_path, stage, run_status, receipt_status, effect):
    peer = ProviderStub()
    app = Process(tmp_path / 'runtime', peer.url, launcher=LAUNCHER)
    (app.root / 'test-fault.json').write_text(json.dumps({'stage': stage}))
    try:
        app.start()
        grant_write(app)
        peer.enqueue(write_reply())
        run = submit(app)
        wait_for(lambda: (app.root / 'test-barrier').exists(), app)
        marker = app.root / 'workspace/answer.txt'
        assert marker.exists() == effect
        if effect:
            assert marker.read_text() == 'hello'
        app.kill()
        marker.write_text('operator edit after kill')
        app.launcher = None
        for _ in range(2):
            app.start()
            recovered = app.client.get(f"/v1/runs/{run['id']}").json()
            assert recovered['status'] == run_status
            assert recovered['verification'] == ('passed' if stage == 'after_receipt' else 'not_requested')
            receipts = app.client.get(f"/v1/runs/{run['id']}/receipts").json()
            assert len(receipts) == (0 if receipt_status is None else 1)
            if receipt_status:
                assert receipts[0]['status'] == receipt_status
            replay = events(app, run['id'])
            assert sum(e['kind'] == 'run.finished' for e in replay) == 1
            assert sum(e['kind'] == 'tool.finished' for e in replay) == len(receipts)
            assert marker.read_text() == 'operator edit after kill'
            # Same accepted request cannot restart a terminal run.
            duplicate = app.client.post('/v1/runs', json=run['request'])
            assert duplicate.status_code == 202 and duplicate.json()['status'] == run_status
            if run_status == 'uncertain':
                retry = dict(run['request'], request_id='explicit-retry', retry_of=run['id'])
                assert app.client.post('/v1/runs', json=retry).status_code == 409
            app.stop()
        assert peer.requests.qsize() == 1
        assert not peer.errors
    finally:
        app.stop()
        peer.close()
