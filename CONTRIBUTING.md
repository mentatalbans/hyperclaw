# Contributing

Runtime v2 lives in `src/hyperclaw`; CI is configured for Python 3.11 and 3.13. Use `uv sync --locked --extra dev --extra mcp`, then `make test`. Narrow pytest runs use `.venv/bin/python -m pytest PATH -q`. Keep tests compatible with a base installation without the optional MCP package.

Follow the September 9 design and milestone plans under docs/superpowers. Test behavior through public contracts, loopback providers and disposable subprocesses. Select live Ollama tests explicitly; never use personal runtime roots or modify running services during tests. Preserve research/planning history. Commit dependency changes in uv.lock.

`make test-coverage` reports statement and branch counts separately; no percentage threshold substitutes for scenario coverage. CI uses locked dependencies, runs the quick battery on Python 3.11 and 3.13, smoke-installs the wheel outside the checkout, and uploads reports even on failure. A configured CI job is not evidence of an executed hosted run.

Docker, model and browser gates require explicit selection. `make test-live` selects the installed model; `make test-live-mcp MCP_DOCS_IMAGE=sha256:...` includes the admitted documentation peer. `make test-all MCP_DOCS_IMAGE=sha256:...` combines offline, model and Docker checks with coverage. Build required images explicitly and pass their immutable IDs.

For browser tests, include `--extra browser` in the sync command, then run `make test-browser BROWSER_CHANNEL=chrome` with an existing Chrome installation. The default Chromium channel requires a separate, explicit `.venv/bin/python -m playwright install chromium`. Browser tests are a separate gate from `make test-all`; ordinary startup and quick tests never install or launch a browser. Keep the three packaged web assets local and verify changes in a real browser.

See [the testing guide](docs/testing.md) for exact setup, supported behavior and milestone evidence.
