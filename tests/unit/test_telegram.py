import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from hyperclaw.config import Settings
from hyperclaw.contracts import InvalidRequest

TOKEN = '123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi'


def telegram_module():
    import hyperclaw
    assert (Path(hyperclaw.__file__).parent / 'telegram.py').exists(), 'Telegram adapter is missing'
    from hyperclaw import telegram
    return telegram


@pytest.mark.parametrize('pairs', [[], ['0:1'], ['1:0'], ['+1:2'], ['01:2'], ['1:02'], ['-01:2'], ['1:-2'], ['1:2 ', '1:2'], ['1:2', '1:2'], ['4503599627370496:2'], [True]])
def test_enabled_configuration_rejects_noncanonical_or_empty_pairs(tmp_path, pairs):
    with pytest.raises(ValidationError):
        Settings(root=tmp_path, telegram_enabled=True, telegram_allowed_pairs=pairs)


def test_valid_configuration_and_disabled_default(tmp_path):
    settings = Settings(root=tmp_path)
    assert getattr(settings, 'telegram_enabled', None) is False
    enabled = Settings(root=tmp_path, telegram_enabled=True, telegram_allowed_pairs=['-123:45', '1:2'])
    assert enabled.telegram_allowed_pairs == ['-123:45', '1:2']
    assert TOKEN not in enabled.model_dump_json()


@pytest.mark.parametrize('kind', ['missing', 'permissions', 'symlink', 'hardlink', 'directory', 'fifo', 'oversize', 'malformed'])
def test_private_credential_rejects_unsafe_files_without_disclosing_secret(tmp_path, kind):
    telegram = telegram_module()
    path = tmp_path / 'telegram-token'
    if kind == 'directory':
        path.mkdir()
    elif kind == 'fifo':
        os.mkfifo(path, 0o600)
    elif kind != 'missing':
        path.write_text(TOKEN if kind != 'oversize' else TOKEN * 100)
        path.chmod(0o644 if kind == 'permissions' else 0o600)
        if kind == 'symlink':
            path.rename(tmp_path / 'target')
            path.symlink_to(tmp_path / 'target')
        if kind == 'hardlink':
            os.link(path, tmp_path / 'link')
        if kind == 'malformed':
            path.write_text('bad secret')
    with pytest.raises(InvalidRequest) as caught:
        telegram.read_telegram_token(tmp_path)
    assert TOKEN not in str(caught.value)


def test_private_credential_accepts_operator_created_file(tmp_path):
    telegram = telegram_module()
    path = tmp_path / 'telegram-token'
    path.write_text(TOKEN + '\n')
    path.chmod(0o600)
    assert telegram.read_telegram_token(tmp_path) == TOKEN


async def test_disabled_adapter_performs_no_credential_or_client_io(tmp_path, monkeypatch):
    telegram = telegram_module()
    def forbidden(*args, **kwargs):
        pytest.fail('Disabled adapter attempted credential/client IO')
    monkeypatch.setattr(telegram, 'read_telegram_token', forbidden)
    monkeypatch.setattr(telegram.httpx, 'AsyncClient', forbidden)
    adapter = telegram.TelegramAdapter(None, Settings(root=tmp_path))
    await adapter.start()
    assert (await adapter.status())['enabled'] is False
    await adapter.close()


def test_credential_read_allows_access_time_update(tmp_path):
    telegram = telegram_module()
    path = tmp_path / 'telegram-token'
    path.write_text(TOKEN)
    path.chmod(0o600)
    os.utime(path, ns=(1, 1))
    assert telegram.read_telegram_token(tmp_path) == TOKEN


def test_normalization_validates_chat_topic_and_bounded_sender_identity():
    from tests.support.telegram_peer import update
    telegram = telegram_module()
    values = [update(chat=1), update(sender=True), update(topic=-1), update(topic=2**52),
              update(chat=2**52), update(**{'from': {'id': 20}}),
              update(message_thread_id=5) | {'message': update()['message'] | {'chat': {'id': 20, 'type': 'private'}, 'message_thread_id': 5}}]
    for payload in values:
        assert telegram.TelegramUpdate.from_payload(payload).kind == 'rejected'


async def test_schema6_additive_upgrade_preserves_run_and_newer_refusal(tmp_path, monkeypatch):
    import sqlite3
    import hyperclaw.store as module
    from hyperclaw.contracts import RunRequest, UnsupportedSchema
    migrations = module.MIGRATIONS
    monkeypatch.setattr(module, 'MIGRATIONS', migrations[:6])
    store = await module.Store.open(tmp_path)
    session = await store.create_session()
    run = await store.submit(RunRequest(session_id=session.id, generation=0, request_id='legacy', text='preserve'))
    await store.close()
    monkeypatch.setattr(module, 'MIGRATIONS', migrations)
    store = await module.Store.open(tmp_path)
    assert (await store.get_run(run.id)).request.text == 'preserve'
    assert await store.telegram_status() == {'updates': [], 'deliveries': []}
    await store.close()
    with sqlite3.connect(tmp_path / 'runtime.sqlite3') as db:
        assert db.execute('SELECT version FROM schema_version').fetchone()[0] == 7
        db.execute('UPDATE schema_version SET version=8')
    with pytest.raises(UnsupportedSchema):
        await module.Store.open(tmp_path)


async def test_cancelled_telegram_reservation_settles_before_return(tmp_path):
    import asyncio
    import threading
    from hyperclaw.store import Store
    from tests.support.telegram_peer import update
    store = await Store.open(tmp_path)
    entered, release = threading.Event(), threading.Event()
    blocker = asyncio.create_task(store._call(lambda: (entered.set(), release.wait(5))))
    await asyncio.to_thread(entered.wait, 2)
    task = asyncio.create_task(store.telegram_reserve(123456, telegram_module().TelegramUpdate.from_payload(update()), True))
    await asyncio.sleep(.02)
    task.cancel()
    await asyncio.sleep(.02)
    assert not task.done()
    release.set()
    await blocker
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await store.telegram_update(123456, 1))['status'] == 'reserved'
    assert await store.telegram_offset(123456) == 2
    await store.close()


def test_outbound_utf16_limit_preserves_run_and_truncation_notice():
    text = telegram_module().delivery_text('😀' * 4000, 'run-reference')
    assert len(text.encode('utf-16-le')) // 2 <= 3500
    assert '[truncated]' in text and text.endswith('Run: run-reference')


async def test_transport_exception_url_is_sanitized_and_cancellation_propagates(caplog):
    import asyncio
    import logging
    import httpx
    from hyperclaw.telegram_transport import TelegramFailure, TelegramTransport
    caplog.set_level(logging.DEBUG)
    async def failed(request):
        raise httpx.ConnectError(str(request.url), request=request)
    transport = TelegramTransport(TOKEN, httpx.AsyncClient(transport=httpx.MockTransport(failed)))
    try:
        with pytest.raises(TelegramFailure) as caught:
            await transport.call('getMe')
        assert TOKEN not in str(caught.value) + caplog.text
    finally:
        await transport.close()
    entered = asyncio.Event()
    async def held(request):
        entered.set()
        await asyncio.Event().wait()
    transport = TelegramTransport(TOKEN, httpx.AsyncClient(transport=httpx.MockTransport(held)))
    task = asyncio.create_task(transport.call('getMe'))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await transport.close()


def test_credential_path_replacement_is_rejected(tmp_path, monkeypatch):
    telegram = telegram_module()
    path = tmp_path / 'telegram-token'
    path.write_text(TOKEN)
    path.chmod(0o600)
    original = telegram.os.open
    def raced(target, flags, *args):
        path.rename(tmp_path / 'original')
        path.write_text(TOKEN)
        path.chmod(0o600)
        return original(target, flags, *args)
    monkeypatch.setattr(telegram.os, 'open', raced)
    with pytest.raises(InvalidRequest):
        telegram.read_telegram_token(tmp_path)


def test_nonregular_credential_closes_opened_descriptor(tmp_path, monkeypatch):
    telegram = telegram_module()
    (tmp_path / 'telegram-token').mkdir()
    opened = []
    original = telegram.os.open
    def record(*args, **kwargs):
        fd = original(*args, **kwargs)
        opened.append(fd)
        return fd
    monkeypatch.setattr(telegram.os, 'open', record)
    with pytest.raises(InvalidRequest):
        telegram.read_telegram_token(tmp_path)
    for fd in opened:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_outbound_unpaired_surrogate_cannot_break_delivery_task():
    import json
    text = telegram_module().delivery_text('untrusted \ud800 output', 'run-reference')
    json.dumps({'text': text}, ensure_ascii=False).encode('utf-8')
    assert text.endswith('Run: run-reference')


@pytest.mark.parametrize('chat_type', [[], {}, 1, None])
def test_untrusted_chat_type_is_rejected_without_crashing_intake(chat_type):
    from tests.support.telegram_peer import update
    payload = update()
    payload['message']['chat']['type'] = chat_type
    assert telegram_module().TelegramUpdate.from_payload(payload).kind == 'rejected'


@pytest.mark.parametrize('caption,want', [
    (None, 'Describe this image.'), ('', 'Describe this image.'),
    (' \t\n ', 'Describe this image.'), ('\u2003', 'Describe this image.'),
    ('  Describe the red square.\n', '  Describe the red square.\n'),
])
def test_photo_caption_normalizes_to_a_valid_request_without_changing_meaningful_text(caption, want):
    from hyperclaw.contracts import RunRequest
    from tests.support.telegram_peer import update
    payload = update(photo=[{'file_id': 'synthetic', 'width': 1, 'height': 1}])
    if caption is not None:
        payload['message']['caption'] = caption
    normalized = telegram_module().TelegramUpdate.from_payload(payload)
    assert normalized.kind == 'photo'
    request = RunRequest(session_id='session', generation=0, request_id='caption', text=normalized.text)
    assert request.text == want
