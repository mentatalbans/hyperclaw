"""Live tests use an explicitly selected, already-installed model and disposable daemon."""
from dataclasses import dataclass

import httpx
import pytest

from tests.support.process import Process


@dataclass(frozen=True)
class Target:
    url: str
    model: str


@pytest.fixture
def ollama_target(request):
    target = Target(request.config.getoption('--ollama-url').rstrip('/'), request.config.getoption('--ollama-model'))
    try:
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=10) as client:
            response = client.get(target.url + '/api/tags')
            response.raise_for_status()
            catalog = response.json()['models']
            assert any(m.get('name') == target.model or m.get('model') == target.model for m in catalog), 'configured model is absent'
    except (httpx.HTTPError, ValueError, KeyError, AssertionError) as exc:
        pytest.fail(f'Explicit live target unavailable: {target.url}, model={target.model}, reason={type(exc).__name__}. No download or fallback.', pytrace=False)
    return target


@pytest.fixture
def live_app(tmp_path, ollama_target):
    app = Process(tmp_path / 'live-runtime', ollama_target.url, model=ollama_target.model, timeout=120)
    try:
        app.start()
        yield app
    finally:
        app.stop()
