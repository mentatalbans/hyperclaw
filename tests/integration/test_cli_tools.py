import base64
import json
import os
from pathlib import Path
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


def cli_with_launcher(app, launcher, *args):
    return subprocess.run(
        [sys.executable, str(launcher), '--root', str(app.root), *args],
        cwd=app._cwd.name,
        env={'PATH': os.environ['PATH']},
        capture_output=True,
        text=True,
        timeout=10,
    )


def assert_cli_omits_credential(result, credential):
    if credential in result.stdout or credential in result.stderr:
        raise AssertionError('operator token appeared in CLI stdout or stderr')


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


def test_cli_wrong_token_is_nonzero_actionable_and_creates_no_session(service):
    app, peer = service
    token_path = app.root / 'token'
    token = token_path.read_text()
    wrong_token = ('0' if token.strip() != '0' * 64 else '1') * 64
    before = app.client.get('/v1/sessions').json()
    token_path.write_text(wrong_token + '\n')
    try:
        result = cli(app, 'chat', 'must not be accepted')
    finally:
        token_path.write_text(token)

    assert result.returncode != 0
    assert 'unauthorized' in result.stderr and 'operator bearer token' in result.stderr.lower()
    assert_cli_omits_credential(result, token.strip())
    assert_cli_omits_credential(result, wrong_token)
    assert app.client.get('/v1/sessions').json() == before
    assert peer.requests.empty()


def test_cli_chat_reports_real_stale_generation_race_without_provider_effect(service):
    app, peer = service
    session = app.client.post('/v1/sessions').json()
    launcher = Path(__file__).parents[1] / 'support' / 'cli_generation_race.py'
    token = (app.root / 'token').read_text().strip()

    result = cli_with_launcher(
        app, launcher, 'chat', 'stale request', '--session', session['id']
    )

    assert result.returncode != 0
    assert 'stale_generation' in result.stderr and 'generation changed' in result.stderr.lower()
    assert app.client.get(f"/v1/sessions/{session['id']}").json()['generation'] == 1
    assert app.client.get(f"/v1/sessions/{session['id']}/runs").json() == []
    assert_cli_omits_credential(result, token)
    assert peer.requests.empty()


def test_cli_failed_foreground_chat_is_nonzero_and_preserves_actionable_error(service):
    app, peer = service
    peer.enqueue(Reply(status=502))
    token = (app.root / 'token').read_text().strip()

    result = cli(app, 'chat', 'surface the synthetic provider failure')

    assert result.returncode != 0
    assert 'failed' in result.stderr and 'http_502' in result.stderr
    assert 'Model endpoint returned HTTP 502' in result.stderr
    assert_cli_omits_credential(result, token)
    assert peer.requests.qsize() == 1
    assert list((app.root / 'workspace').iterdir()) == []


def test_cli_missing_docker_reports_uncertain_run_actionably_and_never_retries(tmp_path):
    peer = ProviderStub()
    empty_path = tmp_path / 'empty-path'
    empty_path.mkdir()
    app = Process(tmp_path / 'runtime', peer.url, environment={'PATH': str(empty_path)})
    try:
        app.start()
        workspace = app.client.get('/v1/workspace').json()
        granted = cli(app, 'grant', 'execute', '--workspace-id', workspace['id'])
        assert granted.returncode == 0, granted.stderr
        peer.enqueue(Reply(frames=frames(
            message_start(),
            *tool_block(0, 'missing-docker-command', 'command', ('{"argv":["true"]}',)),
            *message_end(),
        )))
        token = (app.root / 'token').read_text().strip()

        result = cli(app, 'chat', 'run the unavailable command')

        assert result.returncode != 0
        identifier = run_id(result.stderr)
        inspected = cli(app, 'run', 'inspect', identifier)
        assert inspected.returncode == 0, inspected.stderr
        assert json.loads(inspected.stdout)['status'] == 'uncertain'
        assert 'uncertain' in result.stderr
        assert 'Docker is unavailable' in result.stderr
        assert_cli_omits_credential(result, token)
        assert peer.requests.qsize() == 1
        receipts = app.client.get(f'/v1/runs/{identifier}/receipts').json()
        assert len(receipts) == 1 and receipts[0]['status'] == 'uncertain'
        assert list((app.root / 'workspace').iterdir()) == []
    finally:
        app.stop()
        peer.close()
