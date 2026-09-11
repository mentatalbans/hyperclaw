"""The three M1 real-model acceptance scenarios, through the authenticated HTTP API."""
from uuid import uuid4

import pytest

from tests.support.process import events, sse_events, submit

pytestmark = pytest.mark.ollama


def assert_accounted(app, run, received, model):
    result = app.client.get(f"/v1/runs/{run['id']}").json()
    assert result['status'] == 'succeeded', result
    assert result['output'].strip()
    assert result['verification'] == 'not_requested'
    assert received[-1]['data']['status'] == 'succeeded'
    provenance = next(e['data'] for e in received if e['kind'] == 'run.context')
    assert provenance['model'] == model
    usage = [e['data'] for e in received if e['kind'] == 'model.usage']
    assert len(usage) == 1 and usage[0]['model'] == model
    assert usage[0]['input_tokens'] > 0 and usage[0]['output_tokens'] > 0
    return result['output']


def test_plain_response_has_local_provenance_and_usage(live_app, ollama_target):
    run = submit(live_app, 'Describe a paper kite in one short sentence.', tools=[])
    received = events(live_app, run['id'])
    assert_accounted(live_app, run, received, ollama_target.model)


def test_stream_returns_text_and_accounts_once(live_app, ollama_target, request):
    run = submit(live_app, 'Describe a sailing boat in two short sentences.', tools=[])
    with live_app.client.stream('GET', f"/v1/runs/{run['id']}/events") as response:
        received = list(sse_events(response))
    chunks = [e['data']['text'] for e in received if e['kind'] == 'model.text']
    answer = assert_accounted(live_app, run, received, ollama_target.model)
    assert ''.join(chunks) == answer
    request.node.user_properties.append(('ollama_text_chunks', len(chunks)))


def test_conversation_resumes_without_leaking_to_other_or_reset_sessions(live_app, ollama_target):
    app = live_app
    codeword = 'otter-' + uuid4().hex[:16]
    question = "What is this conversation's synthetic exhibit label? Reply with it only, or UNKNOWN if absent."
    session = app.client.post('/v1/sessions', json={}).json()
    first = submit(app, f"This conversation's synthetic exhibit label is {codeword}. Reply with it only.", session, tools=[])
    assert codeword in assert_accounted(app, first, events(app, first['id']), ollama_target.model)
    app.restart()
    resumed = submit(app, question, session, 'resumed', tools=[])
    assert codeword in assert_accounted(app, resumed, events(app, resumed['id']), ollama_target.model)
    other = submit(app, question, tools=[])
    assert codeword not in assert_accounted(app, other, events(app, other['id']), ollama_target.model)
    reset = app.client.post(f"/v1/sessions/{session['id']}/reset", json={'generation': 0}).json()
    app.restart()
    fresh = submit(app, question, reset, tools=[])
    assert codeword not in assert_accounted(app, fresh, events(app, fresh['id']), ollama_target.model)


def test_synthetic_image_color_through_runtime(live_app, ollama_target):
    import base64
    from tests.support.images import png
    image = {'media_type':'image/png', 'data':base64.b64encode(png()).decode()}
    run = submit(live_app, 'What single primary color fills this image? Reply with the color only.', images=[image], tools=[])
    answer = assert_accounted(live_app,run,events(live_app,run['id']),ollama_target.model)
    assert 'red' in answer.lower(), answer


def test_live_tool_reads_opaque_workspace_marker_and_writes_verified_artifact(live_app):
    marker = 'exhibit-' + uuid4().hex
    (live_app.root/'workspace/marker.txt').write_text(marker)
    workspace = live_app.client.get('/v1/workspace').json()
    assert live_app.client.post('/v1/grants',json={'workspace_id':workspace['id'],'capability':'write'}).status_code == 200
    run = submit(live_app, 'Use workspace_read to read marker.txt, then use workspace_write to copy its exact content into result.txt. Finish with a brief confirmation.', tools=['workspace_read','workspace_write'])
    received = events(live_app,run['id'])
    result = live_app.client.get(f"/v1/runs/{run['id']}").json()
    assert result['status'] == 'succeeded', result
    assert result['verification'] == 'passed', result
    assert (live_app.root/'workspace/result.txt').read_text() == marker
    import hashlib
    receipts = live_app.client.get(f"/v1/runs/{run['id']}/receipts").json()
    assert any(a['sha256'] == hashlib.sha256(marker.encode()).hexdigest() for receipt in receipts for a in receipt['artifacts'])
    assert received[-1]['data']['status'] == 'succeeded'
