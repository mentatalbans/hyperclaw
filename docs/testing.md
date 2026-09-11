# Runtime v2 testing

Runtime v2 replaces the legacy test suite; raw count parity is not a goal. Historical source and tests remain at dcad202.

The six-milestone roadmap ends at M6. The [post-M6 review](reports/2026-09-10-runtime-v2-post-m6-review.md) and [testing plan](superpowers/plans/2026-09-10-runtime-v2-post-m6-testing.md) define the next validation pass: repeatable installed-package acceptance, composed recovery, populated-state restoration, operator workflows, bounded load and separate answer-quality measurement. Tasks 1–6 now have implemented tooling and measured results in the [September 11 execution report](reports/2026-09-11-runtime-v2-post-m6-testing.md) and [companion JSON](reports/2026-09-11-runtime-v2-post-m6-testing.json). Identity-changing restore usability and answer/citation quality remain failed gates; human VoiceOver/Safari/prose checks and real Telegram remain blocked. The M6 results below are historical baselines.

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
| Web shell, API authorization/pagination and browser recovery | tests/integration/test_web_api.py, tests/browser/test_web.py | M6 web client |
| Telegram credential/authorization boundaries, durable intake/delivery and SIGKILL recovery | tests/unit/test_telegram.py, tests/integration/test_telegram.py, tests/support/telegram_peer.py, tests/support/telegram_daemon.py | M6 |

Legacy PostgreSQL, cloud routing, swarm, dashboards, trading, auto-learning and alternate entrypoint tests are retired with their implementations. They are not skipped successes.

Offline tests use disposable roots and loopback providers. Live tests require explicit selection and fail if the requested service/model is unavailable. Tests never download models or switch running services.

Install the full development environment with `uv sync --locked --extra dev --extra mcp`. Run `make test` for offline checks, `make test-coverage` for statement/branch reports and `make test-live` for the model-only Qwen scenarios. Docker, model and browser tests are excluded by default. Set `OLLAMA_URL`/`OLLAMA_MODEL` as Make variables to choose an already-installed target. The runner modes are `quick`, `browser`, `docker`, `live`, and `all`; reports persist under ignored `test-results/<mode>-<timestamp>/` as summary JSON, JUnit, logs, screenshots where applicable, and optional coverage.

Browser checks are opt-in and never download a browser during ordinary installation, startup, collection or the quick battery. Install the Python support with `uv sync --locked --extra dev --extra browser`, explicitly install a browser if needed with `.venv/bin/python -m playwright install chromium`, then run `make test-browser`. Use `BROWSER_CHANNEL=chrome` to select an existing Google Chrome installation. An explicitly selected missing Python package or browser channel fails with the setup command instead of being counted as a skip. The browser tests drive real packaged assets and a real daemon against the loopback fixture provider; they cover in-memory token handling, hostile text, frozen submission retry, stream replay after reload, cancellation, generation history and exact approval hashes.

Verify an installed wheel from fresh environments with `scripts/verify_wheel.py`. The runner requires `--python`, `--variant base|mcp|both`, and `--report-dir`. It builds into a unique scratch directory, exports locked requirements, installs each selected variant into a separate virtual environment, and runs the public checks from an unrelated copied test harness without `src` or `PYTHONPATH`. It compares the distribution version and every packaged Python/HTML/JavaScript/CSS hash with the checkout, checks that the MCP SDK is absent from the base environment and present in the MCP environment, retains commands/JUnit/screenshots in a timestamped report, and removes only its own scratch directory.

The base public path requires no Docker or browser. Add `--browser-channel chrome` (or another explicitly installed channel) to run browser checks. Add `--mcp-docs-image sha256:...` with an MCP variant to run its selected Docker/MCP public check; the runner inspects that exact immutable image and never builds or pulls one. Omitted browser, Docker/MCP, variant, and hosted gates are recorded as `not_run`. Examples:

```sh
.venv/bin/python scripts/verify_wheel.py --python 3.11 --variant base --report-dir test-results/wheel-base
.venv/bin/python scripts/verify_wheel.py --python 3.11 --variant both --browser-channel chrome --mcp-docs-image "$MCP_DOCS_IMAGE" --report-dir test-results/wheel-all
```

The deterministic one-root walkthrough is part of `tests/browser/test_walkthrough.py` and is marked for both browser and Docker. Select it explicitly with `.venv/bin/python -m pytest tests/browser/test_walkthrough.py --run-browser --browser-channel chrome --run-docker --mcp-docs-image "$MCP_DOCS_IMAGE" -q`. It uses the loopback provider; the real Qwen cases remain under the separate Ollama gate.

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

M5 acceptance commands, actual SDK/image/source evidence, the bounded Qwen demonstration and earlier failed attempts are recorded in [the M5 report](reports/2026-09-10-runtime-v2-m5.md) and [results JSON](reports/2026-09-10-runtime-v2-m5-results.json). Offline suites passed 336 tests on macOS Python 3.11/3.13 and local Linux, each with one expected base-only skip. Both Docker platforms passed 22 tests; all seven Qwen cases and 30 installed-wheel public tests passed. The genuine base wheel executed the missing-SDK check without a skip. Final review findings were fixed and cleared by scoped rereview; refreshed acceptance and owned cleanup completed. The live answer retrieved the requested content but misnumbered several citations, an explicit limitation of the model-prose checks.


Telegram offline coverage uses synthetic bot credentials and actual loopback HTTP peers for identity, webhook refusal, polling, file retrieval and message delivery. Run `.venv/bin/python -m pytest tests/unit/test_telegram.py tests/integration/test_telegram.py -q` for its focused suite. Production has no endpoint override or fault gates: a test-only HTTPX transport rewrites the official host to the owned local peer, and a copied test launcher injects process gates at the Store/Runtime boundary. Tests never make real Telegram requests or send real messages.

Two independent trials send actual SIGKILL at each of reserve-before-submit, submit-before-bind, and send-after-request-before-receipt. Public status, polling offsets, SQLite reopen and run/session counts prove durable intake and no blind resend. Additional tests cover schema-6 preservation, cancellation settling, reset races, pair revocation after restart, approval notices and continued intake, rejected-sender text exclusion, raw byte/encoding limits and successful/failing HTTPX INFO log sanitization. Support files resolve relative to the copied test launcher, so the integration suite can run with an installed wheel from an unrelated working directory. This local synthetic evidence does not exercise Telegram service availability or a real bot; platform, wheel, browser, Docker and Qwen acceptance remain separate milestone gates.


## Final M6 acceptance — September 10, 2026

The [M6 report](reports/2026-09-10-runtime-v2-m6.md) and [archived results](reports/2026-09-10-runtime-v2-m6-results.json) record final-source commands, versions, case outcomes, hashes, reviews, failed attempts and cleanup. Python 3.11/3.13 on macOS and Python 3.13 on Linux each passed 427 offline tests with one expected SDK-present/base-only skip. Docker passed 22 cases on each platform; the existing local Qwen gate passed seven; actual Chrome passed 17; fresh base/MCP wheels passed 164 combined public/browser cases with no SDK in base and all 24 packaged Python/web files verified. The unchanged forty-case memory evaluation retains 10/10 exact and 5/10 paraphrase recall, with zero forbidden/stale returns. Hosted CI remains configured but unexecuted.

The accepted one-root walkthrough adds real desktop/mobile browser evidence, exact approval and verified file output, SIGKILL/reopen recovery without repeated effects, corrected-memory recall, admitted Docker MCP plus a reviewed skill, and an actual Qwen web conversation with no console/CSP errors. Telegram's 88 focused cases use synthetic credentials and a local HTTP peer, including actual SIGKILL and cancellation-suppression recovery; no real Telegram user is contacted. The report retains the model citation-number limitation and other M4/M5 follow-ups.

## Post-M6 local acceptance — September 11, 2026

At implementation `f7686e8ce757f6f031318b677cb2aa045385623d`, macOS Python 3.11/3.13 and local Linux Python 3.13 each passed 587 offline tests with one expected SDK-present/base-only skip. Docker passed 22 on each platform, installed Chrome passed 26, and the existing Qwen gate passed seven. Fresh base/MCP wheels passed 183 public/browser executions, including the missing-SDK public case and the corrected one-root walkthrough, with all 24 packaged files matching. Reconciliation found all 644 collected IDs executed and passed across explicitly selected gates (66 new test functions; 170 more collected cases than the planning baseline).

The 900-second and 3,600-second synthetic workloads completed 2,700 and 10,800 runs with zero pending work or duplicate effects. The original forty-case corpus retains 10/10 exact and 5/10 paraphrase case-hit recall@5; the frozen sixty-case corpus scores 15/15 in each category, both with zero forbidden/stale returns. All 36 documentation answers transported, but the frozen quality verdict failed: required-source matches 46/48, expected facts 41/42, and 53/89 parsed range citations satisfy declared path/interval rules. Twenty trials have citation errors; the report distinguishes actual prose defects from lexical-oracle false positives. These are separate measured baselines, not general quality guarantees.

Repeat the implemented local gates with already prepared dependencies:

```sh
.venv/bin/python scripts/test_battery.py quick --coverage --report-dir test-results/post-m6-offline
.venv/bin/python scripts/test_battery.py browser --browser-channel chrome --report-dir test-results/post-m6-chrome
.venv/bin/python scripts/test_battery.py docker --mcp-docs-image "$MCP_DOCS_IMAGE" --report-dir test-results/post-m6-docker
# Explicitly prepared cached Python/dependencies; both wheels and the walkthrough.
UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never .venv/bin/python scripts/verify_wheel.py --python 3.11 --variant both --browser-channel chrome --mcp-docs-image "$MCP_DOCS_IMAGE" --report-dir test-results/post-m6-wheel
make test-live-mcp PYTHON=.venv/bin/python MCP_DOCS_IMAGE="$MCP_DOCS_IMAGE" OLLAMA_URL=http://127.0.0.1:11434 OLLAMA_MODEL=qwen3.8:27b-mlx
```

Run the quick command with a separate prepared Python 3.13 environment for that matrix leg. Use the [trusted Linux recipe](../containers/verification/README.md) for the copied-source Linux leg. Docker and Qwen gates run sequentially. The installed-wheel verifier requires actual, nonempty, consistent JUnit cases and zero selected failures/errors/skips; exit status zero alone is insufficient. The walkthrough finalizer attempts every owned cleanup action independently and preserves cleanup failures.

The report records the failed initial Docker prerequisite, later observed API recovery, and verified owned cleanup. One initially running container was absent at the final inventory, with no established cause; unchanged state of that service is not claimed. Main checkout HEAD/status and other-worktree HEAD/branch matched their baselines. No real root was installed, switched or reconciled, and no hosted run or real Telegram message was authorized.

### Manual operator accessibility and Safari checklist

This checklist is exploratory and does not establish formal accessibility conformance. Use a disposable runtime root, the loopback provider fixture, synthetic messages, and synthetic workspace files. Never capture the operator token in screenshots, browser storage, URLs, notes, or recordings.

Prerequisites: a target Mac with keyboard navigation enabled, macOS VoiceOver available, and Safari installed. The observed installed Safari version for the September 11 post-M6 pass is **26.6.2 (21624.5.1.11.3)**. The manual VoiceOver and Safari trials are **blocked / not run** until a human operator can perform them; headless Chromium automation and `--browser-channel chrome` do not substitute for VoiceOver or Safari/WebKit acceptance.

For the VoiceOver pass:

1. Start at the disconnected page and connect using only Tab, Shift-Tab, typing, Space, and Enter. Confirm focus is visible, VoiceOver announces the connection result, and focus can move onward without cycling inside the connection panel.
2. Create a session, open “Run tools and reviewed skills,” change one tool and one admitted-skill selection, and return to the composer. Confirm every control has a meaningful spoken name and moving through selections does not submit a run.
3. Type a multiline synthetic prompt. Confirm Return adds a line without submitting, then reach Send by keyboard and submit it. While output streams, move among the composer, Cancel run, session list, run history, approvals, activity, and receipts; confirm streaming does not steal focus or make those controls unusable.
4. For a synthetic write approval, review the complete arguments, workspace ID, arguments SHA-256, and policy SHA-256 with VoiceOver. Approve it using the keyboard and confirm the exact reviewed action completes. Repeat with a distinct action and deny it; confirm no file effect occurs.
5. Start a gated streaming run, wait until its visible/spoken prefix arrives, cancel by keyboard, and confirm the cancelled terminal state and retained prefix are discoverable. Navigate to an older run and confirm its status and transcript are announced. Disconnect by keyboard and confirm the disconnected state is announced.
6. Trigger a synthetic authentication error and a synthetic run failure. Confirm each error and terminal state is announced without forcing focus away from the control being used.

For the installed Safari pass, repeat the same core workflow without VoiceOver first and then with VoiceOver if available. Record the exact Safari version from Safari > About Safari, macOS version, runtime commit, viewport/display scaling, each completed operation, console errors, and any focus, announcement, reflow, or streaming issue. Treat the result as exploratory until every operation above passes.

Automated Chrome checks cover 320, 390, and 1440 CSS-pixel widths plus a 1440-pixel display at an effective 200% layout width. A separate native page-zoom check calibrates fresh disposable 100% and 200% Chrome profiles against the real app: the outer window remains 1440 pixels, the inner layout width changes from 1440 to 720 CSS pixels, device pixel ratio changes from 1 to 2, and CDP reports page zoom changing from 1 to 2 while viewport scale remains 1. Raw CDP viewport captures exercise the connected operator workflow at several scroll positions without the clipping seen in Playwright's full-page capture. The profile preference applies native page zoom at browser startup; browser-toolbar interaction is not run, and this does not prove Safari zoom behavior. The checks use long synthetic hostile text, tool-call IDs, approval hashes, and receipts; require reachable controls and no unintended horizontal overflow; inspect the real accessibility tree for status/alert roles and control names; and fail on page, request, console, or CSP console errors.

### Opt-in scale and sustained measurements

`scripts/measure_runtime.py` runs synthetic loopback provider/Telegram peers and owns
all disposable roots and subprocesses. It does not contact Ollama, real Telegram,
Docker, or download models. It requires the installed package and repository dev
test dependencies. It is not selected by the quick battery; only its bounded
CLI/report/cleanup tests run there.

```sh
.venv/bin/python scripts/measure_runtime.py --duration-seconds 12 --seed 1 --report-dir test-results/load-tooling --phase sustained
.venv/bin/python scripts/measure_runtime.py --duration-seconds 1 --seed 1 --report-dir test-results/load-recovery-1 --phase recovery
.venv/bin/python scripts/measure_runtime.py --duration-seconds 1 --seed 2 --report-dir test-results/load-recovery-2 --phase recovery
.venv/bin/python scripts/measure_runtime.py --duration-seconds 1 --seed 3 --report-dir test-results/load-recovery-3 --phase recovery
.venv/bin/python scripts/measure_runtime.py --duration-seconds 1 --seed 1 --report-dir test-results/load-scale --phase schedules --phase populated
.venv/bin/python scripts/measure_runtime.py --duration-seconds 900 --seed 1 --report-dir test-results/load-smoke --phase sustained
# Run the hour only after the smoke's invariants pass.
.venv/bin/python scripts/measure_runtime.py --duration-seconds 3600 --seed 2 --report-dir test-results/load-hour --phase sustained
```

All three required options are explicit. Repeated `--phase` selects independent
probes; omitted phases are `not_run`. Without `--phase`, all four run once in the
listed order. Duration controls only sustained intake; setup, drain, reporting and
cleanup are additional. Recovery uses a reproducibly shuffled ten-case deck for
the supplied seed: the eight Task 2 crash boundary/active-state combinations,
one graceful mixed-IO shutdown/reopen, and one shared-worker release/restart.
Each case has its own root. Run seeds 1, 2 and 3 to obtain thirty cycles.

The eight scheduler experiments use 1, 10, 100 and 1,000 active recurring schedules
(interval one second), mapped by index modulo `min(size, 10)` sessions. Session
zero holds the single model worker. In the due regime every schedule starts due;
in the mostly future regime index zero is due and every other schedule is one day
in the future; at size one, its sole schedule is one day in the future. There are at
least two warmup ticks and exactly twenty reported subsequent real daemon ticks.
A separately seeded approval expires during observation; health and busy-session
reset controls are polled. Queue growth reflects this declared shared-session
admission pattern, not a thousand simultaneously independent sessions. Schedules
are paused before the held stream is released and accepted work drains. Test-only
Store wrappers record actual transaction duration, tick call duration, active/due
counts, and excess delay beyond the normal 100ms interval after the previous tick.
Queue snapshots and instrumentation IO add overhead; these are observational
measurements, with no production batching/indexing changes.

The populated phase owns a separate real Store worker in the harness. It seeds
100 sessions, 10,000 genuinely transitioned completed runs and 10,000 physical
memory records, then walks session/run pages of seventeen IDs. A thousand synthetic
query groups check private/shared visibility and exclude other sessions, other
workspaces, expired, forgotten and superseded facts. Corrections have their own
positive lookup. These facts never modify the sealed forty-case quality corpus.
Seeding and query timing are separate. This phase measures Store APIs directly;
scheduler and sustained phases measure a separately owned daemon through HTTP.

Sustained intake targets approximately one batch per second, each containing one HTTP
run, one one-shot schedule and one synthetic Telegram update. The model stream is
held until all three are visibly accepted, a busy-session conflict and health
probe succeed, and then all three complete and delivery settles. There are at
most three outstanding accepted requests, below the required cap of twenty.
Batches drain independently with no catch-up bursts. A sample-boundary wakeup can
shorten the interval between batches: for example, a batch starting at 9.2 seconds
can be followed by one at the 10-second sample boundary. Three fixed sessions reuse
generation zero initially, then reset after each drained ten-second window;
prior generations and durable run/event/message/schedule/Telegram history remain.
This makes the context reconstruction cost and intentional database growth
interpretable. First-text latency observes real SSE; completion latency ends when
the batch's SSE histories have drained. Resource samples are taken initially,
at drained ten-second boundaries, and after final drain. Sampling occurs after a
batch settles, so a slow batch can delay the nominal ten-second boundary.

Every invocation creates a unique `measurement-<UTC>/` directory with `report.json`,
`events.jsonl`, `dirty.diff`, and per-root log/telemetry/hash/count artifacts.
Reports include exact commands, source hashes, environment/dependency/lock hashes,
seed, selected/omitted phases, failure traceback, accepted/completed/conflicted
counts, queue snapshots, nearest-rank p50/p95/max latency and cleanup identities.
Failure stops later phases and keeps evidence; there is no retry loop. The runner
checks source identity again before success. Keep executable source stable during
a measured run, and run external acceptance separately to avoid interference.

Live observers discard provider request bodies after count/size measurement,
retain Telegram method counters and at most one update, clear completed stream
gates, and keep the last 1,000 daemon log lines. Compact events and transaction/tick
measurements stream to disk for the finite selected workload. Exact percentile
sorting happens after daemon shutdown, outside live resource samples. Daemon RSS,
file descriptors and threads are sampled separately from harness resources with
procfs on Linux or `ps`/`lsof` on macOS; no production dependency is added. SQLite
size includes its sidecars. Integrity/foreign-key checks run only after the owner
stops. Synthetic scratch hashes are retained before deleting owned scratch;
credential/config files are excluded from artifacts. Process exit/reader/peer
cleanup and final pending counts are checked. A watchdog intervention fails the
run. Latency/RSS trends establish this measured operating envelope; they do not
invent a production SLA or replace the existing focused responsiveness bounds.

Occurrence lateness is reported separately: the durable `run.queued` timestamp
minus the occurrence's nominal due time measures reservation delay, and
`run.started` minus nominal due adds worker queue delay. Sustained one-shots are
intentionally backdated one second. Scheduler scale occurrences are created in
warmup when free mapped sessions first reserve; post-warmup ticks generally find
those sessions busy. Their occurrence-delay summaries therefore cover all created
occurrences, including warmup, while tick/transaction summaries use the specified
twenty-tick observation interval. A zero-count occurrence summary means no
occurrence was admitted (the single busy-session/future regime), not zero delay.

Sustained `duplicate_effects` covers exact Telegram send/delivery counts, stable
canonical run IDs and one schedule occurrence per batch. The sustained model
answers are text-only: receipts, workspace files and grants must remain empty.
This is not duplicate-write measurement. Real workspace-write/receipt ambiguity
and replay protection are exercised by the separate Task 2 recovery-cycle deck.
If daemon shutdown acceptance itself fails, peer cleanup still runs and the
original failed result remains failed; cleanup never rescues acceptance.

For a diagnosed fixture change, focused selectors avoid repeating unrelated
measurements: `--recovery-case graceful` selects that one fixed-deck mode for the
supplied seed; `--schedule-size 1 --schedule-regime future` selects one scheduler
experiment. These options may be repeated. Reports enumerate omitted cycles and
experiments as `not_run`; a focused pass alone is not the full thirty-cycle or
eight-experiment acceptance. Graceful reopen supplies the pending photo's synthetic
reply and requires its run and delivery to succeed, with exact provider/send
counts, after the original first-signal shutdown assertions have passed.

Copied verification source does not need Git. The runner checks only `.git` at
its own source root and never discovers an ancestor repository. When root metadata
or the Git executable is absent, `source.commit` is null and `source.git` and
`git-provenance.json` explicitly report the reason Git provenance is unavailable;
no `dirty.diff` is fabricated. A filesystem inventory records each included
relative path and SHA-256 plus the aggregate manifest hash. Its declared exclusions
cover Git metadata, virtual environments, caches, build/coverage output, agent
scratch and test-results, matching the verification copy's source scope while
omitting generated state. The current invocation's output directory is also
excluded, so a report inside the copied tree does not invalidate source identity.
Both entry and exit inventories must still match. In a checkout, Git uses explicit
root metadata/worktree paths and discards ambient `GIT_*` overrides.

Scratch creation, journal opening, source/environment metadata and Git diff capture
are inside the report/cleanup guard. A setup failure records its stage and traceback,
leaves workload phases `not_run`, closes an opened journal and removes owned scratch.
Cleanup errors remain failures. Report-directory creation itself happens before any
owned resource is allocated; an unwritable report destination can only return the
error on stderr. These checks require neither Git installation nor dependency
installation, and do not claim a copied archive belongs to any commit.

### Post-M6 retrieval and documentation answer quality

The new Task 6 inputs were frozen before measurement in commit
`4da045503ba4791a13fd41fa1e7426d174cb70f5`. The original forty-case memory file remains
byte-for-byte unchanged. `scripts/evaluate_memory.py` defaults to that file and
accepts an explicit `--cases` file. All four categories expose recall@5 using the
existing case-level definition: a hit requires every declared expected key in the
five returned records; an empty expected set is a hit when none is missing.
Forbidden and stale returns remain separate absolute invariants.

```sh
.venv/bin/python scripts/evaluate_memory.py --report test-results/original-memory.json
.venv/bin/python scripts/evaluate_memory.py --cases tests/evaluations/post_m6_memory_cases.json --report test-results/new-memory.json
.venv/bin/python scripts/evaluate_answers.py --ollama-url http://127.0.0.1:11434 --ollama-model qwen3.8:27b-mlx --mcp-docs-image "$MCP_DOCS_IMAGE" --cases tests/evaluations/documentation_answer_cases.json --trials 3 --report test-results/answers-baseline.json
```

The answer command requires every displayed option. Run it only when the selected
local model and immutable local Docker image are already installed, after sustained
load and other external gates have stopped. It never pulls/builds an image,
downloads a model, selects a fallback, retries a failed trial or chooses a best
answer. The twelve frozen questions produce thirty-six independent roots and
sessions at three trials each. Each root receives the same frozen documentation
and reviewed skill bytes. Source and skill hashes are checked before and after
trials; the report records the installed model digest, inspected image, exact model
request bodies, received response bytes, admission manifests, checkpoint, run ID,
partial text, SQLite evidence, events, receipts and stopped-process cleanup.
Request headers, tokens and credential-bearing configuration are excluded.
The selected `/api/tags` model entry and digest are mandatory. Optional `/api/show`
metadata is retained with its actual HTTP status/body or exception; an unavailable
show route is explicitly reported and does not invalidate an installed selected
model. Each trial separately records requested and response-reported model names,
including mismatches or missing response identity.

The explicit report path and its `.artifacts` sibling must be new. Failed attempts
are retained. A failed model/transport answer remains a measured trial and cannot
be counted as transport success. A prerequisite, setup, evidence or cleanup failure
fails the harness and leaves later planned slots `not_run`. Git-free copied source
uses the same honest filesystem provenance as `measure_runtime.py`: no ancestor Git
lookup, a null commit and an explicit unavailable reason. Metadata/setup failures
remain inside owned scratch cleanup protection.

Scores separate transport completion, source retrieval, lexical fact coverage,
facts appearing in verified retrieved lines, citation path/line intervals,
abstention, declared unsupported-claim patterns, unauthorized tool attempts and
observed actual effects. Source comparisons check both receipt output delivered to
the model and receipt metadata against the frozen file bytes. Missing or malformed
citations are errors. Raw model tool starts capture attempts rejected before
checkpointing; checkpoint calls and durable invocations capture policy rejection
without receipts. Evidence is deduplicated by call ID and model request/round, with
all origins retained. Uncertain invocation outcomes are reported separately from
proven effects. Workspace/grant snapshots and invocation evidence define the effect
observation scope; they do not claim observation outside the owned runtime.

`status: measured` means measurements were retained, not that answers passed.
The report separates its authority verdict, deterministic quality verdict and all
denominators. Regex coverage cannot establish unrestricted prose correctness,
negation or whether a cited clause supports every statement. Human prose inspection
is `blocked_pending_human`; agent-assisted review may supplement it but cannot mark
human acceptance. The previously observed Qwen citation-number limitation remains
open. The new sixty-case and thirty-six-answer quality baselines are not claimed by
the tooling's miniature offline tests; they require separately recorded runs.
