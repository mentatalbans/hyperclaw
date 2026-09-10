"""Fixed-origin, bounded Telegram HTTP with secret-free errors and diagnostics."""
import asyncio
import json
import logging
import re

import httpx

JSON_LIMIT = 1024 * 1024
PHOTO_LIMIT = 4 * 1024 * 1024


class TelegramFailure(Exception):
    def __init__(self, code, *, rejected=False):
        self.code, self.rejected = code, rejected
        super().__init__(code)


class _CredentialFilter(logging.Filter):
    def __init__(self, token):
        super().__init__()
        self.token = token

    def filter(self, record):
        # HTTPX logs successful request URLs at INFO. Never pass its URL arguments on.
        record.msg = record.getMessage().replace(self.token, '[telegram-credential]')
        record.args = ()
        return True


class TelegramTransport:
    def __init__(self, token, client=None):
        self._token = token
        self.client = client if client is not None else httpx.AsyncClient(
            trust_env=False, follow_redirects=False, timeout=httpx.Timeout(30, connect=5, write=5, pool=5))
        self._filter = _CredentialFilter(token)
        self._loggers = [logging.getLogger(name) for name in (
            'httpx', 'httpcore.connection', 'httpcore.http11', 'httpcore.http2',
            'httpcore.proxy', 'httpcore.socks')]
        for logger in self._loggers:
            logger.addFilter(self._filter)

    async def close(self):
        try:
            await self.client.aclose()
        finally:
            for logger in self._loggers:
                logger.removeFilter(self._filter)

    async def _read(self, url, *, body=None, limit=JSON_LIMIT):
        try:
            async with asyncio.timeout(35):
                async with self.client.stream('GET' if body is None else 'POST', url, json=body,
                        follow_redirects=False, headers={'Accept-Encoding': 'identity'},
                        timeout=httpx.Timeout(30, connect=5, write=5, pool=5)) as response:
                    if 300 <= response.status_code < 400:
                        raise TelegramFailure('telegram_redirect')
                    if response.headers.get('content-encoding', 'identity') != 'identity':
                        raise TelegramFailure('telegram_encoding')
                    declared = response.headers.get('content-length')
                    if declared is not None and (not declared.isascii() or not declared.isdecimal() or int(declared) > limit):
                        raise TelegramFailure('telegram_size')
                    result = bytearray()
                    async for chunk in response.aiter_raw(chunk_size=16384):
                        if len(result) + len(chunk) > limit:
                            raise TelegramFailure('telegram_size')
                        result.extend(chunk)
                    return response.status_code, bytes(result)
        except (httpx.HTTPError, TimeoutError, ValueError):
            raise TelegramFailure('telegram_transport') from None

    async def call(self, method, body=None):
        if method not in {'getMe', 'getWebhookInfo', 'getUpdates', 'getFile', 'sendMessage'}:
            raise TelegramFailure('telegram_method')
        status, raw = await self._read(f'https://api.telegram.org/bot{self._token}/{method}', body=body or {})
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise TelegramFailure('telegram_json') from None
        if not isinstance(value, dict):
            raise TelegramFailure('telegram_response')
        if value.get('ok') is False:
            raise TelegramFailure('telegram_rejected', rejected=True)
        if status != 200 or value.get('ok') is not True or 'result' not in value:
            raise TelegramFailure('telegram_response')
        return value['result']

    async def download(self, path):
        # Restrict all path segments; percent encoding, separators and URL syntax are excluded.
        if (not isinstance(path, str) or len(path) > 512
                or re.fullmatch(r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_.-]+)+', path) is None
                or any(part in {'.', '..'} for part in path.split('/'))):
            raise TelegramFailure('telegram_file_path')
        status, raw = await self._read(f'https://api.telegram.org/file/bot{self._token}/{path}', limit=PHOTO_LIMIT)
        if status != 200:
            raise TelegramFailure('telegram_download')
        return raw
