"""Real CLI process and a loopback Messages API provider, with bounded cleanup."""

from __future__ import annotations

import json
import queue
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer



MODEL = "process-test-model"
IO_TIMEOUT = 5


@dataclass
class Reply:
    text: str = "Synthetic reply"
    status: int = 200
    chunks: tuple[str, ...] = ("Hello ", "world.")
    thinking: str = ""
    gate: threading.Event | None = None
    truncate: bool = False


def message(text: str) -> dict:
    """Complete Messages response envelope, shared by JSON and message_start."""
    return {
        "id": "msg_process_test", "type": "message", "role": "assistant",
        "model": MODEL, "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": 4},
    }


class ProviderStub:
    """Script only the external provider; runtime, HTTP and persistence stay real."""

    def __init__(self):
        self.requests: queue.Queue[dict] = queue.Queue()
        self.replies: queue.Queue[Reply] = queue.Queue()
        self.errors: list[str] = []
        self.gates: list[threading.Event] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def do_POST(self):
                self.connection.settimeout(IO_TIMEOUT)
                try:
                    if self.path != "/v1/messages":
                        raise AssertionError(f"Unexpected provider path: {self.path}")
                    payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    owner.requests.put(payload)
                    reply = owner.replies.get_nowait()
                    if reply.status != 200:
                        self.send_json(reply.status, {"type": "error", "error": {
                            "type": "overloaded_error", "message": "Synthetic provider outage"}})
                    elif payload.get("stream"):
                        self.send_stream(reply)
                    else:
                        self.send_json(200, message(reply.text))
                except Exception as exc:
                    owner.errors.append(f"{type(exc).__name__}: {exc}")
                    self.close_connection = True

            def send_json(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()

            def event(self, payload):
                body = f"event: {payload['type']}\ndata: {json.dumps(payload)}\n\n"
                self.wfile.write(body.encode())
                self.wfile.flush()

            def send_stream(self, reply):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                start = message("")
                start.update(content=[], stop_reason=None)
                start["usage"]["output_tokens"] = 0
                self.event({"type": "message_start", "message": start})
                index = 0
                if reply.thinking:
                    self.event({"type": "content_block_start", "index": index,
                                "content_block": {"type": "thinking", "thinking": "", "signature": ""}})
                    self.event({"type": "content_block_delta", "index": index,
                                "delta": {"type": "thinking_delta", "thinking": reply.thinking}})
                    self.event({"type": "content_block_delta", "index": index,
                                "delta": {"type": "signature_delta", "signature": "synthetic-signature"}})
                    self.event({"type": "content_block_stop", "index": index})
                    index += 1
                self.event({"type": "content_block_start", "index": index,
                            "content_block": {"type": "text", "text": ""}})
                for number, chunk in enumerate(reply.chunks):
                    self.event({"type": "content_block_delta", "index": index,
                                "delta": {"type": "text_delta", "text": chunk}})
                    if number == 0 and reply.gate is not None:
                        if not reply.gate.wait(IO_TIMEOUT * 2):
                            raise TimeoutError("Client did not acknowledge the first SSE chunk")
                if reply.truncate:
                    return  # Clean HTTP EOF, deliberately missing the protocol completion marker.
                self.event({"type": "content_block_stop", "index": index})
                self.event({"type": "message_delta", "delta": {
                    "stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 4}})
                self.event({"type": "message_stop"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def enqueue(self, *replies: Reply):
        for reply in replies:
            if reply.gate is not None:
                self.gates.append(reply.gate)
            self.replies.put(reply)

    def take_request(self):
        return self.requests.get(timeout=IO_TIMEOUT)

    def close(self):
        for gate in self.gates:
            gate.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=IO_TIMEOUT)
        assert not self.thread.is_alive(), "Provider stub did not stop"

