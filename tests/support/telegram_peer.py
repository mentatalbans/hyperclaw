"""Synthetic Telegram HTTP peer. Endpoint rewriting belongs exclusively to tests."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from tests.support.images import png

TOKEN = '123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi'


def update(update_id=1, chat=-10, sender=20, topic=0, **message):
    value = {'message_id': update_id + 100, 'date': 1, 'chat': {'id': chat, 'type': 'supergroup'},
             'from': {'id': sender, 'is_bot': False}, 'text': 'hello'} | message
    if topic:
        value['message_thread_id'] = topic
    if 'photo' in message:
        value.pop('text', None)
    return {'update_id': update_id, 'message': value}


class LocalTransport(httpx.AsyncBaseTransport):
    def __init__(self, url):
        self.url = httpx.URL(url)
        self.transport = httpx.AsyncHTTPTransport(trust_env=False)

    async def handle_async_request(self, request):
        assert request.url.scheme == 'https' and request.url.host == 'api.telegram.org'
        request.url = request.url.copy_with(scheme=self.url.scheme, host=self.url.host, port=self.url.port)
        return await self.transport.handle_async_request(request)

    async def aclose(self):
        await self.transport.aclose()


class TelegramPeer:
    def __init__(self):
        self.requests = []
        self.updates = []
        self.responses = {}
        self.gates = {}
        self.send_gate = threading.Event()
        self.send_gate.set()
        self.send_observed = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.reply('download', {})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
                self.reply(self.path.rsplit('/', 1)[-1], body)

            def reply(self, method, body):
                owner.requests.append((method, body))
                if method in owner.gates:
                    owner.gates[method].wait(15)
                if method == 'sendMessage':
                    owner.send_observed.set()
                    owner.send_gate.wait(15)
                default = {'getMe': {'id': 123456, 'is_bot': True, 'first_name': 'Synthetic'},
                    'getWebhookInfo': {'url': '', 'pending_update_count': 0},
                    'getUpdates': [u for u in owner.updates if u['update_id'] >= body.get('offset', 0)][:25],
                    'getFile': {'file_id': body.get('file_id'), 'file_path': 'photos/synthetic.png', 'file_size': len(png())},
                    'sendMessage': {'message_id': 999, 'chat': {'id': body.get('chat_id')}}}
                result = owner.responses.get(method, png() if method == 'download' else {'ok': True, 'result': default.get(method)})
                code = 200
                headers = {}
                if isinstance(result, tuple):
                    code, headers, result = result
                raw = result if isinstance(result, bytes) else json.dumps(result).encode()
                self.send_response(code)
                for key, value in headers.items():
                    if value is not None:
                        self.send_header(key, value)
                if 'Content-Length' not in headers:
                    self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def client(self):
        return httpx.AsyncClient(transport=LocalTransport(self.url), trust_env=False, follow_redirects=False)

    def close(self):
        self.send_gate.set()
        for gate in self.gates.values():
            gate.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        assert not self.thread.is_alive()
