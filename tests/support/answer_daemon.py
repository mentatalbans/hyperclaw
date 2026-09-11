"""Test-only request/wire observer; execute the installed daemon unchanged."""
import base64
import codecs
import hashlib
import json
from pathlib import Path
import runpy
import sys

import httpx

root = Path(sys.argv[sys.argv.index('--root') + 1])
output = (root / 'answer-wire.jsonl').open('a', buffering=1)
original = httpx.AsyncHTTPTransport.handle_async_request
sequence = 0


def record(value):
    output.write(json.dumps(value) + '\n')


class ObservedStream(httpx.AsyncByteStream):
    def __init__(self, stream, index):
        self.stream, self.index = stream, index

    async def __aiter__(self):
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        async for chunk in self.stream:
            record({'kind': 'response_chunk', 'request_index': self.index,
                    'base64': base64.b64encode(chunk).decode(), 'text': decoder.decode(chunk)})
            yield chunk
        tail = decoder.decode(b'', final=True)
        if tail:
            record({'kind': 'response_chunk', 'request_index': self.index, 'base64': '', 'text': tail})

    async def aclose(self):
        await self.stream.aclose()


async def observe(self, request):
    global sequence
    if request.url.path != '/v1/messages':
        return await original(self, request)
    sequence += 1
    index = sequence
    body = await request.aread()
    record({'kind': 'request', 'request_index': index, 'method': request.method,
            'url': str(request.url), 'body': json.loads(body),
            'body_sha256': hashlib.sha256(body).hexdigest(), 'body_base64': base64.b64encode(body).decode()})
    try:
        response = await original(self, request)
    except BaseException as exc:
        record({'kind': 'transport_error', 'request_index': index, 'type': type(exc).__name__})
        raise
    record({'kind': 'response', 'request_index': index, 'status': response.status_code})
    response.stream = ObservedStream(response.stream, index)
    return response


httpx.AsyncHTTPTransport.handle_async_request = observe
try:
    runpy.run_module('hyperclaw', run_name='__main__')
finally:
    output.close()
