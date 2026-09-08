# Consolidation verification — 2026-09-08

Branch: `feat/consolidate-ollama`.

## Verified behavior

- Both HTTP imports expose the same FastAPI app. Its lifespan owns task workers, sessions, and explicitly enabled channel services.
- Synthetic transport and HTTP tests cover session isolation, restart, reset, legacy import/reset races, cancellation cleanup, memory storage, bounded fallback, attachment capabilities, provider identity, tool delivery, and usage accounting.
- Task tests prove a queued task and competing explicit calls share one execution and terminal result. State and memory persistence tests exercised disposable local PostgreSQL over a private Unix socket. Existing singular state tables are retained; no live database was migrated.
- The development environment was installed into `.venv` with Python 3.11 from `.[dev]`. Test dependencies now include the AWS SDK required by existing tests.
- Docker built successfully, a temporary container's `/health` and Docker healthcheck passed, and installed wheel imports were checked outside the source directory. Schema, configuration, and dashboard assets were present. Private workspace files, credentials, and `.engram` were excluded. After the final Python changes, image `hyperclaw:consolidation-review` was rebuilt and both health checks passed again. Temporary containers were removed.

## Live local model checks

Ollama 0.33.3 was already running at `http://127.0.0.1:11434`, with `qwen3.8:27b-mlx` installed. Live checks used synthetic data and isolated temporary workspace roots.

- Plain chat, separate Alice/Bob history, and follow-up recall passed.
- Qwen used `write_file` and `read_file`; the resulting file matched the requested marker exactly.
- Streamed JSON text fragments reconstructed `STREAM_OK` and ended with `[DONE]`.
- A fresh server resumed the earlier conversation. A second restart preserved the reset marker and an empty conversation.
- Explicit remember/recall worked immediately and after restart.
- Terminal chat offered the existing 87-tool catalog. Qwen selected `bash` exactly once to print `TERMINAL_TOOL_OK`; execution and the final answer contained that marker. The smoke harness blocked unrelated tool execution.

The saved profile at `~/.hyperclaw/config/local.json` selects Ollama, disables thinking, enables interactive tools, and disables database connections, Telegram polling, and scheduling. Exported environment variables can override a loaded profile; rerunning `local` explicitly rewrites its defaults.

## Limits

- Qwen occasionally emitted a literal `</think>` token in its prose despite thinking being disabled. Tool execution and returned files were correct; output text was not silently rewritten.
- PDF documents are not advertised as supported by this Ollama configuration. Image routing is covered with synthetic transport tests; no private screenshot was captured or sent.
- PostgreSQL vector search and the full pgvector schema were not exercised; the local PostgreSQL installation lacks that extension.
- External messaging, trading, and hosted model APIs were not activated. Auxiliary integrations retain their own setup requirements. Separate research workflows remain outside the interactive runtime consolidation.
- Cloud prices are estimates from configured per-model rates. Unknown rates are marked explicitly, and budgets influence routing rather than acting as a hard spending cap.

## Final result

The complete source suite passed: **575 passed, 1 skipped, 8 warnings** in 6.91 seconds. Warnings come from the existing HyperShield audit logger calls and a Starlette dependency deprecation. `git diff --check` passed.

The native server is running at **http://127.0.0.1:8001**, using the saved profile in `~/.hyperclaw`. `/health` reports healthy, the dashboard returns HTTP 200, and `/api/models` reports only `ollama / qwen3.8:27b-mlx`. Start terminal chat with `.venv/bin/hyperclaw chat`.
