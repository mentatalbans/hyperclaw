import asyncio
import hashlib
import json
import threading

import pytest

from hyperclaw.config import load_settings
from hyperclaw.contracts import Conflict, Message, RunRequest, StorageFailure
from hyperclaw.ollama import Ollama
from hyperclaw.runtime import Runtime
from hyperclaw.store import Store
from tests.support.provider import MODEL, ProviderStub, Reply


async def open_runtime(root, peer, **overrides):
    settings = load_settings(root=root, environ={}, overrides={'ollama_url': peer.url, 'model': MODEL, **overrides})
    runtime = Runtime(await Store.open(root), Ollama(settings), settings)
    await runtime.start()
    return runtime


async def submit(runtime, text='hello', session=None, request_id='first', retry_of=None):
    session = session or await runtime.create_session()
    return await runtime.submit(RunRequest(session_id=session.id, generation=session.generation,
                                          request_id=request_id, text=text, retry_of=retry_of))


async def observe(runtime, run):
    async with asyncio.timeout(5):
        return [e async for e in runtime.events(run.id)]


async def until_text(runtime, run):
    stream = runtime.events(run.id)
    async with asyncio.timeout(3):
        async for event in stream:
            if event.kind == 'model.text':
                await stream.aclose()
                return event
    raise AssertionError('No incremental text event')


async def test_completed_run_replays_after_restart(tmp_path):
    peer = ProviderStub()
    peer.enqueue(Reply())
    first = await open_runtime(tmp_path, peer)
    try:
        run = await submit(first)
        seen = await observe(first, run)
        assert seen[-1].kind == 'run.finished'
        assert seen[-1].data['status'] == 'succeeded'
    finally:
        await first.close()
    second = await open_runtime(tmp_path, peer)
    try:
        replayed = await observe(second, run)
        assert replayed == seen
        assert (await second.get_run(run.id)).output == 'Hello world.'
        suffix = [e async for e in second.events(run.id, after=seen[2].seq)]
        assert suffix == seen[3:]
        assert [e async for e in second.events(run.id, after=seen[-1].seq)] == []
    finally:
        await second.close()
        peer.close()


async def test_disconnect_queueing_and_active_vs_queued_cancel(tmp_path):
    gate = threading.Event()
    peer = ProviderStub()
    peer.enqueue(Reply(gate=gate))
    runtime = await open_runtime(tmp_path, peer)
    try:
        run = await submit(runtime)
        prefix = await until_text(runtime, run)  # iterator detached while provider is stalled
        assert prefix.data == {'text': 'Hello '}
        assert (await runtime.get_run(run.id)).status == 'running'
        with pytest.raises(Conflict):
            await runtime.submit(run.request.model_copy(update={'request_id': 'competing'}))
        queued = await submit(runtime)
        assert queued.status == 'queued'
        assert (await runtime.cancel(queued.id)).status == 'cancelled'
        assert (await runtime.cancel(run.id)).status == 'cancelled'
        assert (await runtime.cancel(run.id)).status == 'cancelled'
        assert peer.requests.qsize() == 1
        events = await observe(runtime, run)
        assert [e.kind for e in events].count('run.finished') == 1
        assert (await runtime.get_run(run.id)).output is None
    finally:
        gate.set()
        await runtime.close()
        peer.close()


async def test_shutdown_interrupts_active_and_restart_starts_queued(tmp_path):
    gate = threading.Event()
    peer = ProviderStub()
    peer.enqueue(Reply(gate=gate), Reply(chunks=('queued answer',)))
    runtime = await open_runtime(tmp_path, peer)
    run = await submit(runtime)
    await until_text(runtime, run)
    queued = await submit(runtime)
    await runtime.close()
    gate.set()
    runtime = await open_runtime(tmp_path, peer)
    try:
        assert (await runtime.get_run(run.id)).status == 'interrupted'
        assert (await observe(runtime, queued))[-1].data['status'] == 'succeeded'
        assert (await runtime.get_run(queued.id)).output == 'queued answer'
    finally:
        await runtime.close()
        peer.close()


async def test_incomplete_turns_excluded_and_generation_reset(tmp_path):
    peer = ProviderStub()
    peer.enqueue(Reply(chunks=('secret partial',), truncate=True), Reply(chunks=('complete',)), Reply(), Reply())
    runtime = await open_runtime(tmp_path, peer)
    try:
        session = await runtime.create_session()
        failed = await submit(runtime, 'failed prompt', session)
        assert (await observe(runtime, failed))[-1].data['status'] == 'failed'
        peer.take_request()
        retry = await submit(runtime, 'retry prompt', session, 'retry', failed.id)
        await observe(runtime, retry)
        assert peer.take_request()['messages'] == [{'role': 'user', 'content': 'retry prompt'}]
        next_run = await submit(runtime, 'next prompt', session, 'next')
        await observe(runtime, next_run)
        assert peer.take_request()['messages'] == [
            {'role': 'user', 'content': 'retry prompt'}, {'role': 'assistant', 'content': [{'type':'text', 'text':'complete'}]},
            {'role': 'user', 'content': 'next prompt'}]
        reset = await runtime.reset_session(session.id, 0)
        last = await submit(runtime, 'fresh', reset)
        await observe(runtime, last)
        assert peer.take_request()['messages'] == [{'role': 'user', 'content': 'fresh'}]
    finally:
        await runtime.close()
        peer.close()


async def test_context_reserves_current_request_and_whole_turns(tmp_path):
    peer = ProviderStub()
    peer.enqueue(Reply())
    store = await Store.open(tmp_path)
    session = await store.create_session()
    for number in range(3):
        run = await store.submit(RunRequest(session_id=session.id, generation=0, request_id=str(number), text=str(number) * 15000))
        await store.next_run()
        await store.finish(run.id, 'succeeded', output='answer-' + str(number))
    settings = load_settings(root=tmp_path, overrides={'model': MODEL, 'ollama_url': peer.url})
    runtime = Runtime(store, Ollama(settings), settings)
    await runtime.start()
    try:
        current = 'é' * 17000
        run = await submit(runtime, current, session, 'current')
        events = await observe(runtime, run)
        messages = peer.take_request()['messages']
        assert [m['content'] for m in messages] == ['1' * 15000, 'answer-1', '2' * 15000, 'answer-2', current]
        serialized = json.dumps(messages, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        assert len(serialized) <= 65536
        context = next(e.data for e in events if e.kind == 'run.context')
        assert context['sha256'] == hashlib.sha256(serialized).hexdigest()
        assert context['retained_turns'] == 2
    finally:
        await runtime.close()
        peer.close()


async def test_thinking_usage_and_bounded_ordered_chunks(tmp_path):
    peer = ProviderStub()
    peer.enqueue(Reply(thinking='reason', chunks=('é' * 5000,)))
    runtime = await open_runtime(tmp_path, peer)
    try:
        run = await submit(runtime)
        events = await observe(runtime, run)
        chunks = [e.data['text'] for e in events if e.kind == 'model.text']
        assert ''.join(chunks) == 'é' * 5000
        assert all(len(c.encode()) <= 4096 for c in chunks)
        assert next(e.data for e in events if e.kind == 'model.thinking') == {'text': 'reason'}
        assert next(e.data for e in events if e.kind == 'model.usage')['model'] == MODEL
        assert (await runtime.get_run(run.id)).output == 'é' * 5000
    finally:
        await runtime.close()
        peer.close()


async def test_total_deadline_fails_with_inspectable_partial_output(tmp_path):
    gate = threading.Event()
    peer = ProviderStub()
    peer.enqueue(Reply(gate=gate))
    # Allow the real provider and FULL SQLite commits to schedule before the deadline.
    runtime = await open_runtime(tmp_path, peer, run_timeout_s=2.0)
    try:
        run = await submit(runtime)
        events = await observe(runtime, run)
        assert any(e.kind == 'model.text' for e in events)
        assert events[-1].data['status'] == 'failed'
        assert (await runtime.get_run(run.id)).error.code == 'run_timeout'
    finally:
        gate.set()
        await runtime.close()
        peer.close()


async def test_cancellation_completion_has_one_terminal_winner(tmp_path):
    peer = ProviderStub()
    peer.enqueue(Reply())
    runtime = await open_runtime(tmp_path, peer)
    try:
        run = await submit(runtime)
        cancelled, events = await asyncio.gather(runtime.cancel(run.id), observe(runtime, run))
        assert cancelled.status in {'cancelled', 'succeeded'}
        assert events[-1].data['status'] == cancelled.status
        assert [e.kind for e in events].count('run.finished') == 1
    finally:
        await runtime.close()
        peer.close()


async def test_idle_worker_sleeps_and_storage_failure_stops_acceptance(tmp_path):
    peer = ProviderStub()
    runtime = await open_runtime(tmp_path, peer)
    try:
        await asyncio.sleep(0.05)
        assert runtime._worker._fut_waiter is not None
        # A real SQL failure on claim must stop execution instead of producing fake success.
        await runtime.store._call(lambda: runtime.store._db.execute("CREATE TRIGGER fail_claim BEFORE UPDATE ON runs WHEN NEW.status='running' BEGIN SELECT RAISE(ABORT, 'injected'); END"))
        run = await submit(runtime)
        async with asyncio.timeout(3):
            while runtime.healthy:
                await asyncio.sleep(0.01)
        assert (await runtime.get_run(run.id)).status == 'queued'
        with pytest.raises(StorageFailure):
            await runtime.create_session()
        assert peer.requests.empty()
    finally:
        await runtime.close()
        peer.close()


async def test_shutdown_during_admitted_append_does_not_duplicate_text(tmp_path, monkeypatch):
    gate, entered, release = threading.Event(), threading.Event(), threading.Event()
    peer = ProviderStub()
    peer.enqueue(Reply(gate=gate))
    runtime = await open_runtime(tmp_path, peer)
    original = runtime.store._event
    def gated(run_id, kind, data):
        value = original(run_id, kind, data)
        if kind == 'model.text' and not entered.is_set():
            entered.set()
            assert release.wait(5)
        return value
    monkeypatch.setattr(runtime.store, '_event', gated)
    run = await submit(runtime)
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        closing = asyncio.create_task(runtime.close())
        await asyncio.sleep(0.02)
        release.set()
        await closing
        reopened = await Store.open(tmp_path)
        try:
            replay = await reopened.events(run.id)
            assert [e.data['text'] for e in replay if e.kind == 'model.text'] == ['Hello ']
            assert replay[-1].data['status'] == 'interrupted'
        finally:
            await reopened.close()
    finally:
        release.set()
        gate.set()
        await runtime.close()
        peer.close()


async def test_cancel_waits_for_worker_terminal_reconciliation(tmp_path, monkeypatch):
    peer = ProviderStub()
    peer.enqueue(Reply())
    runtime = await open_runtime(tmp_path, peer)
    entered = asyncio.Event()
    original = runtime.store.finish
    async def gated(run_id, status, **kwargs):
        if status == 'succeeded':
            entered.set()
            await asyncio.Event().wait()
        return await original(run_id, status, **kwargs)
    monkeypatch.setattr(runtime.store, 'finish', gated)
    try:
        run = await submit(runtime)
        await asyncio.wait_for(entered.wait(), 3)
        result = await asyncio.wait_for(runtime.cancel(run.id), 3)
        assert result.status == 'cancelled'
        assert (await observe(runtime, run))[-1].data['status'] == 'cancelled'
    finally:
        await runtime.close()
        peer.close()
