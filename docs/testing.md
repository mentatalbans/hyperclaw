# Runtime v2 testing

Runtime v2 replaces the legacy test suite; raw count parity is not a goal. Historical source and tests remain at dcad202.

| Retained behavior | Destination | Milestone |
| --- | --- | --- |
| Configuration precedence, explicit local model, root preservation | tests/unit/test_config.py | M1 |
| Session ordering, restart/reset isolation, deduplication, terminal transactions | tests/unit/test_store.py, tests/integration/test_runtime.py | M1 |
| Text/thinking separation, usage, malformed streams and early EOF | tests/unit/test_ollama.py | M1 |
| Loopback provider, real subprocess lifecycle, crash/replay, auth | tests/support, tests/integration/test_http.py | M1 |
| Live text/usage, incremental stream/usage, conversation restart/isolation/reset | tests/live/test_ollama.py | M1 |
| Environment isolation, explicit live selection, reports and subprocess coverage | scripts/test_battery.py, tests/unit/test_battery.py | M1 |
| Images/tool blocks, limits, scoped file tools, exact approvals and verified command cancellation | tests/unit/test_tool_transport.py, tests/unit/test_workspace.py, tests/integration/test_execution.py, tests/integration/test_tools_runtime.py, tests/live/test_docker.py, tests/live/test_execution_docker.py | M2 |
| Foreground/detached tool ownership, SIGKILL/effect recovery, atomic schedule reservations | tests/integration/test_recovery.py, tests/live/test_recovery_docker.py, tests/unit/test_scheduling.py | M3 |
| Authenticated schedule controls, CLI, busy-worker polling and SIGKILL at enqueue COMMIT | tests/integration/test_scheduling.py | M3 |
| Background controls, approval expiry and health during model/tool work | tests/integration/test_background.py, tests/live/test_background_docker.py | M3 |
| Scope before ranking, corrections, receipt/FTS atomicity, fresh-store live memory tool | tests/unit/test_memory.py, tests/evaluations, tests/integration/test_memory.py, tests/live/test_memory.py | M4 |
| Reviewed skill parsing/admission, submit-bound hashes and isolated context snapshots | tests/unit/test_skills.py, tests/integration/test_skills.py | M5 |
| Documentation admission, SDK protocol bounds and owned MCP container recovery | tests/unit/test_mcp.py, tests/integration/test_mcp.py, tests/live/test_mcp.py | M5 |
| Web and Telegram authorization/deduplication | adapter tests | M6, not shipped |

Legacy PostgreSQL, cloud routing, swarm, dashboards, trading, auto-learning and alternate entrypoint tests are retired with their implementations. They are not skipped successes.

Offline tests use disposable roots and loopback providers. Live tests require explicit selection and fail if the requested service/model is unavailable. Tests never download models or switch running services.

Install the full development environment with `uv sync --locked --extra dev --extra mcp`. Run `make test` for offline checks, `make test-coverage` for statement/branch reports and `make test-live` for the six model-only Qwen scenarios. Docker and model tests are excluded by default. Set `OLLAMA_URL`/`OLLAMA_MODEL` as Make variables to choose an already-installed target. The runner modes are `quick`, `docker`, `live`, and `all`; reports persist under ignored `test-results/<mode>-<timestamp>/` as summary JSON, JUnit, logs, and optional coverage.

For Docker gates, prepare the command image below and [build the MCP documentation image explicitly](../examples/mcp-docs/README.md), then pass its inspected immutable ID as `MCP_DOCS_IMAGE=sha256:...`. Run `make test-docker MCP_DOCS_IMAGE=sha256:...` for owned-container tests across the integration and live directories. `make test-live-mcp MCP_DOCS_IMAGE=sha256:...` selects the six prior model scenarios plus the documentation/skill demo, requiring both Docker and Ollama. The corresponding runner command is `python scripts/test_battery.py live --with-docker --mcp-docs-image sha256:...`; it selects only Ollama cases. `make test-all MCP_DOCS_IMAGE=sha256:...` explicitly selects offline, Ollama and Docker checks with coverage. Missing requested services/images fail instead of triggering an automatic build, download or fallback.

The live M1 scenarios now use authenticated HTTP and real disposable daemon processes: text/provenance/usage, stream/provenance/usage, and an opaque conversation label across restart/new session/reset. M2 adds a synthetic red image and a real read/write workflow with an opaque marker and observed file hash. The three retained M1 scenarios explicitly use no tools to measure their single-request text/usage behavior. M4 adds actual memory_search retrieval of a synthetic opaque marker after the seeding Store closes and the daemon opens a fresh Store.

Protocol basis: the transport uses [Ollama's Messages compatibility](https://docs.ollama.com/api/anthropic-compatibility). Reproducible installs use a checked [uv lockfile](https://docs.astral.sh/uv/concepts/projects/sync/).

Actual M1 results, gate mapping, coverage counts and a run/result/replay walkthrough are recorded in [the September 9 acceptance report](reports/2026-09-09-runtime-v2-m1.md). Python 3.11 and 3.13 each passed 88 offline tests; all three explicitly selected live Qwen cases passed. Hosted CI is configured but was not executed in this session.

M2 execution gates use real Workspace and Store with disposable roots. External backend failures are injected below the Executor; artifact effects, approvals and receipts stay real. Docker live cases cover parent/child termination, create/start reconciliation before queued work, read-only workspace enforcement, secret/network sentinels, resource settings and bounded output. Docker tests only remove containers carrying their unique installation labels. The tool image is digest-pinned in `containers/tool-runner/Dockerfile` and the backend; prepare that exact image before selected tests.

Public tests exercise the installed daemon and authenticated HTTP for checkpoint/restart/approval, observed artifact receipts, auth rejection and CLI decisions with exact hashes. Limits cover repeated requests, twelve rounds, attachment budgets, aggregate thinking signatures, content-block retention, output bounds and active execution budgets. The deadline test allows two seconds for real IO/SQLite scheduling while the fixture deliberately withholds completion; no 150 ms startup timing assumption remains.

M2 acceptance commands, platform results, review corrections and limits are recorded in [the M2 report](reports/2026-09-09-runtime-v2-m2.md). Hosted Linux CI includes an explicit Docker gate; a configured workflow alone is not claimed as an executed hosted run. A trusted disposable Linux verification runner, where used, is separate from the runtime's restricted command containers.

M3 recovery tests use test-only launchers to pause at real persistence and effect boundaries, then send SIGKILL and restart through the ordinary production CLI. They inspect durable state and actual files after repeated restarts; completed and uncertain effects must never execute again. Docker cases additionally verify termination of owned parent/child processes and preserve immutable receipts when backend access is lost. The launchers add no production fault-injection settings.

Scheduling unit tests exercise the real Store transaction, including rollback, simultaneous ticks, fixed UTC cadence, downtime coalescing, busy sessions, generation retargeting, uncertainty pauses and migrated request-ID collisions. Public schedule tests exercise authenticated HTTP and real CLI processes, then kill the daemon before and after the enqueue transaction commits and verify one occurrence, run, user message and queued event after restart. Public background tests use actual daemon processes and loopback HTTP providers. They test concurrent approval decisions after restart, queued cancellation without dispatch, approval expiry while another session is busy, and health/acceptance failure when an owner stops. The slow-tool responsiveness check uses a real Docker command.

M3 platform commands, process-kill evidence, schedule semantics, review corrections and limits are recorded in [the M3 report](reports/2026-09-09-runtime-v2-m3.md) and [results JSON](reports/2026-09-09-runtime-v2-m3-results.json). The final source passed 198 offline tests on each of macOS Python 3.11/3.13 and local Linux, 10 selected Docker tests on each platform, and all five live Qwen scenarios. The wheel matches source byte for byte. Hosted CI remains configured but unexecuted.

M4 freezes 40 synthetic lexical cases before retrieval implementation: ten exact, ten paraphrase, ten correction/expiry/forget and ten scope cases. Run `uv run --locked python scripts/evaluate_memory.py --report test-results/m4-evaluation.json` for per-case returned/expected IDs and separate recall@5 metrics. Exact recall must be 1.0; forbidden and obsolete returns must be zero. Paraphrase recall is measured without a minimum assertion. The fixture is not tuned after observing results, and the corpus is not evidence of general retrieval quality or large-corpus performance.

Memory integration tests exercise the public HTTP/CLI tool path and actual Store across restarts and session reset. A test-only launcher sends SIGKILL on each side of the memory/receipt transaction commit, then ordinary daemon startup proves atomic state and no replay. Injected receipt-event failure proves facts and FTS changes roll back too. A retrieved instruction cannot grant workspace-write authority; ordinary text without an admitted memory tool creates no fact.

M4 commands, hashes, per-case results, review corrections and earlier failed attempts are recorded in [the M4 report](reports/2026-09-10-runtime-v2-m4.md) and [results JSON](reports/2026-09-10-runtime-v2-m4-results.json). The verified source passed 239 offline tests on each of macOS Python 3.11/3.13 and local Linux, 10 Docker tests on each platform, six live Qwen cases and three installed-wheel public tests. The fixed evaluation returned 10/10 exact and 5/10 paraphrase recall, with zero forbidden or obsolete records. The final live and Linux Docker checks ran sequentially after failed concurrent attempts; those failures and the known timing-sensitive Docker fixture remain documented. Hosted CI was not executed.

M5 skill tests use actual packages and daemon requests to check the supported front matter, referenced-resource hashes, FIFO/link rejection, byte/count/depth limits, exact admission and restart. A deterministic queue test edits a selected package before its first execution and requires failure before any provider request. Existing checkpoints retain their original instruction snapshot, while admission is rechecked before continued model or tool work. Selected instructions use the provider's separate system field, count against the context budget and stay out of later unselected conversations.

The M5 MCP acceptance matrix is deliberately narrow:

| Surface | Accepted behavior |
| --- | --- |
| SDK | Optional, pinned `mcp==2.0.0`; base installation remains usable without it |
| Protocols | Actual SDK peers negotiating 2026-07-28 and 2025-11-25, with observed discovery/metadata or initialize traffic |
| Transport | Bounded stdio attachment to an owned Docker peer; no runtime host-process fallback |
| Catalog | Fixed `docs` server with `mcp_docs_search` and `mcp_docs_read`, explicitly selected per run |
| Scheduling | Direct/detached MCP runs supported; schedule creation rejects MCP tools |
| Authority | Exact operator admission of document, catalog, image, protocol and full container-policy fingerprint |
| Source exposure | Admitted Markdown/text snapshot mounted read-only at `/docs` |
| Results | Supported text/structured JSON, complete serialized output at most 65,536 bytes |
| Framing | At most 262,144 bytes per wire frame, enforced before SDK parsing |
| Retries and cache | Disabled, including state-only `InputRequiredResult` retries |
| Other features | Sampling, roots, elicitation, tasks, subscriptions, remote auth and unsupported result types are not enabled |

Docker gates must inspect actual mounts and process restrictions, exercise malformed/oversized/disconnected peers, and kill the daemon at create, start and receipt-commit boundaries. Ordinary restart twice must clean owned peers without replaying completed calls, including when current MCP configuration or the optional SDK is absent. The combined live gate asks Qwen a repository documentation question with a source citation and an opaque marker available only through the admitted documentation copy. Its evidence includes the actual tool receipt, document hash and final answer.
