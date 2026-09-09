import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import httpx
import pytest

from tests.support.process import Process, events, sse_events, submit
from tests.support.provider import ProviderStub, Reply


@pytest.fixture
def service(tmp_path):
    peer = ProviderStub()
    app = Process(tmp_path / 'runtime', peer.url)
    try:
        app.start()
        yield app, peer
    finally:
        app.stop()
        peer.close()


def test_http_requires_operator_token(service):
    app, peer = service
    with httpx.Client(base_url=app.url, trust_env=False) as stranger:
        assert stranger.get('/healthz').json() == {'status': 'ok'}
        assert stranger.post('/v1/sessions', json={}).status_code == 401
        for path in ['/v1/runs/missing', '/v1/runs/missing/events', '/v1/sessions/missing']:
            assert stranger.get(path).status_code == 401
        assert stranger.get('/v1/runs/missing', headers={'Authorization': 'Bearer wrong'}).status_code == 401
    assert app.client.post('/v1/sessions', json={}).status_code == 200
    assert app.client.get('/healthz', headers={'Host': 'evil.example'}).status_code == 400
    assert app.client.post('/v1/sessions', headers={'Origin': 'https://evil.example'}).status_code == 403
    metadata = (app.root / 'daemon.json').read_text()
    token = (app.root / 'token').read_text().strip()
    assert token not in metadata and token not in app.diagnostics()
    assert peer.requests.empty()


def test_stream_is_incremental_and_disconnect_does_not_cancel(service):
    app, peer = service
    gate = threading.Event()
    peer.enqueue(Reply(gate=gate, thinking='reason'))
    run = submit(app)
    with app.client.stream('GET', f"/v1/runs/{run['id']}/events") as response:
        prefix = []
        for event in sse_events(response):
            prefix.append(event)
            if event['kind'] == 'model.text':
                assert event['data']['text'] == 'Hello '
                break
    assert app.client.get(f"/v1/runs/{run['id']}").json()['status'] == 'running'
    assert app.client.get('/healthz').status_code == 200
    gate.set()
    suffix = events(app, run['id'], prefix[-1]['seq'])
    assert suffix[-1]['data']['status'] == 'succeeded'
    assert any(e['kind'] == 'model.thinking' for e in prefix)
    assert [e['kind'] for e in suffix].count('model.usage') == 1
    assert app.client.get(f"/v1/runs/{run['id']}").json()['output'] == 'Hello world.'


def test_crash_replays_committed_prefix_and_linked_retry(service):
    app, peer = service
    gate = threading.Event()
    peer.enqueue(Reply(gate=gate), Reply(chunks=('recovered',)))
    run = submit(app)
    prefix = []
    with app.client.stream('GET', f"/v1/runs/{run['id']}/events") as response:
        for event in sse_events(response):
            prefix.append(event)
            if event['kind'] == 'model.text':
                break
    token = (app.root / 'token').read_bytes()
    app.kill()
    gate.set()
    app.start()
    result = app.client.get(f"/v1/runs/{run['id']}").json()
    assert result['status'] == 'interrupted' and result['output'] is None
    replay = events(app, run['id'])
    assert replay[:-1] == prefix
    assert replay[-1]['kind'] == 'run.finished'
    assert replay[-1]['data']['status'] == 'interrupted'
    assert (app.root / 'token').read_bytes() == token
    session = app.client.get('/v1/sessions/' + run['request']['session_id']).json()
    retried = submit(app, 'retry', session, 'retry', retry_of=run['id'])
    assert retried['id'] != run['id']
    assert events(app, retried['id'])[-1]['data']['status'] == 'succeeded'
    peer.take_request()
    assert peer.take_request()['messages'] == [{'role': 'user', 'content': 'retry'}]


def test_restart_new_session_and_reset_prompt_content(service):
    app, peer = service
    peer.enqueue(Reply(chunks=('opaque answer',)), Reply(), Reply(), Reply())
    session = app.client.post('/v1/sessions', json={}).json()
    first = submit(app, 'opaque prompt', session)
    original = events(app, first['id'])
    peer.take_request()
    app.restart()
    assert events(app, first['id']) == original
    second = submit(app, 'continue', session, 'second')
    events(app, second['id'])
    assert peer.take_request()['messages'] == [{'role': 'user', 'content': 'opaque prompt'}, {'role': 'assistant', 'content': 'opaque answer'}, {'role': 'user', 'content': 'continue'}]
    fresh = submit(app, 'new conversation')
    events(app, fresh['id'])
    assert peer.take_request()['messages'] == [{'role': 'user', 'content': 'new conversation'}]
    reset = app.client.post(f"/v1/sessions/{session['id']}/reset", json={'generation': 0}).json()
    assert reset['generation'] == 1
    app.restart()
    last = submit(app, 'reset conversation', reset)
    events(app, last['id'])
    assert peer.take_request()['messages'] == [{'role': 'user', 'content': 'reset conversation'}]


def test_conflicts_validation_and_errors_before_sse_headers(service):
    app, peer = service
    peer.enqueue(Reply(gate=threading.Event()))
    run = submit(app)
    assert app.client.post('/v1/runs', json=run['request']).json()['id'] == run['id']
    for changes in [{'text': 'changed'}, {'request_id': 'new'}, {'generation': 1}]:
        assert app.client.post('/v1/runs', json=run['request'] | changes).status_code == 409
    assert app.client.post(f"/v1/sessions/{run['request']['session_id']}/reset", json={'generation': 0}).status_code == 409
    for cursor in ['bad', '-1', '999999']:
        response = app.client.get(f"/v1/runs/{run['id']}/events", params={'after': cursor})
        assert response.status_code == 409
        assert response.headers['content-type'].startswith('application/json')
    assert app.client.get('/v1/runs/missing/events').status_code == 404
    for changes in [{'generation': -1}, {'generation': True}, {'text': ''}, {'text': 'x' * 70000}, {'extra': True}]:
        assert app.client.post('/v1/runs', json=run['request'] | changes).status_code == 422
    assert app.client.post(f"/v1/runs/{run['id']}/cancel").json()['status'] == 'cancelled'
    assert app.client.post(f"/v1/runs/{run['id']}/cancel").json()['status'] == 'cancelled'


@pytest.mark.parametrize('reply,code', [(Reply(truncate=True), 'incomplete_stream'), (Reply(status=502), 'http_502'), (Reply(stop_reason='max_tokens'), 'output_limit')])
def test_async_provider_failures_are_durable(service, reply, code):
    app, peer = service
    peer.enqueue(reply)
    run = submit(app)
    seen = events(app, run['id'])
    assert seen[-1]['data']['status'] == 'failed'
    result = app.client.get(f"/v1/runs/{run['id']}").json()
    assert result['error']['code'] == code and result['output'] is None
    assert peer.requests.qsize() == 1


def test_second_daemon_fails_without_removing_owner_metadata(service):
    app, peer = service
    metadata = (app.root / 'daemon.json').read_bytes()
    result = subprocess.run([sys.executable, '-m', 'hyperclaw', '--root', str(app.root), 'serve', '--port', '0'],
                            env={'PATH': os.environ['PATH']}, cwd=app._cwd.name, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert 'root_in_use' in result.stdout + result.stderr
    assert (app.root / 'daemon.json').read_bytes() == metadata
    assert app.client.get('/healthz').status_code == 200


def cli(app, *args):
    return subprocess.run([sys.executable, '-m', 'hyperclaw', '--root', str(app.root), *args],
                          cwd=app._cwd.name, env={'PATH': os.environ['PATH']}, capture_output=True, text=True, timeout=10)


def test_cli_chat_detach_inspect_events_cancel_and_reset(service):
    app, peer = service
    peer.enqueue(Reply(), Reply(gate=threading.Event()))
    session = app.client.post('/v1/sessions', json={}).json()
    reply = cli(app, 'chat', 'hello', '--session', session['id'])
    assert reply.returncode == 0, reply.stderr
    assert 'Hello world.' in reply.stdout
    detached = cli(app, 'chat', 'detached', '--session', session['id'], '--detach')
    assert detached.returncode == 0, detached.stderr
    run_id = detached.stdout.strip()
    assert json.loads(cli(app, 'run', 'inspect', run_id).stdout)['request']['text'] == 'detached'
    assert cli(app, 'run', 'cancel', run_id).returncode == 0
    assert 'cancelled' in cli(app, 'run', 'events', run_id).stdout
    assert json.loads(cli(app, 'session', 'reset', session['id']).stdout)['generation'] == 1


def test_cli_missing_daemon_does_not_create_runtime(tmp_path):
    result = subprocess.run([sys.executable, '-m', 'hyperclaw', '--root', str(tmp_path / 'absent'), 'chat', 'hello'],
                            cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0 and 'serve' in result.stderr
    assert not (tmp_path / 'absent').exists()


async def test_default_http_port_accepts_normalized_loopback_host(tmp_path):
    from hyperclaw.api import create_app
    from hyperclaw.config import load_settings, read_token
    settings = load_settings(root=tmp_path, overrides={'port': 80, 'ollama_url': 'http://127.0.0.1:1'})
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as client:
            assert (await client.get('/healthz')).status_code == 200
            auth = {'Authorization': 'Bearer ' + read_token(tmp_path), 'Origin': 'http://127.0.0.1'}
            assert (await client.post('/v1/sessions', headers=auth)).status_code == 200
            assert (await client.get('/healthz', headers={'Host': '127.0.0.1:81'})).status_code == 400


async def test_malformed_discovery_metadata_cannot_prevent_shutdown(tmp_path):
    from hyperclaw.api import create_app
    from hyperclaw.config import load_settings
    from hyperclaw.store import Store
    app = create_app(load_settings(root=tmp_path, overrides={'ollama_url': 'http://127.0.0.1:1'}))
    async with app.router.lifespan_context(app):
        (tmp_path / 'daemon.json').write_text('[]')
    assert (tmp_path / 'daemon.json').read_text() == '[]'
    store = await Store.open(tmp_path)
    await store.close()
