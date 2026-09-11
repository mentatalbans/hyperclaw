"""Optional Telegram intake and delivery through the durable runtime."""
import asyncio
import base64
import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Literal

import httpx
from pydantic import Field, ValidationError

from hyperclaw.contracts import (Conflict, ImageAttachment, InvalidRequest, RunRequest,
                                 RuntimeErrorBase, Value, canonical)
from hyperclaw.telegram_transport import TelegramFailure, TelegramTransport

PHOTO_LIMIT = 4 * 1024 * 1024


def read_telegram_token(root: Path) -> str:
    """Read only the operator's fixed, private, single-link regular file."""
    try:
        path = root / 'telegram-token'
        before = path.lstat()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            stream = os.fdopen(fd, 'rb')
        except BaseException:
            os.close(fd)
            raise
        with stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode) or stat.S_IMODE(opened.st_mode) != 0o600
                    or opened.st_nlink != 1 or opened.st_uid != os.getuid()
                    or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
                    or opened.st_size > 256):
                raise ValueError
            raw = stream.read(257)
            after = os.fstat(stream.fileno())
            current = path.lstat()
            stable = ('st_dev', 'st_ino', 'st_mode', 'st_nlink', 'st_uid', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
            if (any(getattr(opened, key) != getattr(after, key) or getattr(opened, key) != getattr(current, key) for key in stable)
                    or len(raw) > 256):
                raise ValueError
        token = raw.decode('ascii').removesuffix('\n')
        if re.fullmatch(r'[1-9][0-9]{0,15}:[A-Za-z0-9_-]{30,100}', token) is None:
            raise ValueError
        if int(token.split(':')[0]) >= 2**52:
            raise ValueError
        return token
    except (OSError, ValueError, UnicodeError):
        raise InvalidRequest('telegram_credential', 'Telegram requires a private 0600 regular telegram-token file with a valid bot token.') from None


def _integer(value, *, signed=False, zero=False):
    return type(value) is int and (abs(value) if signed else value) >= (0 if zero else 1) and abs(value) < 2**52


class TelegramUpdate(Value):
    """Bounded normalization; rejected envelopes retain only ID and fingerprint."""
    update_id: int = Field(ge=0, lt=2**52, strict=True)
    fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')
    kind: Literal['text', 'photo', 'rejected', 'ignored']
    chat_id: int = Field(default=0, gt=-2**52, lt=2**52, strict=True)
    sender_id: int = Field(default=0, ge=0, lt=2**52, strict=True)
    topic_id: int = Field(default=0, ge=0, lt=2**52, strict=True)
    message_id: int = Field(default=0, ge=0, lt=2**52, strict=True)
    text: str = Field(default='', max_length=16384)
    file_id: str = Field(default='', max_length=512)

    @classmethod
    def from_payload(cls, payload):
        if not isinstance(payload, dict) or not _integer(payload.get('update_id'), zero=True):
            raise InvalidRequest('telegram_update', 'Invalid Telegram update envelope.')
        try:
            encoded = canonical(payload).encode('utf-8')
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise InvalidRequest('telegram_update', 'Invalid Telegram update envelope.') from None
        if len(encoded) > 1024 * 1024:
            raise InvalidRequest('telegram_update', 'Telegram update exceeds its limit.')
        base = {'update_id': payload['update_id'], 'fingerprint': hashlib.sha256(encoded).hexdigest()}
        rejected = cls(**base, kind='rejected')
        if set(payload) != {'update_id', 'message'}:
            return rejected
        msg = payload['message']
        if not isinstance(msg, dict):
            return rejected
        # Explicit subset: attachment/service/forwarded sender shapes never become prompts.
        allowed = {'message_id', 'date', 'chat', 'from', 'message_thread_id', 'is_topic_message',
                   'text', 'entities', 'photo', 'caption', 'caption_entities', 'reply_to_message',
                   'has_protected_content', 'link_preview_options', 'show_caption_above_media'}
        if set(msg) - allowed:
            return rejected
        sender, chat = msg.get('from'), msg.get('chat')
        if (not isinstance(sender, dict) or sender.get('is_bot') is not False
                or not _integer(sender.get('id')) or not isinstance(chat, dict)
                or chat.get('type') not in ('private', 'group', 'supergroup')
                or not _integer(chat.get('id'), signed=True)
                or not _integer(msg.get('message_id'))
                or not _integer(msg.get('message_thread_id', 0), zero=True)
                or (chat.get('type') == 'private' and (chat['id'] != sender['id'] or msg.get('message_thread_id', 0) != 0))
                or (chat.get('type') in {'group', 'supergroup'} and chat['id'] >= 0)
                or (chat.get('type') == 'group' and msg.get('message_thread_id', 0) != 0)):
            return rejected
        data = dict(chat_id=chat['id'], sender_id=sender['id'], topic_id=msg.get('message_thread_id', 0),
                    message_id=msg['message_id'])
        text = msg.get('caption', '') if 'photo' in msg else msg.get('text', '')
        if not isinstance(text, str) or len(text) > 16384:
            return rejected
        try:
            if len(text.encode('utf-8')) > 60000:
                return rejected
        except UnicodeError:
            return rejected
        if 'photo' in msg:
            photos = msg['photo']
            if 'text' in msg or not isinstance(photos, list) or not 1 <= len(photos) <= 20:
                return rejected
            choices = []
            for photo in photos:
                if (not isinstance(photo, dict) or not isinstance(photo.get('file_id'), str)
                        or re.fullmatch(r'[A-Za-z0-9_-]{1,512}', photo['file_id']) is None
                        or not _integer(photo.get('width')) or not _integer(photo.get('height'))
                        or not _integer(photo.get('file_size', 0), zero=True)):
                    return rejected
                if photo.get('file_size', 0) <= PHOTO_LIMIT:
                    choices.append(photo)
            if not choices:
                return rejected
            chosen = max(choices, key=lambda p: p['width'] * p['height'])
            return cls(**base, **data, kind='photo', text=text if text.strip() else 'Describe this image.', file_id=chosen['file_id'])
        if not text.strip() or 'caption' in msg:
            return rejected
        return cls(**base, **data, kind='text', text=text)


def delivery_text(text, run_id):
    """Fit Telegram's UTF-16 limit while preserving the operator's run reference."""
    suffix = f'\n\nRun: {run_id}'
    budget = 3500 - len(suffix.encode('utf-16-le')) // 2
    raw = text.encode('utf-16-le', errors='replace')
    text = raw.decode('utf-16-le')
    if len(raw) // 2 > budget:
        note = '\n[truncated]'
        text = raw[:(budget - len(note)) * 2].decode('utf-16-le', errors='ignore') + note
    return text + suffix


class TelegramAdapter:
    def __init__(self, runtime, settings, client: httpx.AsyncClient | None = None):
        self.runtime, self.settings = runtime, settings
        self.client = client
        self.bot_id = None
        self.error = None
        self.transport = None
        self._tasks = []
        self._intake = asyncio.Lock()
        self._close_task = None

    def authorized(self, update):
        return (self.settings.telegram_enabled and update.kind in {'text', 'photo'}
                and update.chat_id != 0 and update.sender_id > 0 and update.message_id > 0
                and f'{update.chat_id}:{update.sender_id}' in self.settings.telegram_allowed_pairs)

    async def start(self) -> None:
        if not self.settings.telegram_enabled or self.transport is not None:
            return
        try:
            token = read_telegram_token(self.settings.root)
            self.transport = TelegramTransport(token, self.client)
            identity = await self.transport.call('getMe')
            if (not isinstance(identity, dict) or identity.get('is_bot') is not True
                    or not _integer(identity.get('id')) or identity['id'] != int(token.split(':')[0])):
                raise TelegramFailure('telegram_identity')
            webhook = await self.transport.call('getWebhookInfo')
            if not isinstance(webhook, dict) or webhook.get('url') != '':
                raise TelegramFailure('telegram_webhook')
            self.bot_id = identity['id']
            await self.runtime.store.telegram_recover_deliveries(self.bot_id)
            self._tasks = [asyncio.create_task(self._poll(), name='hyperclaw-telegram-intake'),
                           asyncio.create_task(self._deliver(), name='hyperclaw-telegram-delivery')]
        except (TelegramFailure, InvalidRequest) as exc:
            self.error = exc.code
            if self.transport is not None:
                await self.transport.close()
            raise InvalidRequest('telegram_startup', 'Telegram startup failed: ' + self.error + '.') from None

    async def handle(self, update: TelegramUpdate) -> None:
        if not self.settings.telegram_enabled:
            return
        if self.bot_id is None:
            raise InvalidRequest('telegram_not_started', 'Telegram adapter has not authenticated.')
        # Revalidate typed callers as well as polled envelopes.
        update = TelegramUpdate.model_validate(update.model_dump())
        async with self._intake:
            row = await self.runtime.store.telegram_reserve(self.bot_id, update, self.authorized(update))
            await self._accept(row)

    async def _accept(self, row):
        if row['status'] not in {'reserved', 'prepared'}:
            return
        store, update_id = self.runtime.store, row['update_id']
        update = TelegramUpdate.model_validate_json(row['normalized_json'])
        try:
            if await store.telegram_reconcile(self.bot_id, update_id):
                return
            if not self.authorized(update):
                raise TelegramFailure('telegram_authorization_revoked')
            if row['request_json'] is None:
                images = ()
                if update.kind == 'photo':
                    file = await self.transport.call('getFile', {'file_id': update.file_id})
                    if (not isinstance(file, dict) or file.get('file_id') != update.file_id
                            or not _integer(file.get('file_size', 0), zero=True)
                            or file.get('file_size', 0) > PHOTO_LIMIT):
                        raise TelegramFailure('telegram_file')
                    if not self.authorized(update):
                        raise TelegramFailure('telegram_authorization_revoked')
                    raw = await self.transport.download(file.get('file_path'))
                    for media in ('image/png', 'image/jpeg', 'image/gif', 'image/webp'):
                        try:
                            images = (ImageAttachment(media_type=media, data=base64.b64encode(raw).decode('ascii')),)
                            break
                        except ValidationError:
                            pass
                    if not images:
                        raise TelegramFailure('telegram_image')
                request = RunRequest(session_id=row['session_id'], generation=row['generation'],
                    request_id=row['request_id'], text=update.text, images=images,
                    context_bytes=8 * 1024 * 1024 if images else 65536)
                await store.telegram_prepare(self.bot_id, update_id, request)
            else:
                request = RunRequest.model_validate_json(row['request_json'])
            await self.runtime.submit(request)
            await store.telegram_reconcile(self.bot_id, update_id)
        except (TelegramFailure, Conflict, InvalidRequest, ValidationError) as exc:
            await store.telegram_reject(self.bot_id, update_id,
                exc.code if isinstance(exc, (TelegramFailure, RuntimeErrorBase)) else 'telegram_input')

    async def _poll(self):
        backoff = 1
        while True:
            try:
                async with self._intake:
                    for row in await self.runtime.store.telegram_pending(self.bot_id):
                        await self._accept(row)
                offset = await self.runtime.store.telegram_offset(self.bot_id)
                values = await self.transport.call('getUpdates', {'offset': offset, 'limit': 25,
                                                   'timeout': 20, 'allowed_updates': ['message']})
                if not isinstance(values, list) or len(values) > 25:
                    raise TelegramFailure('telegram_updates')
                normalized = [TelegramUpdate.from_payload(value) for value in values]
                # Commit in monotonic order so a crash cannot acknowledge an unreserved lower ID.
                for update in sorted(normalized, key=lambda value: value.update_id):
                    try:
                        await self.handle(update)
                    except Conflict:
                        self.error = 'telegram_update_conflict'
                backoff = 1
                await asyncio.sleep(.1)
            except (TelegramFailure, RuntimeErrorBase) as exc:
                self.error = exc.code
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _send(self, row):
        store, update_id, phase = self.runtime.store, row['update_id'], row['phase']
        update = TelegramUpdate.model_validate_json(row['normalized_json'])
        if not await store.telegram_begin_delivery(self.bot_id, update_id, phase):
            return
        try:
            if not self.authorized(update):
                raise TelegramFailure('telegram_authorization_revoked', rejected=True)
            run = await self.runtime.get_run(row['run_id'])
            text = (f'Approval required. Review this run in the local web UI or CLI.' if phase == 'approval'
                    else run.output or f'Run {run.status}. Review details in the local web UI or CLI.')
            body = {'chat_id': update.chat_id, 'text': delivery_text(text, run.id),
                    'reply_parameters': {'message_id': update.message_id}}
            if update.topic_id:
                body['message_thread_id'] = update.topic_id
            result = await self.transport.call('sendMessage', body)
            if (not isinstance(result, dict) or not _integer(result.get('message_id'))
                    or not isinstance(result.get('chat'), dict)
                    or type(result['chat'].get('id')) is not int or result['chat']['id'] != update.chat_id):
                raise TelegramFailure('telegram_send_receipt')
            await store.telegram_finish_delivery(self.bot_id, update_id, phase, 'sent', message_id=result['message_id'])
        except TelegramFailure as exc:
            await store.telegram_finish_delivery(self.bot_id, update_id, phase,
                'failed' if exc.rejected else 'uncertain', error=exc.code)
        except asyncio.CancelledError:
            await store.telegram_finish_delivery(self.bot_id, update_id, phase, 'uncertain', error='telegram_shutdown')
            raise

    async def _deliver(self):
        while True:
            try:
                for row in await self.runtime.store.telegram_delivery_candidates(self.bot_id):
                    await self._send(row)
                await asyncio.sleep(.1)
            except RuntimeErrorBase as exc:
                self.error = exc.code
                await asyncio.sleep(1)

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self):
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self.transport is not None:
            await self.transport.close()
        elif self.client is not None:
            await self.client.aclose()

    async def status(self):
        summaries = await self.runtime.store.telegram_status(self.bot_id) if self.runtime is not None else {'updates': [], 'deliveries': []}
        return {'enabled': self.settings.telegram_enabled, 'bot_id': self.bot_id, 'error': self.error, **summaries}
