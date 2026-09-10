"""Explicit opt-in for live model calls; safe to copy into miniature suites."""

import pytest

def pytest_addoption(parser):
    group = parser.getgroup("hyperclaw battery")
    group.addoption("--mcp-docs-image", default="", help="Explicit immutable documentation peer image; never builds or pulls")
    group.addoption("--run-docker", action="store_true", help="Run tests against the local Docker daemon")
    group.addoption("--run-ollama", action="store_true", help="Run tests against a live Ollama server")
    group.addoption("--ollama-url", default="http://127.0.0.1:11434", help="Live Ollama endpoint")
    group.addoption("--ollama-model", default="qwen3.8:27b-mlx", help="Installed model used by live tests")


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    selected = []
    excluded = []
    for item in items:
        requires_ollama = item.get_closest_marker("ollama") is not None
        requires_docker = item.get_closest_marker("docker") is not None
        enabled = (not requires_ollama or config.getoption("--run-ollama")) and (not requires_docker or config.getoption("--run-docker"))
        (selected if enabled else excluded).append(item)
    items[:] = selected
    if excluded:
        config.hook.pytest_deselected(items=excluded)
