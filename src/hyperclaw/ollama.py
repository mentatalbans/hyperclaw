"""Bounded Ollama Messages transport with explicit protocol completion and no retries."""
import asyncio
import json
import re

import httpx

from hyperclaw.config import Settings
from hyperclaw.contracts import Message, ModelEvent, ProviderFailure, ToolCall

FRAME_LIMIT = 1024 * 1024
TOOL_ARGUMENT_LIMIT = 64 * 1024
TOOL_CALL_LIMIT = 16
THINKING_SIGNATURE_LIMIT = 64 * 1024
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

    async def stream(self, messages: list[Message], system: str = '', tools: list[dict] | None = None):
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
        if tools is not None:
            payload['tools'] = tools
        offered_names = {
            tool.get('name') for tool in tools or ()
            if isinstance(tool, dict) and isinstance(tool.get('name'), str)
        }
        started = False
        active = None
        next_index = 0
        final_delta = False
        reason = None
        content = []
        call_ids = set()
        call_count = 0
        signature_bytes = 0
        usage = {'model': settings.model, 'input_tokens': None, 'output_tokens': None}

        def counts(value, final=False):
            if not isinstance(value, dict):
                raise invalid()
            for key in ('input_tokens', 'output_tokens'):
                if key == 'output_tokens' and not final:
                    continue
                if key in value:
                    count = value[key]
                    if count is not None and (type(count) is not int or count < 0):
                        raise invalid()
                    usage[key] = count

        deadline = asyncio.get_running_loop().time() + settings.request_timeout_s
        response, incoming = None, None
        try:
            async with asyncio.timeout_at(deadline):
                request = self._client.build_request('POST', settings.ollama_url + '/v1/messages', json=payload,
                    headers={'x-api-key': 'ollama', 'anthropic-version': '2023-06-01'})
                response = await self._client.send(request, stream=True)
            if response.status_code != 200:
                raise ProviderFailure(f'http_{response.status_code}', f'Model endpoint returned HTTP {response.status_code}.')
            if response.headers.get('content-type', '').split(';')[0].strip() != 'text/event-stream':
                raise invalid()
            incoming = frames(response)
            while True:
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError
                try:
                    async with asyncio.timeout_at(deadline):
                        value = await anext(incoming)
                except StopAsyncIteration:
                    break
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
                    if final_delta or active is not None or type(index) is not int or index != next_index or next_index >= 256:
                        raise invalid()
                    block = value['content_block']
                    block_type = block['type']
                    if block_type == 'tool_use':
                        if not offered_names:
                            raise ProviderFailure('tools_disabled', 'Model requested a tool while tools are disabled.')
                        call_id, name, initial = block['id'], block['name'], block['input']
                        if (not isinstance(call_id, str) or not isinstance(name, str)
                                or not isinstance(initial, dict) or name not in offered_names
                                or call_id in call_ids or call_count >= TOOL_CALL_LIMIT):
                            raise invalid()
                        call_ids.add(call_id)
                        call_count += 1
                        active = {
                            'index': index, 'type': block_type, 'id': call_id, 'name': name,
                            'initial': initial, 'partial_json': '', 'partial_bytes': 0,
                        }
                        next_index += 1
                        continue
                    if block_type not in {'text', 'thinking'}:
                        raise invalid()
                    text = block.get(block_type, '')
                    if not isinstance(text, str):
                        raise invalid()
                    active = {'index': index, 'type': block_type, block_type: text}
                    if block_type == 'thinking':
                        signature = block.get('signature', '')
                        if not isinstance(signature, str):
                            raise invalid()
                        added_signature_bytes = len(signature.encode('utf-8'))
                        if signature_bytes + added_signature_bytes > THINKING_SIGNATURE_LIMIT:
                            raise ProviderFailure(
                                'response_limit', 'Model response content exceeded its limit.')
                        signature_bytes += added_signature_bytes
                        active['signature'] = signature
                    next_index += 1
                    if text:
                        yield ModelEvent(kind=block_type, data={'text': text})
                elif kind == 'content_block_delta':
                    if active is None or type(value['index']) is not int or value['index'] != active['index']:
                        raise invalid()
                    delta = value['delta']
                    block_type = active['type']
                    if block_type == 'tool_use':
                        if delta.get('type') != 'input_json_delta':
                            raise invalid()
                        partial = delta.get('partial_json')
                        if not isinstance(partial, str):
                            raise invalid()
                        partial_bytes = len(partial.encode('utf-8'))
                        if active['partial_bytes'] + partial_bytes > TOOL_ARGUMENT_LIMIT:
                            raise invalid()
                        active['partial_json'] += partial
                        active['partial_bytes'] += partial_bytes
                        continue
                    if delta.get('type') == 'signature_delta' and block_type == 'thinking':
                        signature = delta.get('signature')
                        if not isinstance(signature, str):
                            raise invalid()
                        added_signature_bytes = len(signature.encode('utf-8'))
                        if signature_bytes + added_signature_bytes > THINKING_SIGNATURE_LIMIT:
                            raise ProviderFailure(
                                'response_limit', 'Model response content exceeded its limit.')
                        signature_bytes += added_signature_bytes
                        active['signature'] += signature
                        continue
                    if delta.get('type') != block_type + '_delta':
                        raise invalid()
                    text = delta[block_type]
                    if not isinstance(text, str):
                        raise invalid()
                    active[block_type] += text
                    if text:
                        yield ModelEvent(kind=block_type, data={'text': text})
                elif kind == 'content_block_stop':
                    if active is None or type(value['index']) is not int or value['index'] != active['index']:
                        raise invalid()
                    block_type = active['type']
                    if block_type == 'tool_use':
                        partial = active['partial_json']
                        if partial and active['initial']:
                            raise invalid()
                        arguments = json.loads(partial) if partial else active['initial']
                        call = ToolCall(id=active['id'], name=active['name'], arguments=arguments)
                        content.append({
                            'type': 'tool_use', 'id': call.id, 'name': call.name,
                            'input': call.arguments,
                        })
                        yield ModelEvent(kind='tool_call', data={
                            'id': call.id, 'name': call.name, 'arguments': call.arguments,
                        })
                    elif block_type == 'thinking':
                        content.append({
                            'type': 'thinking', 'thinking': active['thinking'],
                            'signature': active['signature'],
                        })
                    else:
                        content.append({'type': 'text', 'text': active['text']})
                    active = None
                elif kind == 'message_delta':
                    if active is not None or final_delta:
                        raise invalid()
                    reason = value['delta']['stop_reason']
                    if reason not in {'end_turn', 'max_tokens', 'tool_use'}:
                        raise invalid()
                    counts(value.get('usage', {}), final=True)
                    final_delta = True
                elif kind == 'message_stop':
                    if active is not None or not final_delta:
                        raise invalid()
                    yield ModelEvent(kind='usage', data=usage)
                    if reason == 'max_tokens':
                        raise ProviderFailure('output_limit', 'Model reached the output token limit before completing its answer.')
                    if reason == 'tool_use' and not offered_names:
                        raise ProviderFailure('tools_disabled', 'Model requested a tool while tools are disabled.')
                    has_calls = bool(call_ids)
                    if (reason == 'tool_use') != has_calls:
                        raise invalid()
                    yield ModelEvent(kind='finish', data={'stop_reason': reason, 'content': content})
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
        finally:
            if incoming is not None:
                await incoming.aclose()
            if response is not None:
                await response.aclose()
