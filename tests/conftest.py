"""Explicit opt-in for live model calls; safe to copy into miniature suites."""

import pytest

def pytest_addoption(parser):
    group = parser.getgroup("hyperclaw battery")
    group.addoption("--mcp-docs-image", default="", help="Explicit immutable documentation peer image; never builds or pulls")
    group.addoption("--run-docker", action="store_true", help="Run tests against the local Docker daemon")
    group.addoption("--run-browser", action="store_true", help="Run tests in an explicitly installed browser")
    group.addoption("--browser-channel", default="chromium", help="Playwright browser channel (default: chromium)")
    group.addoption("--browser-screenshot-dir", default="test-results/browser", help="Directory for browser screenshots")
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
        requires_browser = item.get_closest_marker("browser") is not None
        enabled = ((not requires_ollama or config.getoption("--run-ollama"))
                   and (not requires_docker or config.getoption("--run-docker"))
                   and (not requires_browser or config.getoption("--run-browser")))
        (selected if enabled else excluded).append(item)
    items[:] = selected
    if excluded:
        config.hook.pytest_deselected(items=excluded)


@pytest.fixture
def browser_page(request):
    if not request.config.getoption("--run-browser"):
        pytest.fail("Browser test selected without --run-browser.", pytrace=False)
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        pytest.fail(
            "Browser tests require the optional dependency: uv sync --locked --extra dev --extra browser",
            pytrace=False,
        )
    channel = request.config.getoption("--browser-channel")
    with sync_playwright() as playwright:
        options = {"headless": True}
        if channel != "chromium":
            options["channel"] = channel
        try:
            browser = playwright.chromium.launch(**options)
        except Error as exc:
            pytest.fail(
                f"Requested browser channel {channel!r} is unavailable. Install it explicitly "
                f"(for bundled Chromium: playwright install {channel}) or select an installed channel. {exc}",
                pytrace=False,
            )
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        try:
            yield page
        finally:
            context.close()
            browser.close()
