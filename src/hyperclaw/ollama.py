"""Bounded Ollama Messages transport with explicit protocol completion and no retries."""
import asyncio
import json
import re

import httpx

from hyperclaw.config import Settings
from hyperclaw.contracts import Message, ModelEvent, ProviderFailure

FRAME_LIMIT = 1024 * 1024
SEPARATOR = re.compile(br'\r?\n\r?\n')


def invalid():
    return ProviderFailure('invalid_stream', 'The model returned a malformed Messages stream.')


async def frames(response):
    buffer = bytearray()
    async for chunk in response.aiter_bytes():
        buffer.extend(chunk)
        while True:
            match = SEPARATOR.search(buffer)
            if match is None:
                if len(buffer) > FRAME_LIMIT:
                    raise ProviderFailure('frame_too_large', 'Model event exceeded the 1 MiB limit.')
                break
            if match.start() > FRAME_LIMIT:
                raise ProviderFailure('frame_too_large', 'Model event exceeded the 1 MiB limit.')
            raw = bytes(buffer[:match.start()])
            del buffer[:match.end()]
            try:
                lines = raw.decode('utf-8').splitlines()
                data = '\n'.join(line[5:].lstrip(' ') for line in lines if line.startswith('data:'))
                if not data:
                    continue
                value = json.loads(data)
                names = [line[6:].strip() for line in lines if line.startswith('event:')]
                if not isinstance(value, dict) or (names and names != [value.get('type')]):
                    raise invalid()
                yield value
            except (ValueError, UnicodeError):
                raise invalid() from None
    # A partial final frame cannot establish completion.
    if buffer.strip():
        raise ProviderFailure('incomplete_stream', 'Model stream ended before a complete event.')


class Ollama:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = httpx.AsyncClient(trust_env=False, follow_redirects=False,
                                        timeout=httpx.Timeout(settings.request_timeout_s))

    async def aclose(self):
        await self._client.aclose()

    async def stream(self, messages: list[Message], system: str = ''):
        settings = self.settings
        payload = {
            'model': settings.model,
            'messages': [message.model_dump() for message in messages],
            'max_tokens': settings.max_output_tokens,
            'stream': True,
            'thinking': {'type': 'enabled', 'budget_tokens': 1024} if settings.thinking else {'type': 'disabled'},
        }
        if system:
            payload['system'] = system
        started = False
        active = None
        next_index = 0
        final_delta = False
        reason = None
        usage = {'model': settings.model, 'input_tokens': None, 'output_tokens': None}

        def counts(value):
            if not isinstance(value, dict):
                raise invalid()
            for key in ('input_tokens', 'output_tokens'):
                if key in value:
                    count = value[key]
                    if count is not None and (type(count) is not int or count < 0):
                        raise invalid()
                    usage[key] = count

        try:
            async with asyncio.timeout(settings.request_timeout_s):
                async with self._client.stream('POST', settings.ollama_url + '/v1/messages', json=payload,
                                               headers={'x-api-key': 'ollama', 'anthropic-version': '2023-06-01'}) as response:
                    if response.status_code != 200:
                        raise ProviderFailure(f'http_{response.status_code}', f'Model endpoint returned HTTP {response.status_code}.')
                    if response.headers.get('content-type', '').split(';')[0].strip() != 'text/event-stream':
                        raise invalid()
                    async for value in frames(response):
                        kind = value.get('type')
                        if kind == 'ping':
                            continue
                        if kind == 'error':
                            raise ProviderFailure('model_error', 'Model endpoint reported an error.')
                        if kind == 'message_start':
                            if started:
                                raise invalid()
                            message = value['message']
                            if (message.get('role') != 'assistant' or message.get('content') != []
                                    or message.get('stop_reason') is not None):
                                raise invalid()
                            counts(message.get('usage', {}))
                            started = True
                            continue
                        if not started:
                            raise invalid()
                        if kind == 'content_block_start':
                            index = value['index']
                            if final_delta or active is not None or type(index) is not int or index != next_index:
                                raise invalid()
                            block = value['content_block']
                            block_type = block['type']
                            if block_type == 'tool_use':
                                raise ProviderFailure('tools_disabled', 'Model requested a tool while tools are disabled.')
                            if block_type not in {'text', 'thinking'}:
                                raise invalid()
                            text = block.get(block_type, '')
                            if not isinstance(text, str):
                                raise invalid()
                            active = (index, block_type)
                            next_index += 1
                            if text:
                                yield ModelEvent(kind=block_type, data={'text': text})
                        elif kind == 'content_block_delta':
                            if active is None or type(value['index']) is not int or value['index'] != active[0]:
                                raise invalid()
                            delta = value['delta']
                            if delta.get('type') == 'signature_delta' and active[1] == 'thinking':
                                if not isinstance(delta.get('signature'), str):
                                    raise invalid()
                                continue
                            if delta.get('type') != active[1] + '_delta':
                                raise invalid()
                            text = delta[active[1]]
                            if not isinstance(text, str):
                                raise invalid()
                            if text:
                                yield ModelEvent(kind=active[1], data={'text': text})
                        elif kind == 'content_block_stop':
                            if active is None or type(value['index']) is not int or value['index'] != active[0]:
                                raise invalid()
                            active = None
                        elif kind == 'message_delta':
                            if active is not None or final_delta:
                                raise invalid()
                            reason = value['delta']['stop_reason']
                            if reason not in {'end_turn', 'max_tokens', 'tool_use'}:
                                raise invalid()
                            counts(value.get('usage', {}))
                            final_delta = True
                        elif kind == 'message_stop':
                            if active is not None or not final_delta:
                                raise invalid()
                            yield ModelEvent(kind='usage', data=usage)
                            if reason == 'max_tokens':
                                raise ProviderFailure('output_limit', 'Model reached the output token limit before completing its answer.')
                            if reason == 'tool_use':
                                raise ProviderFailure('tools_disabled', 'Model requested a tool while tools are disabled.')
                            yield ModelEvent(kind='finish', data={'stop_reason': reason})
                            return
                        else:
                            raise invalid()
                    raise ProviderFailure('incomplete_stream', 'Model stream ended without message_stop.')
        except (TimeoutError, httpx.TimeoutException):
            raise ProviderFailure('request_timeout', 'Model request exceeded its deadline.') from None
        except httpx.HTTPError:
            raise ProviderFailure('model_connection', 'Cannot complete the request to the configured model endpoint.') from None
        except (KeyError, TypeError, AttributeError, ValueError):
            raise invalid() from None
