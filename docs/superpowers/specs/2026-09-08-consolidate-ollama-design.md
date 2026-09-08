# Consolidated runtime and local Qwen

The user approved the repository review's recommendation to consolidate execution paths, and requested setup with Ollama Qwen 3.8 MLX. This is the implementation contract for that work.

## Result

The HTTP entrypoints share one application and orchestrator. Chat, streaming chat, Telegram, and terminal chat resolve providers through the same registry and inference adapter. Local configuration selects only Ollama, so a failed local request cannot silently send conversation data to a cloud provider. The installed model is `qwen3.8:27b-mlx`, served at `http://127.0.0.1:11434` by Ollama 0.33.3. It advertises chat, image, tool, and thinking capabilities. Use Ollama's Messages-compatible API to retain the existing tool block format; do not mislabel it as a native Anthropic model or promise PDF document support.

## Boundaries

- `hyperclaw/providers.py` owns provider availability, capability filtering, explicit provider selection, and model identities. `hyperclaw/inference.py` owns HTTP transport and message adaptation, finite failover, and stream interruption behavior. The existing `ModelRouter` remains a compatibility facade for task callers, budget policy, and usage accounting.
- `MemoryManager` owns durable sessions and explicit memories. File storage must work without PostgreSQL, preserve IDs and metadata, update recall immediately, and resume across fresh manager instances. Session identifiers must never become arbitrary filesystem paths. The orchestrator serializes turns per session, saves after each turn, and exposes persistent reset.
- `AgentCoordinator` owns task execution. Queued and explicit execution must converge on a single task result, including concurrent requests. Consolidation fixes existing state-schema mismatches without dropping user tables or data.
- The packaged FastAPI app is canonical. The root server and launcher become compatibility entrypoints. Existing dashboard auxiliary routes remain available through a router. A single application lifespan owns workers, optional Telegram polling, and scheduler startup/shutdown.
- Existing tool definitions are retained. Terminal/tool adapters use the shared provider transport; the primary API/chat path exposes explicitly enabled tool execution with bounded rounds. No new external integrations are enabled during validation.

## Acceptance

1. Plain and streamed chat work with Ollama and require no cloud credentials.
2. A real Qwen turn calls a harmless local tool and incorporates its result.
3. Two sessions never share conversation history. A completed turn survives a fresh runtime; reset also survives restart.
4. Explicit remember/recall works immediately and after restart with no database; automatic storage cannot discard a successful model response.
5. A coordinated task executes once, including competing worker/explicit execution.
6. Fallback visits configured compatible candidates once and does not restart a partially emitted answer.
7. Docker bind address, exposed port, and healthcheck agree; package installation includes required runtime data and declared test dependencies.
8. Existing unit tests and new seam/HTTP tests pass. Live model checks use synthetic content and harmless tools only.

## Scope limits

This converges the interactive application and its task path. It does not redesign the separate research algorithms, retrain models, migrate unrelated live databases, activate trading, or connect messaging accounts. Compatibility modules may remain where removing them would break imports. Existing user credentials and other local services are preserved.

## Sources

- https://docs.ollama.com/api/anthropic-compatibility (Messages, streaming, tool blocks, base64 images)
- https://ollama.com/library/qwen3.8:27b-mlx
- Local `/api/tags`, `/api/ps`, `/api/version` verified 2026-09-08.
