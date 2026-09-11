# Consolidation and Ollama implementation plan

**Goal:** Run the shared HyperClaw application with local Qwen, durable isolated sessions, finite provider fallback, and single task execution.

**Architecture:** Retain the provider registry and orchestrator as the public seams. Centralize inference, preserve compatibility imports, and make persistence and task ownership explicit.

**Tech stack:** Python 3.11+, FastAPI, httpx transport with Messages/OpenAI adapters, Ollama Messages compatibility, JSON file persistence and optional asyncpg.

**Spec:** `docs/superpowers/specs/2026-09-08-consolidate-ollama-design.md`

Independent persistence and task/schema work can run in parallel under the dispatching-parallel-agents skill; parent owns inference and entrypoint integration. All changes stay on `feat/consolidate-ollama` and are reviewed together before completion.

## 1. Durable memory and sessions

- [x] Add failing public-method tests for remember/recall/restart, session save/load/reset, arbitrary session IDs, and metadata preservation.
- [x] Update `hyperclaw/memory_manager.py` only, adding `source` to remember and a durable `clear_conversation(session_id)` method. Retain existing async method signatures and legacy Markdown reads.
- [x] Verify the new tests with an isolated HYPERCLAW_ROOT and no network.

## 2. Task ownership and schema

- [x] Add concurrency tests proving `coordinate`, workers, and explicit `execute_task` share one execution per task.
- [x] Update `hyperclaw/agent_coordinator.py` with per-task synchronization; preserve submit/execute/coordinate APIs and terminal outcomes.
- [x] Reconcile `core/hyperstate/store.py` and shipped schema setup without destructive migration. Test fresh save/load/history against real PostgreSQL if available; otherwise report the validation limit explicitly.

## 3. Shared inference and Ollama

- [x] Add failing transport tests for local-only routing, missing cloud keys, finite fallback, explicit model identity, and streamed interruption.
- [x] Implement `hyperclaw/inference.py`; adapt `providers.py`, `model_router.py`, and `solomon.py` to use it. Default provider config includes opt-in Ollama; explicit `HYPERCLAW_PROVIDER=ollama` restricts fallback to Ollama.
- [x] Route terminal and bridge calls through this transport while retaining their tool definitions. Add a bounded shared tool loop for interactive adapters.
- [x] Verify against a mocked HTTP transport, then the installed local Qwen model with synthetic input.

## 4. One application and lifecycle

- [x] Add HTTP tests for both app imports, isolated sessions, durable reset, remember/recall, and startup without Telegram credentials.
- [x] Integrate durable memory and inference in `orchestrator.py`. Move auxiliary root-server routes into a router and make `server.py` import the canonical app.
- [x] Make `run_hyperclaw.py` a single launcher. Align Docker port/bind/healthcheck and exclude local credentials/data from build context. Keep channel allowlists on every enabled channel route.
- [x] Provide `hyperclaw local` setup/launch with Qwen defaults and no cloud credential requirement; retain documented server/chat commands as aliases.

## 5. Verification and delivery

- [x] Run the complete suite from a fresh install using declared development dependencies.
- [x] Run a real local chat, stream, tool round trip, two-session isolation, remember/recall, and restart/resume smoke check.
- [x] Review the final diff against the spec and repository standards; resolve material findings.
- [x] Document actual commands, running address, tested behavior, and remaining architectural limits. Leave the requested local setup ready to run.

Verification evidence: [2026-09-08-consolidation-verification.md](2026-09-08-consolidation-verification.md). Final full suite: 575 passed, 1 skipped; live Qwen and Docker health checks passed.
