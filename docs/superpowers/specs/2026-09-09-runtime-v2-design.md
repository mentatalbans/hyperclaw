# HyperClaw runtime v2 design

Status: M1–M3 implemented, reviewed and verified; M4–M6 remain planned. Design date: 2026-09-09. [M1 acceptance](../../reports/2026-09-09-runtime-v2-m1.md), [M2 acceptance](../../reports/2026-09-09-runtime-v2-m2.md), [M3 acceptance](../../reports/2026-09-09-runtime-v2-m3.md).

## Decision

Build a fresh, small Python assistant runtime in this branch. Transplant the proven Ollama protocol behavior and useful test scenarios; replace orchestration, persistence, tool dispatch, and configuration. Do not build a compatibility layer around the existing platform.

The user explicitly permits greenfield replacements and says there are no client compatibility obligations. The target is one operator running a dependable local assistant on Apple Silicon, with Linux used for CI. That target is an assumption inferred from the preceding Ollama work; it is not a commitment to a general multi-user platform.

The [ecosystem comparison](../../research/2026-09-09-claw-comparison.md) motivates execution controls, recovery, reusable integrations, and measured memory. It does not establish that any competing runtime is faster or better on this machine.

| Approach | Advantage | Cost | Decision |
| --- | --- | --- | --- |
| Continue consolidating existing modules | Small initial changes; existing interfaces remain usable | Retains parallel storage, routing, tools, and initialization conventions | Reject for this refactor |
| Fresh Python kernel, selected transplants | Clear ownership; directly uses the demonstrated local model; removes unused scope | Deliberately drops features until their milestone ships | **Choose** |
| Replace HyperClaw with OpenClaw and custom extensions | Gains a broader assistant product | Adopts another runtime and its upgrade/plugin constraints; still needs local workflow validation | Reserve for a future product-direction change |

No Redis, PostgreSQL, distributed workers, agent framework, generic provider registry, or plugin execution framework enters the replacement. A second real provider can justify an interface later. The HTTP model fixture exercises the same Ollama transport used in production.

## Global constraints

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

Commit resolved dependencies in uv.lock and use locked installation in CI. Keep the existing Python floor; changing language or minimum Python version adds no demonstrated value here. uv documents the difference between a checked lockfile and an implicit dependency update. [uv locking](https://docs.astral.sh/uv/concepts/projects/sync/).

## Package and ownership

~~~text
src/hyperclaw/
  __init__.py, __main__.py
  config.py                 explicit TOML settings, root initialization
  contracts.py              immutable requests, events, outcomes
  store.py                  SQLite transactions, schema changes, root ownership
  ollama.py                 Messages transport, stream validation, usage
  runtime.py                run scheduling, model/tool loop, recovery
  api.py                    authenticated HTTP and replayable events
  cli.py                    setup, doctor, daemon launch, HTTP client commands
  execution/
    __init__.py, policy.py   capabilities, grants, approval decisions
    workspace.py            scoped file operations, artifact publication
    docker.py               owned containers, limits, termination, reconciliation
  memory.py                 scoped facts, corrections, FTS retrieval
  scheduling.py             durable one-shot and interval submissions
  mcp.py                    one admitted external tool server
  skills.py                 reviewed instruction packages and their hashes
  telegram.py               optional polling adapter
tests/
  unit/  integration/  live/  support/  evaluations/
~~~

Create files when their milestone needs them. Do not scaffold every directory in milestone 1. Keep transaction details inside Store and process details inside execution; avoid repositories, managers, factories, and registries that only forward one call.

~~~mermaid
flowchart TB
    C[CLI and small web client] --> A[Authenticated HTTP API]
    T[Optional Telegram adapter] --> R[Runtime: submit, observe, cancel]
    A --> R
    S[Durable schedule] --> R
    R --> D[(SQLite: runs, sessions, events, receipts)]
    R --> O[Ollama Messages transport]
    R --> M[Scoped memory and reviewed skills]
    R --> P[Execution policy and approvals]
    P --> F[Scoped workspace operations]
    P --> X[Owned Docker processes]
    X --> E[Admitted MCP server]
~~~

The diagram describes the completed roadmap. Milestone 1 includes only CLI, API, runtime, SQLite, and Ollama.

The daemon owns all work. CLI chat submits over HTTP and observes the resulting run. It does not initialize a second runtime or write the database directly. A second daemon using the same root fails with a clear root-in-use error. OS file locking releases ownership after process death; a PID file is diagnostic, not a lock.

## State, runs, and failure semantics

Use stdlib sqlite3, one connection created and used in a dedicated single-thread executor, short explicit transactions, foreign keys enabled, a 5-second busy timeout, and journal_mode=DELETE with synchronous=FULL. Never hold a transaction across network or model work. Public Store operations are async; connection/thread ownership remains private. Cancellation of a caller must not abandon a transaction already executing; settle it before releasing ownership.

This single-connection design does not need WAL. On this worktree's Python 3.11.13, SQLite is 3.49.1 and FTS5 is available. Python documents connection thread affinity and transaction control. If measured contention eventually justifies WAL, first require a SQLite release containing the documented WAL-reset fix and test the changed concurrency model. [Python sqlite3](https://docs.python.org/3.11/library/sqlite3.html), [SQLite WAL constraints and fix](https://www.sqlite.org/wal.html).

Start with tables for schema_version, sessions, runs, messages, and events. Add invocations, approvals, artifacts, schedules, memory_records, and skill_versions only in their owning milestones. Mutable run/session rows are the authoritative current state. Append-only events explain changes and feed clients; this is not an event-sourced framework that reconstructs the database on every start.

Every accepted submission atomically records the user message, run, and run.queued event. A required request_id is unique within a session generation. Every submission also supplies the expected generation; stale generations fail before accepting work. The same ID and canonical request payload return the existing run; the same ID with a different payload is a conflict. Server-generated session IDs identify conversations, not users. One bearer token represents this installation's operator.

Only one nonterminal run may occupy a session generation. Different sessions can queue work, but only one run executes at a time initially. Same-session competing submissions return conflict; clients can wait and resubmit deliberately. This keeps context order explicit without a distributed lock/lease design.

Each session has an integer generation. Reset increments it transactionally and hides prior-generation messages from future prompts. Reset fails while that session has a nonterminal run. Old runs remain inspectable. A new session has no implicit access to another session's history. Memory reset is separate.

| State | Meaning | Startup behavior |
| --- | --- | --- |
| queued | Accepted durably, execution not begun | Eligible to start |
| running | The owner is currently executing | Mark interrupted in M1; in M2 reconcile invocation receipts first |
| waiting_approval | A specific invocation needs an operator decision | Preserve; no worker slot held |
| succeeded | A complete assistant response was committed | Read-only terminal result |
| failed | A known error ended execution | Read-only; an explicit new run can retry |
| cancelled | Work has stopped after explicit cancellation | Terminal; prior effects are not undone |
| interrupted | Process stopped before a complete result | Terminal; explicit retry creates a linked new run |
| uncertain | An external effect may have happened without a conclusive receipt | Terminal; require reconciliation before another attempt |

Milestone 1 implements queued, running, succeeded, failed, cancelled, and interrupted. Milestone 2 adds waiting_approval and uncertain. Do not change interrupted/uncertain records back to running. Retrying creates a new run with retry_of for traceability.

A completed model answer establishes response completion. A separate verification field is not_requested, passed, or failed, with evidence references. A writing task whose file check fails ends failed even if the model claims success. General answers have verification=not_requested. Never claim generic proof of task completion from fluent text or a self-reported score.

Run events have a stable integer seq per run, type, UTC timestamp, and JSON payload. Examples: run.queued, run.started, model.text, model.thinking, model.usage, tool.requested, approval.required, tool.finished, run.finished. Persist before publishing. Commit the assistant message, terminal state, and terminal event together. Terminal payload contains status and verification; there is no success-shaped sentinel on failure.

Batch text deltas at 50 ms or 4 KiB, whichever comes first; preserve order and flush before the terminal transaction. Only committed chunks are observable. GET events after a cursor always queries durable rows before waiting for a notification; notifications are hints, so a reconnect or missed notification cannot lose an event. Reject malformed or future cursors. Do not retain DB transactions while a client waits. Slow clients page from storage rather than accumulating an unbounded queue.

Record incomplete text in run events. Only complete, successful assistant turns enter subsequent model history; interrupted user turns and partial assistant output remain inspectable but are excluded from later prompts. Context reserves space for the current request, then selects complete turn groups from the current generation newest-first within a total 64 KiB serialized UTF-8 message budget, returned in chronological order. This is a deterministic input-size cap, not a claim about the model's token window; a provider context-limit rejection produces a clear failure. Never orphan a tool result from its call. Reject a current request that exceeds the input budget; record context selection in the run. M2 adds an explicit larger-budget option for attachments.

On graceful shutdown: stop acceptance, settle transactions, cancel active model IO, stop owned tool processes, record terminal state, and release the root lock last. Queued runs remain queued. On abrupt death, startup reconciliation completes before any queued work starts.

## Ollama and configuration

Retain the existing /v1/messages request/response behavior: text, separate thinking, base64 image blocks, structured tool calls/results, and usage. Ollama's current documentation lists these features but also lists limitations, including unsupported PDF blocks and unenforced thinking budgets. Per-model capability checks still need actual model tests. [Ollama Messages compatibility](https://docs.ollama.com/api/anthropic-compatibility).

Use httpx with trust_env=False, redirects disabled, explicit timeouts, and a single explicitly configured endpoint/model. Validate HTTP(S) URLs and reject embedded credentials. The shipped endpoint is loopback; configuring a remote endpoint is an explicit operator choice. Do not infer strict offline operation merely from an Ollama URL: that server can itself provide cloud-backed models. Doctor reports the selected endpoint/model and observed capabilities.

Initialize a v2 format marker only in a fresh root; refuse nonempty unmarked roots and recognized v1 roots, including when selected through the environment. This prevents accidental use of existing personal data. The only configuration file is root/config.toml; precedence is explicit CLI values over that file over shipped defaults. Root selection is --root, then HYPERCLAW_ROOT, then ~/.hyperclaw-v2. No dotenv loading, import-time mutation, or legacy provider environment probing. Default thinking is off, output limit 4096 tokens, model request deadline 120 seconds, total run deadline 600 seconds. M2 keeps the tested 12-round and repeated-call limits, with each tool deadline capped by the remaining run deadline.

M1 supports text and thinking streams. M2 adds complete tool-call block parsing and image attachments before claiming the previous live feature set. Unexpected tool calls while tools are disabled fail explicitly. A stream ending without message_stop is failed, not successful partial output. Model errors never reroute to another provider; operator retry is explicit.

## Execution and verifiable effects

Implement an execution policy in the actual call path. A tool definition declares its argument schema, capability, workspace scope, effect class, output limit, and deadline. The model receives only tools permitted for the current run. Recheck policy immediately before dispatch; schema or argument changes invalidate a pending approval.

Default tools are scoped read/list/search plus explicit memory tools once available. Workspace writes can be granted once for the chosen workspace; do not ask for every edit inside an already granted scope. Command execution requires a separate workspace execution grant. Requests without an existing grant create a durable approval for that exact invocation. The model cannot approve itself or change a grant. Do not reuse the legacy audit classes merely because they exist.

Host file tools use descriptor-relative traversal and reject symlinks, absolute paths, parent escapes, and devices. A path.resolve() prefix test alone is insufficient. The default workspace is root/workspace; user projects are never implicitly mounted from the current directory. Publishing a generated artifact uses an explicit selected workspace and records the final content hash.

Arbitrary commands run in owned Docker containers on macOS and Linux. No host-shell fallback. Docker absence disables execution with an actionable error; chat and scoped reads remain usable. Use a versioned image pinned to a resolved digest, an unprivileged user, read-only container root, dropped capabilities, no-new-privileges, limited CPU/memory/PIDs, bounded temporary storage and output, no network by default, and only the granted workspace mount. Write grants determine whether that mount is writable. Never mount the Docker socket, application database, operator home, or ambient credentials inside a tool container. These Docker controls are a chosen deployment design, not a claim of absolute isolation. [Docker execution controls](https://docs.docker.com/engine/containers/run/).

Before execution or approval, checkpoint the complete assistant tool-call blocks, pending call IDs, selected context, round count, and consumed execution time. Resume an approved invocation from that checkpoint without asking the model to regenerate its arguments. Approval waits release the worker slot and expire after 24 hours; M2 measures the 600-second limit as active execution time, excluding queued/approval wait. Store elapsed time so a restart cannot reset the budget.

Before execution, record invocation ID, canonical arguments/hash, effective policy hash, and intent. Create a labeled container, persist its ID, then start it. Labels contain installation and invocation identity. On cancellation, stop/kill and inspect that container; closing a docker CLI process does not establish that the container stopped. Startup finds containers by installation label and reconciles create/start/finish gaps. If the backend cannot establish termination/outcome, record uncertain and stop dependent work.

A unique invocation receipt prevents replay of a known completed call. It cannot make external effects exactly-once. Never retry a shell command, file mutation, or MCP mutation just because the response was lost. Read retries also require a terminated prior execution and an explicit safe classification. Verification uses observed outputs: expected file/hash, command exit status plus requested checks, or a remote receipt when that integration exists.

## Memory, integration, and learning

Replace the current mixed file/database memory manager with SQLite records scoped to session or workspace. Default writes are session-scoped; broader facts require an explicit workspace scope. Apply visibility and active-version filters before ranking and LIMIT. FTS5/BM25 is the first measured retrieval baseline; it is lexical, not semantic. Correcting a fact atomically supersedes the old record and updates the index. Forget removes it from retrieval; it does not silently promise to erase conversation/event archives. [SQLite FTS5](https://www.sqlite.org/fts5.html).

Each record includes provenance, observed_at, optional valid_until, scope, and supersedes. Begin with explicit remember/correct/forget; remove keyword-triggered automatic memory extraction. Add embeddings only if a fixed paraphrase evaluation demonstrates a useful improvement and an explicit local embedding model has been selected. No automatic embedding-model download.

Capture config, model, tool-schema, skill-content, and relevant workspace-revision hashes on runs. These observations let later procedures distinguish a changed environment from the one in which a workaround succeeded. They are evidence about freshness, not a complete filesystem snapshot.

Add one MCP client through the execution policy, starting with a read-only documentation server over container-owned stdio. Use the maintained Python SDK; pin a reviewed version and test published 2026-07-28 plus 2025-11-25 peers. The prior research identifies the July protocol changes and v2 SDK; recheck its supported API when implementing. Do not hand-roll the protocol or assume server annotations authorize execution. Arbitrary host stdio, remote OAuth servers, MCP Tasks, ACP, and A2A are outside the initial integration milestone. [Protocol and SDK evidence](../../research/2026-09-09-claw-interoperability-evidence.md).

Support a deliberately narrow SKILL.md subset: name, description, instruction body, and bounded referenced text resources beneath the admitted skill directory. Reject path escapes and unsupported executable hooks. Loading a skill is explicit and records its content hash. Proposed procedures remain drafts until the operator accepts them; model-reported safety or quality scores never auto-activate them.

## Delivery, cuts, and acceptance

The [roadmap](../plans/2026-09-09-runtime-v2-roadmap.md) is the complete scope map. The [M1 plan](../plans/2026-09-09-runtime-v2-m1.md) is the executable first slice.

Do not keep a runnable legacy tree inside the replacement package. The original checkout and commit dcad202 preserve the old implementation. M1 replaces the packaging and runtime source in the new branch; retained behavior is rebuilt behind the new contracts. M1 is useful text chat, M2 adds controlled tools, M3 proves background recovery and scheduling, M4 improves memory, M5 adds portable instructions/MCP, and M6 supplies a small web surface plus optional Telegram.

No legacy data importer is in scope. The new default root prevents accidental schema conversion. If specific historical facts become useful, plan an explicitly selected import separately. Future v2 schema changes do require transactional versioning, SQLite backup before destructive changes, rejection of newer schemas, and tested restoration; that protects newly created state without maintaining v1.

Acceptance requires runnable milestone demos, public-interface regression tests, actual process restart tests, and failure injection at effect boundaries. Live Qwen checks are run when model behavior changes. Docker ownership tests run before enabling execution. Coverage is diagnostic; deleting unsupported modules should not be disguised as an improvement in tested behavior.

Release adoption happens only after M2/M3 recovery gates and the M4 memory baseline are demonstrated in a disposable root. Installation or switching the user's running service is a separate concrete operational action, outside this planning task.
