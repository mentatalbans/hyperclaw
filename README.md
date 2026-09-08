# HyperClaw

A configurable AI assistant with durable conversations, explicit memory, local tool execution, and coordinated agent tasks.

[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)

## Run locally with Ollama Qwen

Use Python 3.11 or newer. From this checkout, install into a virtual environment with [uv](https://docs.astral.sh/uv/):

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e '.[dev]'
```

The local default is `qwen3.8:27b-mlx` on Ollama at `http://127.0.0.1:11434`. This is the MLX model used for the Apple Silicon setup. Start Ollama and check that the model is installed:

```bash
ollama list
# If the model is missing:
ollama pull qwen3.8:27b-mlx
```

Save the local profile, then start the HTTP server:

```bash
.venv/bin/hyperclaw local --setup-only
.venv/bin/hyperclaw server
```

Open **http://127.0.0.1:8001**. Setup checks Ollama's installed model list; it does not download models. No cloud API key or database is required.

For terminal chat:

```bash
# Configure the local defaults and open chat:
.venv/bin/hyperclaw local --chat

# Reuse the saved profile and a named conversation:
.venv/bin/hyperclaw chat --session my-session

# Chat without executing tools:
.venv/bin/hyperclaw chat --session my-session --no-tools
```

Inside terminal chat, `/reset` persistently clears the current session and `/quit` exits. `hyperclaw start` is also a terminal-chat entrypoint.

To select a different installed Ollama model or endpoint:

```bash
.venv/bin/hyperclaw local --model YOUR_INSTALLED_MODEL \
  --base-url http://127.0.0.1:11434 --setup-only
```

## Configuration and local defaults

The local command saves `~/.hyperclaw/config/local.json`, or `$HYPERCLAW_ROOT/config/local.json` when that root is set. It preserves existing credential files. Re-running `local` writes the requested model and restores the local defaults:

| Setting | Local profile |
| --- | --- |
| Provider | `HYPERCLAW_PROVIDER=ollama` |
| Model | `OLLAMA_MODEL=qwen3.8:27b-mlx` |
| Endpoint | `OLLAMA_BASE_URL=http://127.0.0.1:11434` |
| Tools | `HYPERCLAW_ENABLE_TOOLS=1` |
| Thinking | `OLLAMA_THINK=0` |
| Telegram polling | `HYPERCLAW_ENABLE_TELEGRAM=0` |
| Scheduler | `HYPERCLAW_ENABLE_SCHEDULER=0` |
| Database connection | `HYPERCLAW_ENABLE_DATABASE=0` |

Existing environment variables take precedence when loading a saved profile. Use `server` or `chat` after setup to retain those overrides. For example, `HYPERCLAW_ENABLE_DATABASE=true .venv/bin/hyperclaw server` explicitly enables database connection attempts when `DATABASE_URL` is configured.

Selecting `HYPERCLAW_PROVIDER=ollama` restricts model routing and fallback to Ollama. A failed local model request cannot fall through to a configured cloud provider. Enabled tools and integrations may independently access files, execute commands, or contact external services; use `--no-tools` in terminal chat or `"tools": false` in an HTTP request for chat without tools.

Provider definitions and capability routing live in the user's `config/models.yaml`, seeded from the shipped configuration. An explicit provider override must name a configured provider with its required credentials and endpoint. Ollama uses its Messages-compatible API for chat, streaming, image blocks, and tool blocks. PDF document support is not advertised for this local model.

Cloud cost estimates use explicit per-model rates in `models.yaml`. Missing rates are marked unknown; the reported total is then only the known subtotal, and routing switches to the compatible fast slot. The daily budget guides routing rather than imposing a hard spending cap. Ollama has no API usage charge in this accounting.

The optional [.env.example](.env.example) documents runtime variables and integration credentials. You do not need to copy it for the local setup above. Telegram polling requires an explicit enable flag, a bot token, and an allowed chat ID. Telegram webhooks separately require `TELEGRAM_WEBHOOK_SECRET`, the matching `X-Telegram-Bot-Api-Secret-Token` header, and an allowed chat ID. See [SECURITY.md](SECURITY.md) for channel configuration.

## One HTTP application

`hyperclaw.server:app` is the canonical FastAPI application. `server:app` aliases the same object, and `run_hyperclaw.py` launches it. These are alternative entrypoints to one server:

```bash
.venv/bin/hyperclaw server --host 127.0.0.1 --port 8001
.venv/bin/python -m hyperclaw server --port 8001
.venv/bin/python run_hyperclaw.py
```

Native launch defaults to loopback on port `8001`. The Python launcher accepts `HOST` and `PORT`, with legacy `HYPERCLAW_PORT` as a fallback. The CLI accepts `--host` and `--port`.

One application lifespan starts the orchestrator and task workers, plus explicitly enabled Telegram polling and scheduling. It closes these services on shutdown. The `/api/swarm/*` compatibility routes use the same coordinator as `/api/tasks`; legacy agent IDs and unambiguous display names resolve to canonical agent IDs. Dashboard feeds, voice, trading, and other auxiliary routes remain available and require their own configuration.

Chat, streaming chat, Telegram, and terminal chat use the shared provider transport and durable conversation runtime. Each supplied `session_id` selects its own history. Reuse that ID to resume after a restart; reset persists across restarts. An HTTP request with a null session ID creates a new ID, while an omitted ID uses `default`.

Reset clears the session's conversation messages. Explicit remembered facts persist and remain available through recall.

File storage supports conversation history and explicit remember/recall without PostgreSQL. Optional database storage and the separate research, recursive, and civilization modules remain available. Those modules have their own workflows and setup requirements; this consolidation covers the interactive runtime and its coordinated task path.

## HTTP examples

API documentation is available at `/docs`.

```bash
# Chat in a named session without tools.
curl -sS http://127.0.0.1:8001/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Hello","session_id":"demo","tools":false}'

# Stream the next turn in that session.
curl -N http://127.0.0.1:8001/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"message":"Continue","session_id":"demo","tools":false}'

# Persistently reset only this conversation.
curl -sS -X POST 'http://127.0.0.1:8001/reset?session_id=demo'

# Explicit memory is searchable immediately and after restart.
curl -sS http://127.0.0.1:8001/api/memory/remember \
  -H 'Content-Type: application/json' \
  -d '{"content":"The demo project color is cobalt","domain":"demo"}'
curl -sS http://127.0.0.1:8001/api/memory/recall \
  -H 'Content-Type: application/json' \
  -d '{"query":"cobalt"}'

# Submit a task, then poll its returned task_id.
curl -sS http://127.0.0.1:8001/api/tasks \
  -H 'Content-Type: application/json' \
  -d '{"goal":"Outline a small example program","agent_id":"code_specialist"}'
curl -sS http://127.0.0.1:8001/api/tasks/TASK_ID

# Inspect runtime health and the configured models.
curl -sS http://127.0.0.1:8001/health
curl -sS http://127.0.0.1:8001/api/models
```

Streaming uses server-sent events: text payloads are JSON strings, thinking uses `event: thinking`, and successful completion sends `data: [DONE]`. A failure before output returns an HTTP error; an interrupted response emits `event: error` without a completion marker.

## Docker

With Ollama running on the host:

```bash
docker compose up --build
```

Compose selects Ollama, uses `http://host.docker.internal:11434`, and publishes **127.0.0.1:8001**. The container binds `0.0.0.0:8001`, matching its exposed port and healthcheck. Ensure that the host Ollama endpoint is reachable from Docker. A `hyperclaw_data` volume stores application data; Telegram, scheduling, database access, and tools are disabled by the Compose defaults.

If your shell or `.env` already sets `OLLAMA_BASE_URL` to host loopback, override it for the container:

```bash
OLLAMA_BASE_URL=http://host.docker.internal:11434 docker compose up --build
```

The build context excludes `.env` files, local workspace data, virtual environments, and logs while retaining `.env.example`. Mounted JSON secrets under `/mnt/secrets` (or `SECRETS_MOUNT`) are decoded into environment variables without shell evaluation; existing environment values take precedence.

## Troubleshooting

- **Local setup cannot find the model:** check `ollama list` and the endpoint supplied to `--base-url`.
- **No compatible provider:** inspect `/api/models` and the effective `HYPERCLAW_PROVIDER`, `OLLAMA_MODEL`, and `OLLAMA_BASE_URL` values. An explicit local provider stays local when unavailable.
- **Old database credentials trigger a connection:** set `HYPERCLAW_ENABLE_DATABASE=false`, or run `local --setup-only` to save the local defaults.
- **Conversation does not resume:** use the same `HYPERCLAW_ROOT` and `session_id` as the original turn.

## Development

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e '.[dev]'
PYTHON_DOTENV_DISABLED=1 HYPERCLAW_ROOT="$(mktemp -d)" \
  .venv/bin/python -m pytest tests/ -q
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines. HyperClaw is [MIT licensed](LICENSE).
