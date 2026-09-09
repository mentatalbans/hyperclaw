# Contributing

Runtime v2 lives in src/hyperclaw; Python 3.11 and 3.13 are tested in CI. Use `uv sync --locked --extra dev`, then `make test`. Narrow pytest runs use `uv run --locked pytest PATH -q`.

Follow the September 9 design and milestone plans under docs/superpowers. Test behavior through public contracts, loopback providers and disposable subprocesses. Select live Ollama tests explicitly; never use personal runtime roots or modify running services during tests. Preserve research/planning history. Commit dependency changes in uv.lock.
