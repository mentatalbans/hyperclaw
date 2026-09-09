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
