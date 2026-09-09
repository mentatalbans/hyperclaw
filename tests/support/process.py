"""Owned subprocesses and public SSE decoding; no ambient configuration or PYTHONPATH."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time

import httpx

from hyperclaw.config import initialize_root, load_settings, read_token
from tests.support.provider import MODEL


class Process:
    def __init__(self, root: Path, provider_url: str, model=MODEL, timeout=5, thinking=False):
        self.root = root
        self.timeout = timeout
        self.process = None
        self.client = None
        self.logs = []
        self._cwd = None
        initialize_root(load_settings(root=root, environ={}, overrides={
            'ollama_url': provider_url, 'model': model, 'port': 0, 'thinking': thinking,
            'request_timeout_s': float(timeout), 'run_timeout_s': float(timeout * 2),
        }))

    def start(self):
        assert self.process is None, 'Stop owned child before starting another'
        env = {key: os.environ[key] for key in (
            'PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT', 'COVERAGE_PROCESS_CONFIG', 'COVERAGE_FILE',
        ) if key in os.environ}
        env['PYTHONUNBUFFERED'] = '1'
        self._cwd = tempfile.TemporaryDirectory(prefix='hyperclaw-process-cwd-')
        self.process = subprocess.Popen([sys.executable, '-m', 'hyperclaw', '--root', str(self.root), 'serve', '--port', '0'],
            cwd=self._cwd.name, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        output = self.process.stdout
        def read():
            for line in output:
                self.logs.append(line)
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        deadline = time.monotonic() + 20
        try:
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise AssertionError('Daemon exited during startup\n' + self.diagnostics())
                try:
                    metadata = json.loads((self.root / 'daemon.json').read_text())
                    if metadata['pid'] != self.process.pid:
                        time.sleep(0.01)
                        continue
                    self.url = metadata['url']
                    with httpx.Client(trust_env=False, timeout=0.3) as probe:
                        response = probe.get(self.url + '/healthz')
                        if response.status_code == 200:
                            self.client = httpx.Client(base_url=self.url, trust_env=False, timeout=self.timeout + 5,
                                headers={'Authorization': 'Bearer ' + read_token(self.root)})
                            return self
                except (OSError, ValueError, KeyError, httpx.HTTPError):
                    pass
                time.sleep(0.02)
            raise AssertionError('Daemon readiness timed out\n' + self.diagnostics())
        except BaseException:
            self.stop()
            raise

    def _stop(self, abrupt):
        if self.client:
            self.client.close()
            self.client = None
        if self.process is None:
            return
        process = self.process
        if process.poll() is None:
            process.kill() if abrupt else process.terminate()
        forced = False
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            forced = True
            process.kill()
            process.wait(timeout=5)
        self.reader.join(timeout=5)
        process.stdout.close()
        self.process = None
        self._cwd.cleanup()
        assert not self.reader.is_alive(), 'Child log reader leaked'
        assert not forced, self.diagnostics()
        if not abrupt:
            assert process.returncode in (0, -signal.SIGTERM), self.diagnostics()

    def stop(self):
        self._stop(False)

    def kill(self):
        self._stop(True)

    def restart(self):
        self.stop()
        return self.start()

    def diagnostics(self):
        return ''.join(self.logs[-100:])


def sse_events(response):
    response.raise_for_status()
    data, seq, kind = [], None, None
    for line in response.iter_lines():
        if not line:
            if data:
                value = json.loads('\n'.join(data))
                assert int(seq) == value['seq'] and kind == value['kind']
                yield value
            data, seq, kind = [], None, None
        elif line.startswith('data:'):
            data.append(line[5:].lstrip())
        elif line.startswith('id:'):
            seq = line[3:].strip()
        elif line.startswith('event:'):
            kind = line[6:].strip()
    assert not data, 'Stream ended inside an SSE frame'


def submit(app, text='hello', session=None, request_id='first', **changes):
    session = session or app.client.post('/v1/sessions', json={}).json()
    response = app.client.post('/v1/runs', json={
        'session_id': session['id'], 'generation': session['generation'], 'request_id': request_id,
        'text': text, **changes,
    })
    assert response.status_code == 202, response.text
    return response.json()


def events(app, run_id, after=0):
    with app.client.stream('GET', f'/v1/runs/{run_id}/events', params={'after': after}) as response:
        return list(sse_events(response))
