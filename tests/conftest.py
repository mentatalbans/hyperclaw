"""Explicit opt-in for model calls and required disposable database checks."""

import pytest

def pytest_addoption(parser):
    group = parser.getgroup("hyperclaw battery")
    group.addoption("--run-ollama", action="store_true", help="Run tests against a live Ollama server")
    group.addoption("--ollama-url", default="http://127.0.0.1:11434", help="Live Ollama endpoint")
    group.addoption("--ollama-model", default="qwen3.8:27b-mlx", help="Installed model used by live tests")
    group.addoption("--require-postgres", action="store_true", help="Fail if disposable PostgreSQL cannot run")
    group.addoption("--postgres-bin", default=None, help="Explicit PostgreSQL bin directory (no autodetection)")


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    for item in items:
        if "temporary_postgres" in item.fixturenames:
            item.add_marker(pytest.mark.postgres)
    if config.getoption("--run-ollama"):
        return
    selected = []
    excluded = []
    for item in items:
        (excluded if item.get_closest_marker("ollama") else selected).append(item)
    items[:] = selected
    if excluded:
        config.hook.pytest_deselected(items=excluded)
