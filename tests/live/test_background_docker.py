"""Health, durable replay and cancellation while a real tool owns child processes."""
import time

import pytest

from tests.live.test_recovery_docker import (
    TREE_SCRIPT, cleanup_owned, command_reply, grant_command, owned_container_ids, wait_for,
)
from tests.support.process import Process, events, sse_events, submit
from tests.support.provider import ProviderStub

pytestmark = pytest.mark.docker


def tool_prefix(app, run_id):
    prefix = []
    with app.client.stream('GET', f'/v1/runs/{run_id}/events') as response:
        for event in sse_events(response):
            prefix.append(event)
            if event['kind'] == 'tool.requested':
                return prefix
    raise AssertionError('Expected committed invocation before completion')


def test_http_remains_responsive_and_cancellation_stops_slow_tool_tree(tmp_path):
    peer = ProviderStub()
    app = Process(tmp_path / 'runtime', peer.url, timeout=15)
    try:
        app.start()
        grant_command(app)
        peer.enqueue(command_reply(TREE_SCRIPT))
        run = submit(app, 'run the controlled slow command', tools=['command'])
        wait_for(app.root / 'workspace/ready', app)
        ready_at = time.monotonic()
        assert len(owned_container_ids(app.root)) == 1

        started = time.monotonic()
        assert app.client.get('/healthz').json() == {'status': 'ok'}
        first = tool_prefix(app, run['id'])
        assert tool_prefix(app, run['id']) == first
        assert time.monotonic() - started < 2

        started = time.monotonic()
        cancelled = app.client.post(f"/v1/runs/{run['id']}/cancel")
        assert cancelled.status_code == 200 and cancelled.json()['status'] == 'cancelled'
        assert time.monotonic() - started < 5
        receipts = app.client.get(f"/v1/runs/{run['id']}/receipts").json()
        assert len(receipts) == 1 and receipts[0]['evidence']['terminated'] is True
        assert owned_container_ids(app.root) == []
        assert events(app, run['id'])[-1]['data']['status'] == 'cancelled'
        remaining = 8.3 - (time.monotonic() - ready_at)
        if remaining > 0:
            time.sleep(remaining)
        assert not (app.root / 'workspace/parent-late').exists()
        assert not (app.root / 'workspace/child-late').exists()
        assert peer.requests.qsize() == 1
        assert not peer.errors
    finally:
        app.stop()
        cleanup_owned(app.root)
        peer.close()
