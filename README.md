# HyperClaw runtime v2

A local assistant being rebuilt as one Python package. M1 delivers durable text chat; implementation progress and feature destinations are in [testing](docs/testing.md). The old platform remains in git at dcad202.

Python 3.11+ on macOS/Linux. Install with `uv sync --locked --extra dev`.

```sh
uv run --locked hyperclaw --help
uv run --locked hyperclaw init
uv run --locked hyperclaw doctor
uv run --locked hyperclaw doctor --probe
make test
```

The default root is `~/.hyperclaw-v2`, selected by `--root`, then `HYPERCLAW_ROOT`. Settings are CLI overrides over `root/config.toml` over shipped defaults. Default model: `qwen3.8:27b-mlx` at `http://127.0.0.1:11434`. No automatic model downloads or cloud fallback. Ordinary setup and doctor do not contact a model; `--probe` checks the installed catalog.

Initialization refuses nonempty unmarked roots and v1 data. Existing tokens are reused with local permissions. No legacy client compatibility, migration, tools, images, memory, scheduler, web client or messaging adapters are promised by M1.

Start the daemon in one terminal, then submit from another:

```sh
hyperclaw --root /tmp/hyperclaw-demo init
hyperclaw --root /tmp/hyperclaw-demo serve --port 8011
hyperclaw --root /tmp/hyperclaw-demo chat "Describe a paper kite."
hyperclaw --root /tmp/hyperclaw-demo chat "Draft a short note." --detach
hyperclaw --root /tmp/hyperclaw-demo run inspect RUN_ID
hyperclaw --root /tmp/hyperclaw-demo run events RUN_ID --after 3
hyperclaw --root /tmp/hyperclaw-demo run cancel RUN_ID
hyperclaw --root /tmp/hyperclaw-demo session reset SESSION_ID
```

Use a fresh demo directory. The default listener is `127.0.0.1:8011`; `serve --port 0` selects a free port. `root/daemon.json` contains discovery metadata, and `root/token` contains the private bearer token. All `/v1` requests require `Authorization: Bearer TOKEN`; public `/healthz` returns only liveness. Host must match the selected loopback endpoint. Cross-origin requests are disabled.

`chat` reports session/run IDs on stderr; reuse `--session SESSION_ID` to continue. `--request-id ID` makes deliberate repeat submissions idempotent; changing the payload conflicts. `--retry-of RUN_ID` links an explicit new attempt to a failed, cancelled or interrupted run in the same session generation. There is no automatic retry. Only one active run occupies a session; other sessions queue behind one worker.

Ctrl-C while observing detaches. Explicit `run cancel` stops work. Only complete successful turns enter later prompts; partial text remains inspectable through events. Reset increments the generation, hiding old messages from new prompts without deleting old runs; reset fails while that session has active work. Shutdown interrupts active work and preserves queued work. After abrupt process death, startup marks active work interrupted before starting the queue.

M1 supports text/thinking and usage. General answers have `verification=not_requested`; response completion is not proof of an external task. Tools, images and controlled execution remain M2, schedules M3, explicit memory M4, MCP/skills M5, and web/Telegram M6.
