"""Explicit opt-in for live model calls; safe to copy into miniature suites."""

import pytest

def pytest_addoption(parser):
    group = parser.getgroup("hyperclaw battery")
    group.addoption("--run-ollama", action="store_true", help="Run tests against a live Ollama server")
    group.addoption("--ollama-url", default="http://127.0.0.1:11434", help="Live Ollama endpoint")
    group.addoption("--ollama-model", default="qwen3.8:27b-mlx", help="Installed model used by live tests")


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-ollama"):
        return
    selected = []
    excluded = []
    for item in items:
        (excluded if item.get_closest_marker("ollama") else selected).append(item)
    items[:] = selected
    if excluded:
        config.hook.pytest_deselected(items=excluded)
