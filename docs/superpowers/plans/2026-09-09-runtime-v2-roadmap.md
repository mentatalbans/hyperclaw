# HyperClaw Runtime v2 Roadmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the sprawling platform with a dependable local assistant, shipping six demonstrable milestones.

**Architecture:** A fresh Python package owns one durable run engine, SQLite state, explicit execution policy, and the existing Ollama model. CLI, HTTP, scheduling, and optional Telegram submit to the same runtime.

**Tech Stack:** Python >=3.11, sqlite3, httpx, Pydantic, FastAPI, Uvicorn, Typer/Rich, pytest, uv; Docker and MCP enter their own milestones.

**Spec:** [Runtime v2 design](../specs/2026-09-09-runtime-v2-design.md)

## Global Constraints

- Python >=3.11; CI on Python 3.11 and 3.13; initial runtime support is macOS and Linux.
- One installable package at src/hyperclaw and one CLI entrypoint: hyperclaw.
- One daemon owns each runtime root, its SQLite connection, model requests, and tool processes.
- Default runtime root is ~/.hyperclaw-v2; default HTTP listener is 127.0.0.1:8011.
- Default model is qwen3.8:27b-mlx at http://127.0.0.1:11434; no cloud fallback or automatic model download.
- Existing ~/.hyperclaw data, .engram, other worktrees, and running services are outside the refactor's mutation scope.
- No old HTTP routes, import paths, environment aliases, database formats, or dashboard feature parity are required.
- Persistent side effects must pass through the execution policy and have a recorded invocation.
- Client disconnect does not cancel a run; cancellation is an explicit operation.
- Unknown tool outcomes are recorded as uncertain and are never automatically retried.
- Offline tests use temporary roots and loopback fixtures; real Ollama and Docker tests require explicit selection.
- Imported skills, retrieved text, and model output cannot grant execution authority.

---

## Reading order and branch

Read the design first, then this roadmap, then the M1 task plan. M2–M6 are bounded milestone briefs, not pretense that every downstream implementation detail is already proven. Expand each into a task plan against the actual preceding milestone before writing that subsystem. Keep the decisions and acceptance gates below unless new evidence justifies a recorded change.

Worktree: /Users/kairos/Projects/work/hyper/hyperclaw/.worktrees/runtime-refactor

Branch: refactor/runtime-v2

Original implementation/research reference: dcad202

Worktree housekeeping baseline: 6f75839

The worktree has its own Python 3.11.13 virtual environment. Fresh `make test` on 2026-09-09: **621 passed, 1 skipped, 30 deselected, 8 warnings, 47.35 seconds**. The excluded tests require PostgreSQL or live Ollama. The skip is the existing absent-stub-catalog check; warnings are the existing Starlette alias and unawaited legacy audit coroutines. Report: `test-results/quick-20260909T200920724780Z/summary.json` (ignored local artifact). This run verifies the starting tree, not the proposed implementation.

This planning change adds documents only in the refactor branch; no replacement source has been implemented. Two housekeeping changes on the starting branch ignore .worktrees in Git and Docker contexts. No runtime, model service, or user profile was modified.

## What to transplant, replace, and remove

| Existing paths | Treatment | Evidence/behavior to carry forward |
| --- | --- | --- |
| hyperclaw/inference.py, providers.py, local.py | Extract Ollama behavior into ollama.py/config.py; remove registry and fallback ladders | Text/thinking separation, image/tool blocks, usage, explicit local endpoint, malformed-stream failure |
| hyperclaw/orchestrator.py, tool_loop.py, agent_coordinator.py, agent.py | Replace with Runtime and execution modules | Session ordering, offered-tool allowlist, deadlines, repeated-call limits, cancellation regressions |
| hyperclaw/memory_manager.py, memory_tools.py, memory/, schema/ | Replace with SQLite transcript/run state in M1; explicit memory in M4 | Restart/reset isolation, durable mutation, visible scope before ranking, corrections |
| hyperclaw/server.py, terminal.py, cli/, root server.py/run_hyperclaw.py | Replace with one API factory and CLI | Real process/HTTP tests; provider failure and interrupted-stream behavior |
| tests/support/http_process.py, tests/integration/test_http_process.py, tests/live/test_ollama.py | Preserve useful fixture logic and port scenarios to new public APIs | Loopback provider, real subprocess lifecycle, five Qwen scenarios |
| scripts/test_battery.py, .coveragerc, .github/workflows/tests.yml, docs/testing.md | Rework selection around the new package; retain isolation and reports | Required dependencies fail when selected; subprocess coverage; no ambient secrets |
| security/ and scattered tool dispatch | Replace with policy in the actual invocation path | Keep abuse/cancellation cases, not disconnected classes or their private APIs |
| core/, models/, swarm/, agents/, recursive/, civilization/, integrations/ | Remove from replacement branch in M1 | Git history retains experiments; no promise to port them |
| hyperclaw/tui.py, cli_tui.py, dashboard_api.py, ui/ | Remove; small run-oriented web surface in M6 | Chat, inspect work, cancel, approve; no old dashboard parity |
| scheduler.py, skills.py, prometheus.py, solomon.py | Replace only the narrower scheduling/memory/skills use cases in M3–M5 | No model-scored auto-activation, fixed all-hands jobs, or hidden user-directory imports |
| Telegram launchers/daemons and alternate messaging gateways | Remove; one opt-in polling adapter in M6 | Sender authorization before downloads, stable session mapping, durable deduplication |
| Cloud deployment scripts, legacy installer/onboarding docs, extra entrypoints | Remove obsolete paths in M1 | New README must describe only runnable milestone behavior |

Retain LICENSE, research notes/PDF, design/planning history, and useful fixtures. Retire tests coupled to removed features explicitly; raw count parity with 651 earlier passing tests is not a goal. Before deleting code, record each retained scenario's new destination in docs/testing.md. In M1–M3 that document also identifies memory/MCP/channel scenarios whose owning milestone is not shipped.

Do not copy removed source into an archive package. The original checkout is the runnable fallback; dcad202 is the permanent source reference. No files under ~/.hyperclaw or private workspace roots are deletion candidates.

## Milestone sequence

~~~mermaid
flowchart LR
    M1[M1: durable local chat] --> M2[M2: controlled tools]
    M2 --> M3[M3: recovery and scheduling]
    M3 --> M4[M4: scoped memory]
    M4 --> M5[M5: one MCP server and skills]
    M5 --> M6[M6: web and Telegram]
~~~

### M1 — Replace the runtime with durable local chat

**Deliverable:** a fresh package that launches one authenticated daemon, chats with Ollama, persists sessions/runs, replays committed stream events, cancels explicitly, and reports interruption after process death.

**Plan:** [M1 implementation tasks](2026-09-09-runtime-v2-m1.md).

**Interface:** Runtime exposes create_session, get_session, submit, get_run, events, cancel, and reset_session; precise signatures are in M1. CLI/API own no model, storage, or task state.

**Files:** src/hyperclaw/{contracts,config,store,ollama,runtime,api,cli}.py; tests/{unit,integration,live,support}; pyproject.toml; uv.lock; Makefile; test runner and CI.

**Gate:**

- [ ] Real HTTP text arrives before the fixture provider finishes; usage and thinking remain distinct.
- [ ] Restart preserves completed history and replay cursors; a new session/reset sees no old prompt history.
- [ ] Duplicate request IDs return one run; conflicting payloads fail; a second owner cannot start.
- [ ] EOF without message_stop fails; no implicit provider/model retry occurs.
- [ ] Killing the process produces interrupted, not succeeded, on restart.
- [ ] Locked fresh install and three applicable live Qwen scenarios pass: text/usage, streaming/usage, conversation restart/isolation/reset.

The new API intentionally replaces legacy /chat and /api/swarm routes. Tools, memory, images, and channels are not advertised by this milestone.

### M2 — Give tools a real execution policy and owner

**Deliverable:** useful scoped file operations and container command execution, with durable approvals/receipts and verified cancellation. Restore image/tool model support.

**Consumes:** M1 Runtime, Store, run events, and Ollama content contracts.

**Produces:** `Executor.invoke(run_id: str, call: ToolCall) -> ToolReceipt`; `Executor.cancel(run_id: str) -> None`; `Executor.reconcile() -> list[ToolReceipt]`. ToolCall contains id/name/JSON arguments. ToolReceipt contains invocation_id, status, bounded output, artifact references, and verification evidence. Persist these through Store; do not add a second database connection.

**Files:** src/hyperclaw/execution/{policy,workspace,docker}.py; extend contracts.py/store.py/runtime.py/ollama.py/api.py/cli.py; containers/tool-runner/Dockerfile; tests/integration/test_execution.py; tests/live/test_ollama.py.

Start with a focused Docker ownership test before expanding the tool catalog. Resolve and commit the actual image digest during implementation, then test macOS Docker Desktop and Linux CI. The image needs only Python and basic filesystem utilities for the first workflow; package installation and general outbound network access are excluded.

**Gate:**

- [ ] An admitted workspace read/write succeeds; symlink, parent traversal, absolute path, and device access fail through public tools.
- [ ] An unknown/disallowed tool or changed arguments cannot reuse an approval.
- [ ] A workspace write grant avoids repeated prompts within that scope; missing command authority pauses durably for approval. Restart/resume uses the persisted call arguments and remaining active execution budget; approval expires after 24 hours.
- [ ] A container with a child that writes a delayed marker is cancelled; neither parent nor child can write afterward.
- [ ] Backend loss yields uncertain, without re-execution; restart reconciles owned containers before queued work.
- [ ] Denied writes cannot be achieved through a writable command mount; network and secret sentinels are unavailable.
- [ ] Tool call/result grouping, 12-round limit, repeated-call bound, total deadline, bounded output, and live synthetic image behavior pass.
- [ ] A generated file is reported with its observed hash, and a deliberately wrong expected hash fails verification.

### M3 — Prove background recovery and add durable schedules

**Deliverable:** detached work uses the same model/tool loop as chat; one-shot and fixed-interval schedules submit those runs durably.

**Consumes:** Runtime.submit and invocation reconciliation. **Produces:** `Scheduler.tick(now: datetime) -> list[str]` (new run IDs); persistent schedule records with id, session_id, generation, input, next_due_at, and optional interval_seconds.

**Files:** src/hyperclaw/scheduling.py; extend store.py/runtime.py/api.py/cli.py; tests/integration/test_recovery.py; tests/unit/test_scheduling.py.

Reserve a due occurrence and enqueue its run in one transaction, keyed by (schedule_id, nominal_due_at). The daemon polls due work; no broker, worker service, or APScheduler import is needed. Store UTC instants. After downtime, coalesce missed intervals into at most one submission and advance to the first future occurrence. A busy target session retains its pending occurrence; pause a schedule whose session generation has changed until the operator retargets it.

**Gate:**

- [ ] The same tool workflow passes as a foreground observation and a detached run.
- [ ] Process kills before invocation creation, after creation, after dispatch, and after receipt commit each produce the design's documented outcome.
- [ ] Completed receipts are not replayed; ambiguous mutations never retry automatically.
- [ ] Two ticks at the same instant and a restart at enqueue commit produce one scheduled run.
- [ ] Cancelling a queued run prevents any model/tool request; approving a resumed request occupies no duplicate worker slot.
- [ ] Health, event replay, and cancellation remain responsive during a slow model/tool request.

### M4 — Replace memory with scoped, correctable facts

**Deliverable:** explicit remember/search/correct/forget with provenance and a measured lexical baseline.

**Consumes:** the same Store and session/workspace identity. **Produces:** `Memory.remember(scope: MemoryScope, text: str, source_run_id: str | None) -> MemoryRecord`; `search(scope, query: str, limit: int = 5) -> list[MemoryRecord]`; `correct(record_id: str, text: str, scope: MemoryScope) -> MemoryRecord`; `forget(record_id: str, scope: MemoryScope) -> None`. MemoryScope has workspace_id and optional session_id; records explicitly mark session versus workspace visibility.

**Files:** src/hyperclaw/memory.py; extend store.py/contracts.py/runtime.py/execution/policy.py; tests/unit/test_memory.py; tests/evaluations/memory_cases.json; tests/evaluations/test_memory.py; port the fresh-store live memory-tool scenario.

Build 40 synthetic cases: 10 exact recall, 10 paraphrases, 10 corrections/expiry, and 10 cross-session/workspace negatives. Report recall@5 for lexical/paraphrase subsets separately. Require 100% exact-match recall, zero forbidden-scope returns, and zero superseded/expired answers in deterministic correction cases. Paraphrase score is measured, not asserted to be solved by FTS. An embedding proposal requires the same fixture, a named installed model, and a documented improvement.

**Gate:**

- [ ] Other-session records cannot crowd permitted records out of the top five.
- [ ] Correct/forget survives restart and updates retrieval atomically.
- [ ] Retrieved facts carry their source/version and do not grant tool authority.
- [ ] The model receives an opaque stored marker through an actual memory tool after a fresh store opens.
- [ ] No keyword-driven automatic facts or self-rated learning activation remain.

### M5 — Reuse one MCP integration and reviewed skills

**Deliverable:** one admitted read-only documentation MCP server and a narrow SKILL.md loader, both recorded in run provenance.

**Consumes:** Executor, policy grants, owned container lifecycle, and memory/provenance fields. **Produces:** `McpTools.list_tools() -> list[ToolDefinition]`, `McpTools.invoke(call: ToolCall) -> ToolReceipt`; `Skills.load(name: str) -> SkillDocument`, containing name, description, body, content_hash, and admitted resource references.

**Files:** src/hyperclaw/mcp.py; src/hyperclaw/skills.py; pyproject.toml optional mcp extra; uv.lock; examples/mcp-docs/; tests/integration/test_mcp.py; tests/unit/test_skills.py.

The first useful peer serves this repository's public documentation mounted read-only. Use a version-pinned maintained SDK and fixture servers for the two protocol revisions. Namespace tool names by server identity; apply operator-controlled schemas/capabilities, response size limits, and timeout/reconciliation behavior. A schema change invalidates old grants. Document unsupported optional features instead of claiming full protocol conformance.

**Gate:**

- [ ] Both selected protocol revisions are exercised; disconnected peers and oversized results produce bounded failures.
- [ ] The admitted server reads documentation but cannot reach runtime credentials or mutate the workspace.
- [ ] Changing a skill or tool schema changes provenance and requires renewed admission where authority changes.
- [ ] Instruction text cannot enable a blocked tool; unsupported hooks/path escapes are rejected.
- [ ] One real documentation question is answered with a retrieved source through the admitted server.

### M6 — Make the focused assistant comfortable to use

**Deliverable:** a small web page for chat/run inspection/approvals and one opt-in Telegram polling adapter.

**Consumes:** the existing API and Runtime; no additional orchestrator. **Produces:** static web assets and `TelegramAdapter.handle(update: TelegramUpdate) -> None`, where the adapter normalizes platform input before Runtime.submit.

**Files:** src/hyperclaw/web/{index.html,app.js,style.css}; src/hyperclaw/telegram.py; extend api.py/config.py; tests/integration/test_web_api.py; tests/integration/test_telegram.py; README.md; SECURITY.md.

Web authentication uses the bearer header, with no token in a URL or persistent browser storage. Event streaming uses authenticated fetch. Telegram requires explicit bot credentials and allowed chat/sender pairs, checked before attachment downloads. Map (bot identity, chat, topic, sender) to one session ID in Store. Deduplicate by update ID; persist delivery status. A Telegram send with an unknown outcome is visible and not blindly resent. Omit webhooks and additional channels.

**Gate:**

- [ ] Web reconnect replays run state without resubmitting work; approvals bind exact invocations.
- [ ] Forged/missing bearer tokens cannot read transcripts, approve, reset, or submit.
- [ ] Telegram authorization, duplicate updates, group-sender isolation, and restart mapping pass with fixtures.
- [ ] No stale launchers, dead routes, broad feature claims, or obsolete dependency instructions remain.
- [ ] A disposable-root walkthrough covers chat, verified file task, interruption recovery, correction recall, MCP lookup, and a reviewed skill.

## Completion and operating limits

The first review checkpoint is the working M1 vertical slice, not an empty framework. M2's container cancellation/reconciliation gate is the highest implementation uncertainty; resolve it before unattended execution. M4's paraphrase evaluation decides whether semantic retrieval deserves more work.

Do not add multi-agent delegation, browser automation, speech, trading, cloud model routing, remote multi-user hosting, embeddings, or automatic skill learning merely to match the old catalog. Each needs a concrete workflow and its own acceptance evidence.

Run narrow tests during development, the full deterministic battery at each milestone boundary, live Qwen when the transport/tool behavior changes, and Docker tests when process policy changes. Preserve reports and explicit skips. Do not run all external-service tests after documentation-only edits.

At completion, one package owns all advertised behavior and every advertised capability has a working public-path test. The user can choose to switch their running installation after seeing the disposable-root demonstration.
