"""Real Docker recovery through killed daemon processes and ordinary startup."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import time

import pytest

from hyperclaw.execution.docker import INSTALLATION_LABEL
from tests.support.process import Process, events, submit
from tests.support.provider import ProviderStub, Reply
from tests.support.tool_provider import frames, message_end, message_start, tool_block

pytestmark = pytest.mark.docker

LAUNCHER = Path(__file__).resolve().parents[1] / 'support/recovery_daemon.py'


def wait_for(path, app, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(.02)
    raise AssertionError(f'Condition timed out waiting for {path}\n' + app.diagnostics())


def command_reply(script, *, checks=()):
    arguments = {'argv': ['python', '-c', script], 'timeout_s': 30, 'checks': list(checks)}
    return Reply(frames=frames(
        message_start(),
        *tool_block(0, 'command-1', 'command', (json.dumps(arguments),)),
        *message_end(),
    ))


def grant_command(app):
    workspace = app.client.get('/v1/workspace').json()
    for capability in ('execute', 'write'):
        response = app.client.post('/v1/grants', json={
            'workspace_id': workspace['id'], 'capability': capability,
        })
        assert response.status_code == 200, response.text


def owned_container_ids(root):
    database = root / 'runtime.sqlite3'
    if not database.exists():
        return []
    with sqlite3.connect(database) as connection:
        row = connection.execute('SELECT id FROM installation').fetchone()
    if row is None:
        return []
    result = subprocess.run(
        ['docker', 'ps', '-a', '-q', '--no-trunc', '--filter', f'label={INSTALLATION_LABEL}={row[0]}'],
        capture_output=True, text=True, timeout=20, check=True,
    )
    return [value for value in result.stdout.splitlines() if value]


def cleanup_owned(root):
    for container_id in owned_container_ids(root):
        subprocess.run(['docker', 'rm', '--force', container_id], capture_output=True, timeout=20, check=True)


TREE_SCRIPT = """import os,time
from pathlib import Path
Path('/workspace/ready').write_text('yes')
pid=os.fork()
if pid==0:
    time.sleep(8);Path('/workspace/child-late').write_text('bad');os._exit(0)
time.sleep(8);Path('/workspace/parent-late').write_text('bad')
os.waitpid(pid,0)
"""


def test_startup_stops_live_container_tree_before_settling_run(tmp_path):
    peer = ProviderStub()
    app = Process(tmp_path / 'runtime', peer.url, launcher=LAUNCHER, timeout=15)
    (app.root / 'test-fault.json').write_text(json.dumps({'stage': 'after_dispatch'}))
    try:
        app.start()
        grant_command(app)
        peer.enqueue(command_reply(TREE_SCRIPT))
        run = submit(app)
        wait_for(app.root / 'test-barrier', app)
        started_at = time.monotonic()
        assert (app.root / 'workspace/ready').read_text() == 'yes'
        app.kill()

        app.launcher = None
        app.start()
        recovered = app.client.get(f"/v1/runs/{run['id']}").json()
        receipts = app.client.get(f"/v1/runs/{run['id']}/receipts").json()
        assert recovered['status'] == 'interrupted'
        assert len(receipts) == 1 and receipts[0]['status'] == 'interrupted'
        assert receipts[0]['evidence']['terminated'] is True
        assert owned_container_ids(app.root) == []
        remaining = 8.3 - (time.monotonic() - started_at)
        if remaining > 0:
            time.sleep(remaining)
        assert not (app.root / 'workspace/parent-late').exists()
        assert not (app.root / 'workspace/child-late').exists()

        original = (recovered, receipts, events(app, run['id']))
        app.restart()
        assert app.client.get(f"/v1/runs/{run['id']}").json() == original[0]
        assert app.client.get(f"/v1/runs/{run['id']}/receipts").json() == original[1]
        replay = events(app, run['id'])
        assert replay == original[2]
        assert [event['kind'] for event in replay].count('run.finished') == 1
        assert peer.requests.qsize() == 1
        assert not peer.errors
    finally:
        app.stop()
        cleanup_owned(app.root)
        peer.close()


def test_startup_records_already_exited_container_and_artifact(tmp_path):
    peer = ProviderStub()
    app = Process(tmp_path / 'runtime', peer.url, launcher=LAUNCHER, timeout=15)
    (app.root / 'test-fault.json').write_text(json.dumps({'stage': 'after_exit'}))
    effect = app.root / 'workspace/effect'
    try:
        app.start()
        grant_command(app)
        expected = hashlib.sha256(b'complete').hexdigest()
        peer.enqueue(command_reply(
            "from pathlib import Path;Path('/workspace/effect').write_text('complete')",
            checks=({'path': 'effect', 'expected_sha256': expected},),
        ))
        run = submit(app)
        wait_for(app.root / 'test-barrier', app)
        assert effect.read_text() == 'complete'
        app.kill()

        app.launcher = None
        app.start()
        recovered = app.client.get(f"/v1/runs/{run['id']}").json()
        receipts = app.client.get(f"/v1/runs/{run['id']}/receipts").json()
        assert recovered['status'] == 'interrupted'
        assert recovered['verification'] == 'passed'
        assert len(receipts) == 1 and receipts[0]['status'] == 'succeeded'
        assert receipts[0]['artifacts'][0]['sha256'] == expected
        assert receipts[0]['evidence']['exit_code'] == 0
        assert receipts[0]['evidence']['terminated'] is True
        assert owned_container_ids(app.root) == []
        app.restart()
        replay = events(app, run['id'])
        assert [event['kind'] for event in replay].count('run.finished') == 1
        assert [event['kind'] for event in replay].count('tool.finished') == 1
        assert peer.requests.qsize() == 1
        assert not peer.errors
    finally:
        app.stop()
        cleanup_owned(app.root)
        peer.close()


def test_unavailable_backend_records_uncertain_then_later_cleans_owned_tree(tmp_path):
    peer = ProviderStub()
    app = Process(tmp_path / 'runtime', peer.url, launcher=LAUNCHER, timeout=15)
    (app.root / 'test-fault.json').write_text(json.dumps({'stage': 'after_dispatch'}))
    try:
        app.start()
        grant_command(app)
        peer.enqueue(command_reply(TREE_SCRIPT))
        run = submit(app)
        wait_for(app.root / 'test-barrier', app)
        started_at = time.monotonic()
        app.kill()

        app.launcher = None
        missing_path = tmp_path / 'without-docker'
        missing_path.mkdir()
        app.environment = {'PATH': str(missing_path)}
        app.start()
        recovered = app.client.get(f"/v1/runs/{run['id']}").json()
        receipts = app.client.get(f"/v1/runs/{run['id']}/receipts").json()
        assert recovered['status'] == 'uncertain'
        assert len(receipts) == 1 and receipts[0]['status'] == 'uncertain'
        assert 'unavailable' in receipts[0]['evidence']['reason'].lower()
        assert len(owned_container_ids(app.root)) == 1
        app.stop()

        app.environment = {}
        app.start()
        assert app.client.get(f"/v1/runs/{run['id']}/receipts").json() == receipts
        assert owned_container_ids(app.root) == []
        remaining = 8.3 - (time.monotonic() - started_at)
        if remaining > 0:
            time.sleep(remaining)
        assert not (app.root / 'workspace/parent-late').exists()
        assert not (app.root / 'workspace/child-late').exists()
        replay = events(app, run['id'])
        assert [event['kind'] for event in replay].count('run.finished') == 1
        assert [event['kind'] for event in replay].count('tool.finished') == 1
        assert peer.requests.qsize() == 1
        assert not peer.errors
    finally:
        app.environment = {}
        app.stop()
        cleanup_owned(app.root)
        peer.close()
