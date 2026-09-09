import base64
import json
import os
import re
import subprocess
import sys
import time

import pytest

from tests.support.process import Process, events
from tests.support.provider import ProviderStub, Reply
from tests.support.tool_provider import frames, message_end, message_start, tool_block


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


def cli(app, *args):
    return subprocess.run(
        [sys.executable, '-m', 'hyperclaw', '--root', str(app.root), *args],
        cwd=app._cwd.name,
        env={'PATH': os.environ['PATH']},
        capture_output=True,
        text=True,
        timeout=10,
    )


def pending_approval(app):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        approvals = app.client.get('/v1/approvals').json()
        if approvals:
            return approvals[0]
        time.sleep(0.01)
    raise AssertionError('approval did not become pending')


def write_reply(call_id='write-1'):
    return Reply(frames=frames(
        message_start(),
        *tool_block(0, call_id, 'workspace_write', ('{"path":"answer.txt","content":"hello"}',)),
        *message_end(),
    ))


def run_id(stderr):
    match = re.search(r'run ([a-f0-9]+)', stderr)
    assert match, stderr
    return match.group(1)


def test_cli_chat_submits_validated_image_context_and_selected_tools(service, tmp_path):
    app, peer = service
    image = tmp_path / 'tiny.png'
    raw = b'\x89PNG\r\n\x1a\nsynthetic-image'
    image.write_bytes(raw)
    peer.enqueue(Reply())

    result = cli(
        app,
        'chat',
        'describe this',
        '--image',
        str(image),
        '--context-bytes',
        '131072',
        '--tool',
        'workspace_read',
    )

    assert result.returncode == 0, result.stderr
    request = peer.take_request()
    assert [tool['name'] for tool in request['tools']] == ['workspace_read']
    assert request['messages'] == [{
        'role': 'user',
        'content': [
            {'type': 'text', 'text': 'describe this'},
            {'type': 'image', 'source': {
                'type': 'base64',
                'media_type': 'image/png',
                'data': base64.b64encode(raw).decode('ascii'),
            }},
        ],
    }]
    saved = app.client.get(f'/v1/runs/{run_id(result.stderr)}').json()['request']
    assert saved['context_bytes'] == 131072
    assert saved['tools'] == ['workspace_read']
    assert saved['images'][0]['media_type'] == 'image/png'


def test_cli_chat_can_disable_tools_and_rejects_conflicting_selection(service):
    app, peer = service
    peer.enqueue(Reply())

    result = cli(app, 'chat', 'plain text', '--no-tools')

    assert result.returncode == 0, result.stderr
    assert peer.take_request()['tools'] == []
    invalid = cli(app, 'chat', 'ambiguous', '--no-tools', '--tool', 'workspace_read')
    assert invalid.returncode != 0
    assert 'tool_selection' in invalid.stderr
    assert peer.requests.empty()


def test_cli_detaches_on_approval_then_approves_exact_action_and_lists_receipt(service):
    app, peer = service
    peer.enqueue(write_reply(), Reply(chunks=('Done.',)))

    chat = cli(app, 'chat', 'Create answer.txt')

    assert chat.returncode == 0, chat.stderr
    approval = pending_approval(app)
    assert 'Approval required' in chat.stderr
    assert 'Detached' in chat.stderr
    assert approval['id'] in chat.stderr
    assert 'answer.txt' in chat.stderr and 'hello' in chat.stderr
    assert json.loads(cli(app, 'approval', 'list').stdout) == [approval]

    changed = cli(
        app,
        'approval',
        'approve',
        approval['id'],
        '--arguments-sha256',
        '0' * 64,
        '--policy-sha256',
        approval['policy_sha256'],
    )
    assert changed.returncode != 0
    assert 'approval_changed' in changed.stderr
    assert app.client.get('/v1/approvals').json() == [approval]

    accepted = cli(
        app,
        'approval',
        'approve',
        approval['id'],
        '--arguments-sha256',
        approval['arguments_sha256'],
        '--policy-sha256',
        approval['policy_sha256'],
    )
    assert accepted.returncode == 0, accepted.stderr
    identifier = run_id(chat.stderr)
    assert events(app, identifier)[-1]['data']['status'] == 'succeeded'
    receipts = json.loads(cli(app, 'run', 'receipts', identifier).stdout)
    assert receipts == app.client.get(f'/v1/runs/{identifier}/receipts').json()
    assert receipts[0]['artifacts'][0]['path'] == 'answer.txt'


def test_cli_denies_exact_action_and_grants_only_selected_workspace(service):
    app, peer = service
    peer.enqueue(write_reply('write-denied'))
    chat = cli(app, 'chat', 'Do not create answer.txt')
    approval = pending_approval(app)

    denied = cli(
        app,
        'approval',
        'deny',
        approval['id'],
        '--arguments-sha256',
        approval['arguments_sha256'],
        '--policy-sha256',
        approval['policy_sha256'],
    )

    assert chat.returncode == 0, chat.stderr
    assert denied.returncode == 0, denied.stderr
    assert json.loads(denied.stdout)['error']['code'] == 'approval_denied'
    assert not (app.root / 'workspace' / 'answer.txt').exists()
    assert peer.requests.qsize() == 1

    workspace_result = cli(app, 'workspace')
    assert workspace_result.returncode == 0, workspace_result.stderr
    workspace = json.loads(workspace_result.stdout)
    assert workspace == app.client.get('/v1/workspace').json()
    wrong = cli(app, 'grant', 'write', '--workspace-id', 'changed-workspace')
    assert wrong.returncode != 0 and 'workspace_changed' in wrong.stderr
    granted = cli(app, 'grant', 'execute', '--workspace-id', workspace['id'])
    assert granted.returncode == 0, granted.stderr
    assert json.loads(granted.stdout) == {'workspace_id': workspace['id'], 'capability': 'execute'}
    assert app.client.get('/v1/workspace').json()['grants'] == ['execute']


def test_cli_rejects_invalid_image_before_submitting_a_run(service, tmp_path):
    app, peer = service
    image = tmp_path / 'not-an-image.png'
    image.write_text('not an image')

    result = cli(app, 'chat', 'describe this', '--image', str(image))

    assert result.returncode != 0
    assert 'invalid_image' in result.stderr
    assert peer.requests.empty()
