import pytest
from importlib import import_module

def telegram_module():
    return import_module("hyperclaw.telegram")


def test_telegram_normalization_rejects_forged_sender_shapes():
    telegram = telegram_module()
    base = {'update_id': 1, 'message': {'message_id': 3, 'date': 1,
        'chat': {'id': -10, 'type': 'supergroup'}, 'from': {'id': 20, 'is_bot': False}, 'text': 'secret'}}
    for change in ({'sender_chat': {'id': -10}}, {'from': {'id': 20, 'is_bot': True}},
                   {'document': {'file_id': 'x'}}, {'message_thread_id': True}):
        value = base | {'message': base['message'] | change}
        update = telegram.TelegramUpdate.from_payload(value)
        assert update.kind == 'rejected'
        assert 'secret' not in update.model_dump_json()

import asyncio
import logging
import sqlite3

from hyperclaw.config import Settings, initialize_root
from hyperclaw.ollama import Ollama
from hyperclaw.runtime import Runtime
from hyperclaw.store import Store
from tests.support.provider import ProviderStub, Reply
from tests.support.telegram_peer import TOKEN, TelegramPeer, update


@pytest.fixture
async def stack(tmp_path):
    peer, provider = TelegramPeer(), ProviderStub()
    settings = Settings(root=tmp_path / 'root', ollama_url=provider.url, model='process-test-model',
                        telegram_enabled=True, telegram_allowed_pairs=['-10:20'])
    initialize_root(settings)
    token = settings.root / 'telegram-token'
    token.write_text(TOKEN + '\n')
    token.chmod(0o600)
    store = await Store.open(settings.root)
    runtime = Runtime(store, Ollama(settings), settings)
    await runtime.start()
    adapter = telegram_module().TelegramAdapter(runtime, settings, peer.client())
    try:
        yield adapter, peer, provider, store, runtime
    finally:
        await adapter.close()
        await runtime.close()
        peer.close()
        provider.close()


async def eventually(predicate):
    for _ in range(400):
        value = await predicate()
        if value:
            return value
        await asyncio.sleep(.02)
    raise AssertionError('Telegram condition did not settle')


async def test_authorization_precedes_photo_io_and_journals_only_safe_summary(stack):
    adapter, peer, provider, store, runtime = stack
    await adapter.start()
    normalized = telegram_module().TelegramUpdate.from_payload(update(sender=99, caption='PRIVATE REJECTED TEXT',
        photo=[{'file_id': 'abc', 'width': 50, 'height': 50, 'file_size': 50}]))
    before = [request for request in peer.requests if request[0] != 'getUpdates']
    await adapter.handle(normalized)
    assert [request for request in peer.requests if request[0] != 'getUpdates'] == before
    assert provider.requests.empty()
    assert await store.sessions() == []
    journal = await store.telegram_update(123456, 1)
    assert journal['status'] == 'rejected'
    assert 'PRIVATE REJECTED TEXT' not in str(journal)
    assert 'PRIVATE REJECTED TEXT' not in str(await adapter.status())


async def test_deduplication_conflict_and_plain_terminal_delivery(stack, caplog):
    adapter, peer, provider, store, runtime = stack
    caplog.set_level(logging.INFO, logger='httpx')
    provider.enqueue(Reply(chunks=('Synthetic <b>reply</b>',)))
    await adapter.start()
    normal = telegram_module().TelegramUpdate.from_payload(update())
    await adapter.handle(normal)
    first = await store.telegram_update(123456, 1)
    await adapter.handle(normal)
    second = await store.telegram_update(123456, 1)
    assert first['run_id'] == second['run_id']
    assert len(await store.session_runs(first['session_id'])) == 1
    async def sent():
        status = await adapter.status()
        return status['deliveries'] and status['deliveries'][0]['status'] == 'sent'
    await eventually(sent)
    sends = [body for method, body in peer.requests if method == 'sendMessage']
    assert len(sends) == 1
    assert sends[0]['chat_id'] == -10 and sends[0]['reply_parameters'] == {'message_id': 101}
    assert 'parse_mode' not in sends[0]
    assert 'Synthetic <b>reply</b>' in sends[0]['text']
    assert first['run_id'] in sends[0]['text']
    from hyperclaw.contracts import Conflict
    with pytest.raises(Conflict, match='changed'):
        await adapter.handle(telegram_module().TelegramUpdate.from_payload(update(text='changed')))
    assert (await store.telegram_update(123456, 1))['conflict'] == 1
    assert len(await store.session_runs(first['session_id'])) == 1
    assert TOKEN not in caplog.text


@pytest.mark.parametrize('method,response', [
    ('getMe', {'ok': True, 'result': {'id': 888, 'is_bot': True}}),
    ('getWebhookInfo', {'ok': True, 'result': {'url': 'https://existing.example'}}),
    ('getMe', {'ok': False, 'description': TOKEN}),
    ('getMe', b'not json'), ('getMe', b' ' * (1024 * 1024 + 1)),
    ('getMe', (200, {'Content-Length': None}, b' ' * (1024 * 1024 + 1))),
], ids=['identity', 'webhook', 'api-error', 'malformed', 'oversized', 'streamed-oversized'])
async def test_startup_refuses_bad_identity_webhook_or_untrusted_response(stack, method, response, caplog):
    from hyperclaw.contracts import InvalidRequest
    adapter, peer, *_ = stack
    caplog.set_level(logging.INFO, logger='httpx')
    peer.responses[method] = response
    with pytest.raises(InvalidRequest) as caught:
        await adapter.start()
    assert TOKEN not in str(caught.value) + caplog.text + str(await adapter.status())
    assert all(method not in {'deleteWebhook', 'setWebhook', 'getUpdates'} for method, _ in peer.requests)


async def test_photo_uses_validated_attachment_and_explicit_context_budget(stack):
    adapter, peer, provider, store, runtime = stack
    provider.enqueue(Reply())
    await adapter.start()
    await adapter.handle(telegram_module().TelegramUpdate.from_payload(update(caption='image caption',
        photo=[{'file_id': 'small', 'width': 5, 'height': 5}, {'file_id': 'large', 'width': 50, 'height': 50}])))
    row = await store.telegram_update(123456, 1)
    run = await runtime.get_run(row['run_id'])
    assert run.request.context_bytes == 8 * 1024 * 1024
    assert run.request.images[0].media_type == 'image/png'
    assert run.request.text == 'image caption'
    assert ('getFile', {'file_id': 'large'}) in peer.requests
    assert not run.request.skills
    from hyperclaw.contracts import DEFAULT_TOOLS
    assert run.request.tools == DEFAULT_TOOLS


@pytest.mark.parametrize('path', ['../evil', '/absolute', 'https://evil.example/x', 'photos/../x',
                                 'photos/%2fsecret', 'photos/x?token=1', 'photos/x#fragment', 'photos\\x'])
async def test_untrusted_photo_path_never_downloaded(stack, path):
    adapter, peer, provider, store, runtime = stack
    peer.responses['getFile'] = {'ok': True, 'result': {'file_id': 'abc', 'file_path': path}}
    await adapter.start()
    await adapter.handle(telegram_module().TelegramUpdate.from_payload(update(photo=[{'file_id': 'abc', 'width': 1, 'height': 1}])))
    assert (await store.telegram_update(123456, 1))['status'] == 'rejected'
    assert not any(method == 'download' for method, _ in peer.requests)
    assert provider.requests.empty()


@pytest.mark.parametrize('response', [b'invalid image', b'x' * (4 * 1024 * 1024 + 1),
    (302, {'Location': 'https://evil.example'}, b''),
    (200, {'Content-Encoding': 'gzip'}, b'compressed data'),
    (200, {'Content-Length': None}, b'x' * (4 * 1024 * 1024 + 1))],
    ids=['invalid', 'lying-size', 'redirect', 'encoding', 'streamed-oversized'])
async def test_photo_content_download_limits_and_redirects(stack, response):
    adapter, peer, provider, store, runtime = stack
    peer.responses['download'] = response
    await adapter.start()
    await adapter.handle(telegram_module().TelegramUpdate.from_payload(update(photo=[{'file_id': 'abc', 'width': 1, 'height': 1}])))
    assert (await store.telegram_update(123456, 1))['status'] == 'rejected'
    assert provider.requests.empty()


@pytest.mark.parametrize('response,want', [({'ok': False, 'description': TOKEN}, 'failed'),
    (b'broken', 'uncertain'), ({'ok': True, 'result': {'message_id': True, 'chat': {'id': -10}}}, 'uncertain')],
    ids=['rejection', 'malformed-receipt', 'invalid-id'])
async def test_outbound_failure_is_inspectable_and_never_retried(stack, response, want):
    adapter, peer, provider, store, runtime = stack
    peer.responses['sendMessage'] = response
    provider.enqueue(Reply())
    await adapter.start()
    await adapter.handle(telegram_module().TelegramUpdate.from_payload(update()))
    async def settled():
        values = (await adapter.status())['deliveries']
        return values and values[0]['status'] == want
    await eventually(settled)
    await asyncio.sleep(.25)
    assert sum(method == 'sendMessage' for method, _ in peer.requests) == 1
    assert TOKEN not in str(await adapter.status())


async def test_reconcile_reset_and_all_session_key_components(stack):
    from hyperclaw.contracts import RunRequest
    adapter, peer, provider, store, runtime = stack
    await adapter.start()
    # Pause polling to exercise the transaction boundaries deterministically.
    adapter._tasks[0].cancel()
    await asyncio.gather(adapter._tasks[0], return_exceptions=True)
    normal = telegram_module().TelegramUpdate.from_payload(update())
    row = await store.telegram_reserve(123456, normal, True)
    request = RunRequest(session_id=row['session_id'], generation=0, request_id='telegram:123456:1', text='hello')
    await store.telegram_prepare(123456, 1, request)
    run = await store.submit(request)
    await store.next_run()
    await store.finish(run.id, 'succeeded', output='before reset')
    await store.reset_session(row['session_id'], 0)
    assert await store.telegram_reconcile(123456, 1) == run.id
    await adapter.handle(normal)
    assert len(await store.session_runs(row['session_id'])) == 1
    next_row = await store.telegram_reserve(123456, telegram_module().TelegramUpdate.from_payload(update(2)), True)
    await store.reset_session(row['session_id'], 1)
    await adapter.handle(telegram_module().TelegramUpdate.from_payload(update(2)))
    assert (await store.telegram_update(123456, 2))['error'] == 'stale_generation'
    assert next_row['generation'] == 1
    rows = [await store.telegram_reserve(bot, telegram_module().TelegramUpdate.from_payload(payload), True)
            for bot, payload in [(888, update(1)), (123456, update(3, chat=-11)),
                                 (123456, update(4, sender=21)), (123456, update(5, topic=7))]]
    assert len({row['session_id'], *(r['session_id'] for r in rows)}) == 5


async def test_busy_session_and_revoked_delivery_do_not_reexecute(stack):
    import threading
    adapter, peer, provider, store, runtime = stack
    gate = threading.Event()
    provider.enqueue(Reply(gate=gate))
    await adapter.start()
    await adapter.handle(telegram_module().TelegramUpdate.from_payload(update()))
    await adapter.handle(telegram_module().TelegramUpdate.from_payload(update(2)))
    assert (await store.telegram_update(123456, 2))['error'] == 'session_busy'
    adapter.settings = adapter.settings.model_copy(update={'telegram_allowed_pairs': ['-11:20']})
    gate.set()
    async def failed():
        rows = (await adapter.status())['deliveries']
        return rows and rows[0]['error'] == 'telegram_authorization_revoked'
    await eventually(failed)
    assert not any(method == 'sendMessage' for method, _ in peer.requests)
    assert provider.requests.qsize() == 1


async def test_uncertain_runtime_result_gets_terminal_notice(stack):
    adapter, peer, provider, store, runtime = stack
    await adapter.start()
    # Boundaries use real Store state; no model needed for an already completed ambiguous effect.
    normal = telegram_module().TelegramUpdate.from_payload(update())
    row = await store.telegram_reserve(123456, normal, True)
    from hyperclaw.contracts import RunRequest
    request = RunRequest(session_id=row['session_id'], generation=0, request_id=row['request_id'], text='hello')
    await store.telegram_prepare(123456, 1, request)
    run = await store.submit(request)
    await store.next_run()
    await store.finish(run.id, 'uncertain')
    await store.telegram_reconcile(123456, 1)
    async def delivered():
        return (await adapter.status())['deliveries']
    assert (await eventually(delivered))[0]['status'] in {'sending', 'sent'}

import json
from pathlib import Path
import subprocess
import sys
import time
import httpx

from tests.support.process import Process


@pytest.fixture
def process_stack(tmp_path):
    peer, provider = TelegramPeer(), ProviderStub()
    app = Process(tmp_path / 'root', provider.url, launcher=Path(__file__).parents[1] / 'support' / 'telegram_daemon.py')
    config = app.root / 'config.toml'
    config.write_text(config.read_text().replace('telegram_enabled = false', 'telegram_enabled = true')
                      .replace('telegram_allowed_pairs = []', 'telegram_allowed_pairs = ["-10:20"]'))
    token = app.root / 'telegram-token'
    token.write_text(TOKEN + '\n')
    token.chmod(0o600)
    (app.root / 'test-telegram-url').write_text(peer.url)
    (app.root / 'test-telegram-stage').write_text('none')
    try:
        yield app, peer, provider
    finally:
        app.stop()
        peer.close()
        provider.close()


def wait_sync(predicate):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.025)
    raise AssertionError('Telegram process condition did not settle')


def test_public_telegram_status_is_authenticated_and_cli_uses_daemon(process_stack):
    app, peer, provider = process_stack
    app.start()
    with httpx.Client(base_url=app.url, trust_env=False) as anonymous:
        assert anonymous.get('/v1/telegram').status_code == 401
    response = app.client.get('/v1/telegram')
    assert response.status_code == 200
    assert response.json()['enabled'] is True
    assert response.json()['bot_id'] == 123456
    result = subprocess.run([sys.executable, '-m', 'hyperclaw', '--root', str(app.root), 'telegram', 'status'],
                            capture_output=True, text=True, cwd=app._cwd.name, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['bot_id'] == 123456
    assert TOKEN not in result.stdout + result.stderr + app.diagnostics()
    assert provider.requests.empty()


@pytest.mark.parametrize('attempt', [1, 2])
@pytest.mark.parametrize('stage', ['reserve', 'submit', 'send'])
def test_sigkill_reopen_never_reexecutes_or_resends(process_stack, stage, attempt):
    app, peer, provider = process_stack
    (app.root / 'test-telegram-stage').write_text(stage)
    provider.enqueue(Reply())
    if stage == 'send':
        peer.send_gate.clear()
    app.start()
    peer.updates = [update()]
    if stage == 'send':
        assert peer.send_observed.wait(10)
    else:
        wait_sync(lambda: (app.root / 'test-telegram-observed').exists())
    # Actual SIGKILL, Store reopen, and real public API. Repeat independently for each gate.
    app.kill()
    with sqlite3.connect(app.root / 'runtime.sqlite3') as db:
        assert db.execute('SELECT count(*) FROM telegram_updates').fetchone()[0] == 1
        assert db.execute('SELECT next_offset FROM telegram_cursors').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM runs').fetchone()[0] == (0 if stage == 'reserve' else 1)
        if stage == 'send':
            assert db.execute('SELECT status FROM telegram_deliveries').fetchone()[0] == 'sending'
    peer.send_gate.set()
    app.start()
    def settled():
        status = app.client.get('/v1/telegram').json()
        deliveries = status['deliveries']
        return status if deliveries and deliveries[0]['status'] in {'sent', 'uncertain'} else None
    status = wait_sync(settled)
    assert status['updates'][0]['status'] == 'accepted'
    assert status['deliveries'][0]['status'] == ('uncertain' if stage == 'send' else 'sent')
    assert len(app.client.get('/v1/sessions').json()) == 1
    session_id = status['updates'][0]['session_id']
    runs = app.client.get(f'/v1/sessions/{session_id}/runs').json()
    assert len(runs) == 1
    assert runs[0]['id'] == status['updates'][0]['run_id']
    time.sleep(.3)
    assert sum(method == 'sendMessage' for method, _ in peer.requests) == 1
    assert provider.requests.qsize() <= 1
    polls = [body for method, body in peer.requests if method == 'getUpdates']
    assert any(body['offset'] == 2 for body in polls)
    assert all(body['allowed_updates'] == ['message'] and body['limit'] == 25 and body['timeout'] == 20 for body in polls)
    assert TOKEN not in app.diagnostics() + str(status)


async def test_waiting_approval_notice_is_independent_and_intake_keeps_polling(stack):
    from tests.support.tool_provider import frames, message_start, tool_block, message_end
    adapter, peer, provider, store, runtime = stack
    provider.enqueue(Reply(frames=frames(message_start(), *tool_block(0, 'write-once', 'workspace_write',
        ('{"path":"telegram-test.txt","content":"must require operator"}',)), *message_end())), Reply())
    await adapter.start()
    peer.updates = [update()]
    async def approval_notice():
        values = (await adapter.status())['deliveries']
        return values and values[0]['phase'] == 'approval' and values[0]['status'] == 'sent'
    await eventually(approval_notice)
    first = await store.telegram_update(123456, 1)
    assert (await runtime.get_run(first['run_id'])).status == 'waiting_approval'
    assert not (adapter.settings.root / 'workspace' / 'telegram-test.txt').exists()
    # A new topic routes independently while the first run awaits exact local authorization.
    peer.updates.append(update(2, topic=8))
    async def second_delivered():
        values = (await adapter.status())['deliveries']
        return any(value['update_id'] == 2 and value['status'] == 'sent' for value in values)
    await eventually(second_delivered)
    assert len(await runtime.approvals()) == 1
    await runtime.cancel(first['run_id'])
    async def terminal_notice():
        values = (await adapter.status())['deliveries']
        return any(value['update_id'] == 1 and value['phase'] == 'terminal' and value['status'] == 'sent' for value in values)
    await eventually(terminal_notice)
    phases = [(value['update_id'], value['phase']) for value in (await adapter.status())['deliveries']]
    assert phases.count((1, 'approval')) == phases.count((1, 'terminal')) == 1
    assert not (adapter.settings.root / 'workspace' / 'telegram-test.txt').exists()
    assert all('grant' not in method.lower() and 'approval' not in method.lower() for method, _ in peer.requests)


async def test_poll_failure_keeps_runtime_available_and_status_is_sanitized(stack):
    adapter, peer, provider, store, runtime = stack
    peer.responses['getUpdates'] = {'ok': False, 'description': TOKEN}
    provider.enqueue(Reply())
    await adapter.start()
    async def error_visible():
        return (await adapter.status())['error'] == 'telegram_rejected'
    await eventually(error_visible)
    session = await runtime.create_session()
    from hyperclaw.contracts import RunRequest
    run = await runtime.submit(RunRequest(session_id=session.id, generation=0, request_id='local', text='local work'))
    async for _ in runtime.events(run.id):
        pass
    assert (await runtime.get_run(run.id)).status == 'succeeded'
    assert TOKEN not in str(await adapter.status())
    assert sum(method == 'getUpdates' for method, _ in peer.requests) == 1


def test_configuration_revocation_after_sigkill_prevents_pending_send(process_stack):
    app, peer, provider = process_stack
    (app.root / 'test-telegram-stage').write_text('submit')
    provider.enqueue(Reply())
    app.start()
    peer.updates = [update()]
    wait_sync(lambda: (app.root / 'test-telegram-observed').exists())
    app.kill()
    config = app.root / 'config.toml'
    config.write_text(config.read_text().replace('["-10:20"]', '["-11:20"]'))
    app.start()
    def rejected():
        values = app.client.get('/v1/telegram').json()['deliveries']
        return values and values[0]['status'] == 'failed'
    wait_sync(rejected)
    assert not any(method == 'sendMessage' for method, _ in peer.requests)
    assert app.client.get('/v1/telegram').json()['deliveries'][0]['error'] == 'telegram_authorization_revoked'


def test_disabled_daemon_status_without_telegram_credential(tmp_path):
    provider = ProviderStub()
    app = Process(tmp_path / 'root', provider.url)
    try:
        app.start()
        status = app.client.get('/v1/telegram').json()
        assert status == {'enabled': False, 'bot_id': None, 'error': None, 'updates': [], 'deliveries': []}
        assert not (app.root / 'telegram-token').exists()
        assert provider.requests.empty()
    finally:
        app.stop()
        provider.close()


async def test_recovery_does_not_adopt_a_different_request_with_same_key(stack):
    from hyperclaw.contracts import RunRequest
    adapter, peer, provider, store, runtime = stack
    await adapter.start()
    adapter._tasks[0].cancel()
    await asyncio.gather(adapter._tasks[0], return_exceptions=True)
    normal = telegram_module().TelegramUpdate.from_payload(update())
    row = await store.telegram_reserve(123456, normal, True)
    expected = RunRequest(session_id=row['session_id'], generation=0, request_id=row['request_id'], text='hello')
    await store.telegram_prepare(123456, 1, expected)
    # Local API request IDs are caller-selected; another payload must never be silently adopted.
    other = await store.submit(expected.model_copy(update={'text': 'different local request'}))
    await adapter.handle(normal)
    journal = await store.telegram_update(123456, 1)
    assert journal['run_id'] is None
    assert journal['status'] == 'rejected' and journal['error'] == 'telegram_request_conflict'
    assert len(await store.session_runs(row['session_id'])) == 1
    assert (await store.get_run(other.id)).request.text == 'different local request'


async def test_revocation_between_getfile_and_download_stops_photo_io(stack):
    import threading
    adapter, peer, provider, store, runtime = stack
    peer.gates['getFile'] = threading.Event()
    await adapter.start()
    task = asyncio.create_task(adapter.handle(telegram_module().TelegramUpdate.from_payload(
        update(photo=[{'file_id': 'abc', 'width': 1, 'height': 1}]))))
    try:
        async def requested():
            return any(method == 'getFile' for method, _ in peer.requests)
        await eventually(requested)
        adapter.settings = adapter.settings.model_copy(update={'telegram_allowed_pairs': ['-11:20']})
        peer.gates['getFile'].set()
        await task
        assert not any(method == 'download' for method, _ in peer.requests)
        assert provider.requests.empty()
        assert (await store.telegram_update(123456, 1))['error'] == 'telegram_authorization_revoked'
    finally:
        peer.gates['getFile'].set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_rejected_updates_commit_offsets_and_status_is_bounded(stack):
    adapter, peer, provider, store, runtime = stack
    await adapter.start()
    peer.updates = [{'update_id': 7, 'edited_message': update()['message']}, update(8, sender=99, text='private rejected marker')]
    async def committed():
        return await store.telegram_offset(123456) == 9
    await eventually(committed)
    assert provider.requests.empty()
    assert await store.sessions() == []
    for number in range(9, 64):
        await adapter.handle(telegram_module().TelegramUpdate.from_payload(update(number, sender=99)))
    status = await adapter.status()
    assert len(status['updates']) == 50
    assert status['updates'][0]['update_id'] == 63
    assert 'private rejected marker' not in str(status)
    assert (await store.telegram_update(123456, 7))['normalized_json'] is None


@pytest.mark.parametrize('phase', ['poll', 'sent', 'uncertain'])
async def test_shutdown_stops_cycles_when_transport_suppresses_cancellation(stack, monkeypatch, phase):
    from hyperclaw.telegram_transport import TelegramFailure
    adapter, peer, provider, store, runtime = stack
    entered, cancelled = asyncio.Event(), asyncio.Event()
    await adapter.start()
    original = adapter.transport.call
    target = 'getUpdates' if phase == 'poll' else 'sendMessage'
    held = False

    async def suppress_once(method, body=None):
        nonlocal held
        failure = None
        try:
            result = await original(method, body)
        except TelegramFailure as exc:
            result, failure = None, exc
        if method == target and not held:
            held = True
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # Model the observed lower-layer suppression after actual local HTTP IO.
                cancelled.set()
        if failure is not None:
            raise failure
        return result

    monkeypatch.setattr(adapter.transport, 'call', suppress_once)
    closing = None
    try:
        if phase != 'poll':
            if phase == 'uncertain':
                peer.responses['sendMessage'] = b'malformed receipt'
            provider.enqueue(Reply(), Reply())
            await adapter.handle(telegram_module().TelegramUpdate.from_payload(update()))
            await adapter.handle(telegram_module().TelegramUpdate.from_payload(update(2, topic=7)))
        await asyncio.wait_for(entered.wait(), 3)
        requests_before_close = sum(method == target for method, _ in peer.requests)
        closing = asyncio.create_task(adapter.close())
        await asyncio.wait_for(cancelled.wait(), 3)
        done, _ = await asyncio.wait({closing}, timeout=.5)
        assert closing in done, 'Adapter shutdown kept waiting after transport suppressed cancellation'
        await closing
        assert sum(method == target for method, _ in peer.requests) == requests_before_close
        assert all(task.done() for task in adapter._tasks)
        assert adapter.transport.client.is_closed
        if phase != 'poll':
            deliveries = (await adapter.status())['deliveries']
            assert len(deliveries) == 1
            assert deliveries[0]['status'] == phase
    finally:
        # On RED, release the original unbounded loop using a second ordinary cancellation.
        for task in adapter._tasks:
            task.cancel()
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)


async def test_cancelled_close_waits_for_owned_transport_cleanup(stack, monkeypatch):
    adapter, peer, provider, store, runtime = stack
    await adapter.start()
    entered, release = asyncio.Event(), asyncio.Event()
    original = adapter.transport.close
    async def delayed_close():
        entered.set()
        await release.wait()
        await original()
    monkeypatch.setattr(adapter.transport, 'close', delayed_close)
    closing = asyncio.create_task(adapter.close())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        closing.cancel()
        done, _ = await asyncio.wait({closing}, timeout=.05)
        assert closing not in done, 'Cancelled close returned while its owned transport was still open'
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert adapter.transport.client.is_closed
        assert all(task.done() for task in adapter._tasks)
    finally:
        release.set()
        for task in adapter._tasks:
            task.cancel()
        await asyncio.gather(closing, return_exceptions=True)
