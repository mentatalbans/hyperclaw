"""Real CLI process and a loopback Messages API provider, with bounded cleanup."""

from __future__ import annotations

import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx


REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL = "process-test-model"
STARTUP_TIMEOUT = 20
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


class HyperClawProcess:
    def __init__(self, root: Path, provider_url: str, source: Path = REPO_ROOT):
        self.root = root
        self.source = source
        self.process = None
        self.client = None
        self.logs: list[str] = []
        config = root / "config"
        config.mkdir(parents=True)
        (config / "local.json").write_text(json.dumps({
            "HYPERCLAW_PROVIDER": "ollama", "OLLAMA_MODEL": MODEL,
            "OLLAMA_BASE_URL": provider_url, "OLLAMA_THINK": "0",
            "HYPERCLAW_ENABLE_TOOLS": "0", "HYPERCLAW_ENABLE_TELEGRAM": "0",
            "HYPERCLAW_ENABLE_SCHEDULER": "0", "HYPERCLAW_ENABLE_DATABASE": "0",
        }))
        # JSON is valid YAML; no developer model ladder or agent config is inherited.
        (config / "models.yaml").write_text(json.dumps({
            "providers": {"ollama": {"kind": "anthropic",
                "base_url_env": "OLLAMA_BASE_URL", "model_env": "OLLAMA_MODEL",
                "capabilities": ["chat", "streaming", "thinking"]}},
            "slots": {slot: ["ollama"] for slot in ("primary", "fast", "tools", "vision")},
        }))
        (config / "agents.yaml").write_text("agents: []\n")

    def start(self):
        assert self.process is None, "Stop the previous child before restarting"
        # An allowlist prevents ambient credentials, proxies and service flags from
        # making a test contact developer services or overwrite their configuration.
        # Coverage.py serializes its active settings here for Python children.
        env = {key: os.environ[key] for key in (
            "PATH", "LANG", "LC_ALL", "SYSTEMROOT", "COVERAGE_PROCESS_CONFIG",
        ) if key in os.environ}
        env.update({
            "PYTHONPATH": str(self.source), "PYTHONUNBUFFERED": "1",
            "PYTHON_DOTENV_DISABLED": "1", "HYPERCLAW_ROOT": str(self.root),
            "SECRETS_MOUNT": str(self.root / "absent-secrets"),
            "HYPERCLAW_REQUEST_TIMEOUT": str(IO_TIMEOUT),
            "NO_PROXY": "*",
        })
        self.process = subprocess.Popen(
            [sys.executable, "-m", "hyperclaw", "server", "--host", "127.0.0.1", "--port", "0"],
            cwd=self.root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        ready: queue.Queue[str | None] = queue.Queue()
        output = self.process.stdout

        def read_output():
            for line in output:
                self.logs.append(line)
                match = re.search(r"Uvicorn running on (http://127\.0\.0\.1:\d+)", line)
                if match:
                    ready.put(match.group(1))
            ready.put(None)

        self.reader = threading.Thread(target=read_output, daemon=True)
        self.reader.start()
        try:
            url = ready.get(timeout=STARTUP_TIMEOUT)
            assert url, f"CLI exited during startup\n{self.diagnostics()}"
            self.client = httpx.Client(base_url=url, timeout=IO_TIMEOUT, trust_env=False)
            # Uvicorn emits its bound address after application startup and listen.
            response = self.client.get("/health")
            assert response.status_code == 200, response.text
            assert response.json()["components"]["orchestrator"] is True, response.text
        except BaseException:
            self.stop()
            raise
        return self

    def stop(self):
        if self.client is not None:
            self.client.close()
            self.client = None
        if self.process is None:
            return
        process = self.process
        if process.poll() is None:
            process.terminate()
        forced = False
        try:
            process.wait(timeout=IO_TIMEOUT)
        except subprocess.TimeoutExpired:
            forced = True
            process.kill()
            process.wait(timeout=IO_TIMEOUT)
        self.reader.join(timeout=IO_TIMEOUT)
        process.stdout.close()
        self.process = None
        assert not forced, f"HyperClaw did not shut down gracefully\n{self.diagnostics()}"
        # Recent uvicorn versions re-raise SIGTERM after graceful ASGI cleanup.
        assert process.returncode in (0, -signal.SIGTERM), f"CLI exit {process.returncode}\n{self.diagnostics()}"

    def restart(self):
        self.stop()
        return self.start()

    def diagnostics(self):
        return "".join(self.logs[-150:])


def sse_events(response):
    """Decode complete public SSE frames; preserve the explicit DONE sentinel."""
    kind, data = "message", []
    for line in response.iter_lines():
        if not line:
            if data:
                raw = "\n".join(data)
                yield kind, raw if raw == "[DONE]" else json.loads(raw)
            kind, data = "message", []
        elif line.startswith("event:"):
            kind = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    assert not data, "Stream ended inside an SSE frame"
