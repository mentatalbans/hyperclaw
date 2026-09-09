import asyncio
import json
import threading

import pytest

from hyperclaw.config import load_settings
from hyperclaw.contracts import Message, ProviderFailure
from hyperclaw.ollama import Ollama
from tests.support.provider import MODEL, ProviderStub, Reply


def frame(value):
    return ('event: ' + value['type'] + '\r\ndata: ' + json.dumps(value) + '\r\n\r\n').encode()


START = {'type': 'message_start', 'message': {'type': 'message', 'role': 'assistant', 'model': MODEL, 'content': [], 'stop_reason': None, 'usage': {'input_tokens': 12}}}
BLOCK = {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}}
TEXT = {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'hello'}}
STOP = {'type': 'content_block_stop', 'index': 0}
DELTA = {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {'output_tokens': 4}}
END = {'type': 'message_stop'}
VALID = [START, BLOCK, TEXT, STOP, DELTA, END]


async def collect(tmp_path, reply, **settings):
    peer = ProviderStub()
    peer.enqueue(reply)
    model = Ollama(load_settings(root=tmp_path, environ={}, overrides={'ollama_url': peer.url, 'model': MODEL, **settings}))
    events = []
    error = None
    try:
        try:
            async for event in model.stream([Message(role='user', content='hello')], system='Be brief.'):
                events.append(event)
        except ProviderFailure as exc:
            error = exc
        request = peer.take_request()
        assert peer.requests.empty(), 'No implicit retry'
        return events, error, request
    finally:
        await model.aclose()
        peer.close()


async def test_missing_message_stop_is_a_failure(tmp_path):
    events, error, _ = await collect(tmp_path, Reply(chunks=('partial',), truncate=True))
    assert error.code == 'incomplete_stream'
    assert any(e.kind == 'text' for e in events)
    assert not any(e.kind == 'finish' for e in events)


async def test_text_thinking_usage_and_request_shape(tmp_path):
    events, error, request = await collect(tmp_path, Reply(thinking='consider', chunks=('Hello ', 'world.')), thinking=True, max_output_tokens=77)
    assert error is None
    assert ''.join(e.data['text'] for e in events if e.kind == 'text') == 'Hello world.'
    assert [e.data['text'] for e in events if e.kind == 'thinking'] == ['consider']
    assert [e.data for e in events if e.kind == 'usage'] == [{'model': MODEL, 'input_tokens': 12, 'output_tokens': 4}]
    assert events[-1].kind == 'finish'
    assert request == {'model': MODEL, 'messages': [{'role': 'user', 'content': 'hello'}], 'max_tokens': 77, 'stream': True, 'thinking': {'type': 'enabled', 'budget_tokens': 1024}, 'system': 'Be brief.'}


async def test_split_crlf_frames_and_unknown_usage_counts(tmp_path):
    start = {**START, 'message': {**START['message'], 'usage': {}}}
    delta = {**DELTA, 'usage': {}}
    events, error, request = await collect(tmp_path, Reply(frames=tuple(frame(e) for e in [start, {'type': 'ping'}, BLOCK, TEXT, STOP, delta, END]), split_bytes=3))
    assert error is None
    assert next(e.data for e in events if e.kind == 'usage') == {'model': MODEL, 'input_tokens': None, 'output_tokens': None}
    assert request['thinking'] == {'type': 'disabled'}


@pytest.mark.parametrize('status', [502, 302])
async def test_http_errors_and_redirects_never_retry(tmp_path, status):
    events, error, _ = await collect(tmp_path, Reply(status=status))
    assert error.code == f'http_{status}'
    assert events == []
    assert 'Synthetic' not in error.message


@pytest.mark.parametrize('reason,code', [('max_tokens', 'output_limit'), ('tool_use', 'tools_disabled'), ('mystery', 'invalid_stream')])
async def test_only_end_turn_is_success(tmp_path, reason, code):
    events, error, _ = await collect(tmp_path, Reply(stop_reason=reason))
    assert error.code == code
    assert not any(e.kind == 'finish' for e in events)


@pytest.mark.parametrize('sequence', [
    [BLOCK, *VALID], [START, TEXT, END], [START, BLOCK, BLOCK, END],
    [START, BLOCK, {**TEXT, 'index': 1}, END],
    [START, BLOCK, {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'thinking_delta', 'thinking': 'wrong'}}, END],
    [START, {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'image'}}, END],
    [START, BLOCK, {'type': 'new_content_type'}, END],
    [START, BLOCK, TEXT, DELTA, END], [START, END],
    [START, BLOCK, {**TEXT, 'delta': {'type': 'text_delta', 'text': 22}}, STOP, DELTA, END],
    [START, BLOCK, TEXT, STOP, {**DELTA, 'usage': {'output_tokens': -1}}, END],
])
async def test_malformed_content_is_rejected(tmp_path, sequence):
    events, error, _ = await collect(tmp_path, Reply(frames=tuple(frame(e) for e in sequence)))
    assert error.code == 'invalid_stream'
    assert not any(e.kind == 'finish' for e in events)


async def test_unexpected_tool_block_is_explicit(tmp_path):
    events, error, _ = await collect(tmp_path, Reply(frames=(frame(START), frame({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'tool_use', 'id': 'call', 'name': 'execute', 'input': {}}}))))
    assert error.code == 'tools_disabled'
    assert not any(e.kind == 'finish' for e in events)


@pytest.mark.parametrize('raw,code', [(b'data: not-json\n\n', 'invalid_stream'), (b'data: []\n\n', 'invalid_stream'), (b'data: ' + b'x' * (1024 * 1024) + b'\n\n', 'frame_too_large')])
async def test_non_json_and_oversized_frames(tmp_path, raw, code):
    _, error, _ = await collect(tmp_path, Reply(frames=(raw,)))
    assert error.code == code


async def test_incremental_output_and_cancellation_close_io(tmp_path):
    gate, disconnected = threading.Event(), threading.Event()
    peer = ProviderStub()
    peer.enqueue(Reply(gate=gate, disconnected=disconnected))
    model = Ollama(load_settings(root=tmp_path, overrides={'model': MODEL, 'ollama_url': peer.url}))
    stream = model.stream([Message(role='user', content='hello')])
    try:
        first = await asyncio.wait_for(anext(stream), 2)
        assert first.kind == 'text' and first.data['text'] == 'Hello '
        waiting = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.02)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert await asyncio.to_thread(disconnected.wait, 2)
    finally:
        gate.set()
        await stream.aclose()
        await model.aclose()
        peer.close()


async def test_request_deadline_is_bounded(tmp_path):
    _, error, _ = await collect(tmp_path, Reply(gate=threading.Event()), request_timeout_s=0.1)
    assert error.code == 'request_timeout'
