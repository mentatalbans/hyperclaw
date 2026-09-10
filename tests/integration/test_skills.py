import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from hyperclaw.skills import Skills
from tests.support.process import Process, events, submit
from tests.support.provider import ProviderStub, Reply
from tests.support.tool_provider import frames, message_end, message_start, tool_block


def write_skill(root: Path, body='Follow the documentation rules.'):
    directory = root / 'skills' / 'documentation-answer'
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'SKILL.md').write_text(
        '---\nname: documentation-answer\ndescription: Documentation answers.\n---\n'
        f'{body}\n\nRead [citation rules](rules.txt).\n'
    )
    (directory / 'rules.txt').write_text('Cite the source path.')
    return Skills(root / 'skills').load('documentation-answer')


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
        cwd=app._cwd.name, env={'PATH': os.environ['PATH']}, capture_output=True,
        text=True, timeout=10,
    )


def test_authenticated_preview_admission_and_selected_context_are_isolated(service):
    app, peer = service
    document = write_skill(app.root)
    stranger = app.client.__class__(base_url=app.url, trust_env=False)
    try:
        assert stranger.get('/v1/skills').status_code == 401
    finally:
        stranger.close()

    preview = app.client.get('/v1/skills/documentation-answer')
    assert preview.status_code == 200
    assert preview.json()['content_hash'] == document.content_hash
    listed = app.client.get('/v1/skills').json()
    assert listed == [preview.json() | {'admitted': False}]
    wrong = app.client.post('/v1/skills/documentation-answer/admit',
                            json={'content_hash': '0' * 64})
    assert wrong.status_code == 409
    assert app.client.get('/v1/skills').json()[0]['admitted'] is False
    admitted = app.client.post('/v1/skills/documentation-answer/admit',
                               json={'content_hash': document.content_hash})
    assert admitted.status_code == 200
    assert admitted.json() == preview.json()

    peer.enqueue(Reply(), Reply())
    selected = submit(app, skills=['documentation-answer'])
    selected_events = events(app, selected['id'])
    request = peer.take_request()
    assert 'Follow the documentation rules.' in request['system']
    assert 'Cite the source path.' in request['system']
    assert request['messages'] == [{'role': 'user', 'content': 'hello'}]
    saved = app.client.get(f"/v1/runs/{selected['id']}").json()
    assert saved['skill_hashes'] == {'documentation-answer': document.content_hash}
    context = next(event['data'] for event in selected_events if event['kind'] == 'run.context')
    serialized = json.dumps(request['messages'], sort_keys=True, separators=(',', ':'),
                            ensure_ascii=False).encode()
    assert context['skill_hashes'] == saved['skill_hashes']
    assert context['serialized_bytes'] == len(serialized) + len(request['system'].encode())
    assert context['sha256'] == hashlib.sha256(request['system'].encode() + serialized).hexdigest()

    later = submit(app, request_id='unselected')
    events(app, later['id'])
    assert 'system' not in peer.take_request()


def test_changed_or_revoked_skill_blocks_new_and_queued_runs(service):
    app, peer = service
    document = write_skill(app.root)
    assert app.client.post('/v1/skills/documentation-answer/admit',
                           json={'content_hash': document.content_hash}).status_code == 200
    changed = write_skill(app.root, 'Changed instructions.')
    response = app.client.post('/v1/runs', json={
        'session_id': app.client.post('/v1/sessions').json()['id'], 'generation': 0,
        'request_id': 'changed', 'text': 'hello', 'skills': ['documentation-answer'],
    })
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'skill_not_admitted'
    assert app.client.post('/v1/skills/documentation-answer/admit',
                           json={'content_hash': changed.content_hash}).status_code == 200

    peer.enqueue(Reply())
    run = submit(app, request_id='selected', skills=['documentation-answer'])
    assert app.client.delete('/v1/skills/documentation-answer/admission').status_code == 200
    seen = events(app, run['id'])
    assert seen[-1]['data']['status'] == 'failed'
    assert app.client.get(f"/v1/runs/{run['id']}").json()['error']['code'] == 'skill_not_admitted'
    assert peer.requests.empty()


def test_cli_skill_admin_and_chat_selection(service):
    app, peer = service
    document = write_skill(app.root)
    inspected = cli(app, 'skill', 'inspect', 'documentation-answer')
    assert inspected.returncode == 0, inspected.stderr
    assert json.loads(inspected.stdout)['content_hash'] == document.content_hash
    admitted = cli(app, 'skill', 'admit', 'documentation-answer',
                   '--content-hash', document.content_hash)
    assert admitted.returncode == 0, admitted.stderr
    assert json.loads(cli(app, 'skill', 'list').stdout)[0]['admitted'] is True
    peer.enqueue(Reply())
    chat = cli(app, 'chat', 'answer', '--skill', 'documentation-answer')
    assert chat.returncode == 0, chat.stderr
    assert 'Documentation answers.' in peer.take_request()['system']
    revoked = cli(app, 'skill', 'revoke', 'documentation-answer')
    assert revoked.returncode == 0, revoked.stderr
    assert json.loads(cli(app, 'skill', 'list').stdout)[0]['admitted'] is False


def test_skill_snapshot_survives_disk_edit_across_approval_and_grants_no_tools(service):
    app, peer = service
    document = write_skill(app.root, 'Original reviewed guidance.')
    app.client.post('/v1/skills/documentation-answer/admit',
                    json={'content_hash': document.content_hash}).raise_for_status()
    peer.enqueue(
        Reply(frames=frames(
            message_start(),
            *tool_block(0, 'write-1', 'workspace_write',
                        ('{"path":"answer.txt","content":"hello"}',)),
            *message_end(),
        )),
        Reply(chunks=('Done.',)),
        Reply(frames=frames(
            message_start(),
            *tool_block(0, 'write-disabled', 'workspace_write',
                        ('{"path":"unsafe.txt","content":"unsafe"}',)),
            *message_end(),
        )),
    )
    selected = submit(app, 'write an answer', skills=['documentation-answer'])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        approvals = app.client.get('/v1/approvals').json()
        if approvals:
            break
        time.sleep(.01)
    approval = approvals[0]
    first_request = peer.take_request()
    assert 'Original reviewed guidance.' in first_request['system']
    write_skill(app.root, 'Unreviewed disk edit.')
    app.client.post(f"/v1/approvals/{approval['id']}/decision", json={
        'approved': True,
        'arguments_sha256': approval['arguments_sha256'],
        'policy_sha256': approval['policy_sha256'],
    }).raise_for_status()
    assert events(app, selected['id'])[-1]['data']['status'] == 'succeeded'
    resumed = peer.take_request()
    assert 'Original reviewed guidance.' in resumed['system']
    assert 'Unreviewed disk edit.' not in resumed['system']

    changed = Skills(app.root / 'skills').load('documentation-answer')
    app.client.post('/v1/skills/documentation-answer/admit',
                    json={'content_hash': changed.content_hash}).raise_for_status()
    disabled = submit(app, 'try a write', request_id='disabled',
                      skills=['documentation-answer'], tools=[])
    assert events(app, disabled['id'])[-1]['data']['status'] == 'failed'
    assert app.client.get(f"/v1/runs/{disabled['id']}").json()['error']['code'] == 'tools_disabled'
    assert not (app.root / 'workspace' / 'unsafe.txt').exists()


def test_combined_selected_instructions_must_fit_context_budget(service):
    app, peer = service
    names = []
    for number in range(4):
        name = f'large-{number}'
        directory = app.root / 'skills' / name
        directory.mkdir(parents=True)
        (directory / 'SKILL.md').write_text(
            f'---\nname: {name}\ndescription: Large.\n---\nRead [rules](rules.txt).\n'
        )
        (directory / 'rules.txt').write_text(str(number) * 16_384)
        document = Skills(app.root / 'skills').load(name)
        app.client.post(f'/v1/skills/{name}/admit',
                        json={'content_hash': document.content_hash}).raise_for_status()
        names.append(name)
    session = app.client.post('/v1/sessions').json()
    response = app.client.post('/v1/runs', json={
        'session_id': session['id'], 'generation': 0, 'request_id': 'large',
        'text': 'hello', 'skills': names, 'context_bytes': 65_536,
    })
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'context_limit'
    assert peer.requests.empty()
