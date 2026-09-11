# Runtime v2 post-M6 testing plan

> **For agentic workers:** Use superpowers:executing-plans to execute this testing plan task-by-task. If test implementation is delegated, use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking. This document requests no execution during its creation.

**Goal:** Establish repeatable evidence for installing, operating and recovering the completed M1–M6 runtime, and measure the limits that milestone acceptance left open.

**Architecture:** Keep the existing single Runtime/Store owner and public HTTP/CLI interfaces. Extend the current pytest fixtures, installed-wheel checks and process-boundary launchers; keep model-quality measurement separate from deterministic correctness.

**Tech Stack:** Existing Python >=3.11 package, SQLite, pytest, HTTPX, Playwright, uv, Docker and the already-installed Qwen model. No new production dependency is required by this plan.

**Spec:** [Runtime v2 design](../specs/2026-09-09-runtime-v2-design.md), [six-milestone roadmap](2026-09-09-runtime-v2-roadmap.md), [post-M6 review](../../reports/2026-09-10-runtime-v2-post-m6-review.md).

**Status:** Executed with failed and blocked gates; see the [September 11 testing report](../../reports/2026-09-11-runtime-v2-post-m6-testing.md) and companion JSON for all results. Tasks 1–6 tooling and automated measurements are complete; identity-changing restore usability and answer/citation quality remain failed, human checks remain blocked, and Task 7 remains blocked. There is no defined M7. Baseline documentation commit: `ef947cf3a8058c6aef680e96cf589b734e1ef43f`; accepted implementation: `79f50c368cfb027105c6a3906d71d5c5c26a25c2`. Work in the existing `refactor/runtime-v2` worktree. Finish Tasks 1–3 before spending time on extended load or model-quality trials.

## Global constraints

The following requirements are copied from the design and apply throughout:

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

Use synthetic content and disposable roots. Test-only failure injection belongs under `tests/support/`; add no production fault flags. Preserve the existing forty-case memory fixture byte for byte. Do not increase a timeout or weaken an assertion simply to turn a failure green. Inspect the cause first.

For a newly discovered product defect, first add a public-behavior regression that fails for the observed reason, make the smallest correction, then rerun affected gates. Save each failed attempt. A timeout rescued by an extra cancellation is a failure, not a pass. Avoid repeated broad reviews and full external matrices after documentation-only changes.

## Evidence and gate rules

Create `test-results/post-m6-<UTC timestamp>/` per execution. Record the source commit and dirty diff, tracked input hashes, Python/SQLite/OS/architecture, dependency lock hash, exact commands, exit codes, durations, selected/deselected/skipped cases, JUnit, and report paths. For external checks record immutable image IDs, browser version and configured model identity. Do not archive bearer tokens, real bot credentials or unrelated user data.

Each result is `passed`, `failed`, `blocked` or `not_run`. A missing explicitly selected dependency is a failed prerequisite; lack of authorization for an external trial is `blocked`. Neither is a skip-shaped success. The known base-only missing-SDK skip is allowed in an SDK-present environment only when the same public check actually runs in the clean base installation.

For each failure record: case ID, trigger, expected and actual behavior, seed if any, source/environment hashes, event/run/receipt references, and an exact reproduction command. Preserve before/after effect hashes. Collect SQLite integrity checks only while the owned daemon is stopped or through its Store worker. Track owned process/container identities and verify cleanup; never stop an unrelated service.

The numbers below are the historical M6 baseline, not a fixed target after new tests are added:

| Gate | Existing command / recipe | Accepted M6 reference |
| --- | --- | --- |
| Offline | `make test-coverage` | macOS Python 3.11/3.13 and Linux 3.13: 427 passed, one expected skip each |
| Browser | `make test-browser BROWSER_CHANNEL=chrome` | 17 passed in installed Chrome |
| Docker | `make test-docker MCP_DOCS_IMAGE="$mcp_docs_image"` | 22 passed on macOS and Linux |
| Model + MCP | `make test-live-mcp MCP_DOCS_IMAGE="$mcp_docs_image"` | Seven passed against existing Qwen |
| Memory regression | `.venv/bin/python scripts/evaluate_memory.py --report test-results/post-m6-memory.json` | 40 cases; exact 10/10, paraphrase 5/10, forbidden/stale zero |
| Linux | [Trusted verification runner](../../../containers/verification/README.md) | Copied source hashes match; actual local arm64 run |
| Installed base/MCP wheel | Task 1's tracked runner | 164 public/browser cases combined; 24 packaged files matched |

Initialize `mcp_docs_image` from `docker image inspect --format '{{.Id}}' hyperclaw-mcp-docs:runtime-v2-m6` only if that retained image still matches the candidate source. Otherwise explicitly build the current image using [its README](../../../examples/mcp-docs/README.md), then inspect its ID. Never silently substitute a tag, build or pull inside a selected test. Prepare the pinned command image using the existing [testing guide](../../testing.md).

Install locked test dependencies together with `uv sync --locked --extra dev --extra mcp --extra browser` when all these gates are selected. Browser installation remains a separate explicit preparation step. `make test-all` does **not** include browser acceptance; run the browser gate separately. Run Docker and Qwen gates sequentially on this machine because retained acceptance records contain interference from concurrent external trials.

### Task 1: Make installed-package acceptance repeatable — high priority

**Finding:** F1, F4 and test selection. **Files:** create `scripts/verify_wheel.py` and `tests/browser/test_walkthrough.py`; extend `tests/unit/test_battery.py`, `.github/workflows/tests.yml` and `docs/testing.md`. Reuse `tests/support/process.py`, existing browser fixtures and the archived M6 helper logic. The local helper sources are in `test-results/m6-preparation/verify_artifacts.py` and `walkthrough.py`; their retained text is also in the committed [M6 results](../../reports/2026-09-10-runtime-v2-m6-results.json). Do not make new tooling depend on ignored paths.

**Deliverable:** A fresh checkout can verify the built package's public behavior and run the deterministic walkthrough without reconstructing controller commands.

- [x] Preserve the wheel helper's tested isolation: build into a unique directory; export locked dependencies; install base and MCP variants into separate fresh virtual environments; run from unrelated directories with no copied `src` or `PYTHONPATH`; verify distribution version, optional SDK presence/absence and every Python/web asset hash. Copy only the test fixtures and examples needed by the selected checks.
- [x] Give `scripts/verify_wheel.py` explicit `--python`, `--variant base|mcp|both`, `--report-dir`, optional `--browser-channel`, and optional `--mcp-docs-image` arguments. Base-only runs need no Docker. Browser and Docker/MCP public gates run only when explicitly selected. Missing selected prerequisites fail. Preserve reports and clean only the scratch directories/processes created by this invocation, including on failure.
- [x] Transfer the deterministic one-root walkthrough into a browser/Docker-marked test: chat, exact write approval, observed file hash, process death after committed receipt, operator edit, two normal restarts and duplicate-request recovery, memory correction/recall, admitted MCP with reviewed skill, logout. Assert one effect per accepted invocation, stable receipts, unchanged grants and zero owned containers. Keep real Qwen separately marked and selected through its existing gate.
- [x] Extend the miniature selection matrix with a browser-marked test. Assert quick/all do not select it implicitly, browser mode does, and unavailable explicitly selected browser support fails. Check omitted gates are listed as `not_run` in package reports.
- [x] Replace CI's help-only wheel check with the tracked base public-path check; run the MCP variant in the job that prepares its image. Keep browser its own job. Record a hosted run ID, source SHA, actual architecture and uploaded reports when remote execution is authorized; until then report hosted verification as `not_run`.

Run these proposed commands **after creating the runner**:

```sh
.venv/bin/python -m pytest tests/unit/test_battery.py -q
.venv/bin/python scripts/verify_wheel.py --python 3.11 --variant both --browser-channel chrome --mcp-docs-image "$mcp_docs_image" --report-dir test-results/post-m6-wheel
.venv/bin/python -m pytest tests/browser/test_walkthrough.py --run-browser --browser-channel chrome --run-docker --mcp-docs-image "$mcp_docs_image" -q --junitxml=test-results/post-m6-walkthrough.xml
```

**Pass:** Both installations import exclusively from site-packages, package bytes/version match, selected public checks pass with no unexplained skips, walkthrough receipts/effects survive recovery, and cleanup is recorded. The base wheel must execute the missing-SDK case. A local pass and a hosted pass are separate results.

### Task 2: Compose channels, approvals and recovery — high priority

**Finding:** F2. **Files:** create `tests/integration/test_runtime_composition.py`; extend `tests/support/telegram_daemon.py` only for test boundaries; reuse provider, Telegram peer and process fixtures. Keep Docker-specific cases in `tests/live/test_recovery_docker.py`.

**Deliverable:** One daemon proves the shared ownership contract while multiple submission sources are active.

- [x] Add `test_http_telegram_and_schedule_share_one_worker`: hold a real fixture model stream open for an HTTP run, accept an authorized Telegram update and reserve a due schedule in other sessions, then release the stream. Assert at most one executing run, durable accepted requests, one schedule occurrence and one Telegram binding. Do not assert an undocumented ordering between simultaneous different-session arrivals.
- [x] Add `test_pending_approval_does_not_block_other_channels`: pause a write for exact approval, complete work in two other sessions, deny or expire the approval, and verify no file effect. Repeat with an exact approved invocation and require one receipt/one observed effect.
- [x] Add `test_mixed_intake_survives_process_death`: use real persistence gates at accepted HTTP submission, schedule reservation and Telegram submit/bind; SIGKILL the owned daemon and reopen twice. Reuse the same canonical request/update identities. Assert accepted work is not duplicated, running work follows interrupted/uncertain semantics, queued work survives and completed/uncertain effects are never replayed. Use two independent trials per boundary.
- [x] Add `test_reset_and_revocation_while_other_channels_are_busy`: stale-generation HTTP work conflicts; reset still refuses a nonterminal session; revoked Telegram pairs cannot download/send; revoked skills/MCP admission blocks further work; scheduled MCP remains rejected. Check provider requests, download/send attempts and grants as well as response codes.
- [x] Exercise graceful shutdown with intake, delivery and a model stream pending, including the existing cancellation-suppression regressions. Require root-lock release only after owned cleanup; fail on watchdog intervention. Reuse existing precise boundary tests rather than replacing them with random timing.

```sh
.venv/bin/python scripts/test_battery.py quick tests/integration/test_runtime_composition.py tests/integration/test_background.py tests/integration/test_scheduling.py tests/integration/test_telegram.py --report-dir test-results/post-m6-composition
```

**Pass:** No duplicated accepted runs/effects/deliveries, no unauthorized IO, no lost committed events, terminal receipts remain immutable, and all owned resources settle after stop/restart. Timing variation alone must not change the assertions.

### Task 3: Restore populated state and exercise storage failure — high priority

**Finding:** F3 and failure diagnosis in F7. **Files:** create `tests/integration/test_storage_recovery.py` and, where subprocess injection is needed, `tests/support/storage_daemon.py`; reuse `tests/unit/test_store.py`, `test_execution_store.py`, `test_scheduling.py`, `test_memory.py` and `test_telegram.py` migration patterns.

**Deliverable:** A disposable populated v2 root can be upgraded/restored and storage failures cannot produce success-shaped or replayed effects.

- [x] Build synthetic fixtures at each supported prior v2 schema (1–6), using the real migration statements and the fields available at that version. Include completed/interrupted runs, event cursors, approvals/receipts/grants, schedules, corrected/shared/private memory and skill/MCP admission when that schema supports them. Keep legacy v1 import explicitly out of scope.
- [x] Start the current daemon against each fixture, then stop and reopen twice. Compare IDs, generations, canonical requests, receipts, memory visibility and schedule occurrences. Assert old requests retain their original tool allowlists and that newer-schema refusal leaves input state unchanged and releases the root lock.
- [ ] Create a populated schema-7 root with Telegram intake/delivery records. Stop its daemon, copy the whole disposable root as the restore snapshot, and record database plus workspace/artifact hashes. Mutate only the working copy, stop it, restore the snapshot at the same disposable path, then use ordinary startup/public APIs to verify usability, grants/admissions, memory correction and no replay. Even a same-path restore can change filesystem identity: require stale grants/admissions to be rejected when their recorded identity no longer matches, and do not silently regrant them. **Execution: failed; see report.**
- [x] Force an actual SQLite allocation failure by limiting `PRAGMA max_page_count` on the owned connection and performing a transaction that needs another page. Verify `SQLITE_FULL` was reached, save the injected boundary, and test both pre-effect admission and receipt persistence after an observed file effect. Distinguish this SQLite limit from an OS-wide disk-full claim. Never fill the host disk.
- [x] Add controlled journal/commit failure and migration-interruption cases in the test launcher. The public result must not claim success without a committed receipt; after an ambiguous external effect, recovery must preserve uncertainty and prevent replay. Check the existing unhealthy/503 contract where the worker or maintenance owner fails.
- [x] On the stopped disposable root run `PRAGMA integrity_check` and `PRAGMA foreign_key_check`; verify correct active memory/FTS results, inspect backups and repeat normal startup. Record whether operator-visible errors identify enough context to diagnose the failure without revealing credentials.

```sh
.venv/bin/python scripts/test_battery.py quick tests/integration/test_storage_recovery.py tests/unit/test_store.py tests/unit/test_execution_store.py tests/unit/test_scheduling.py tests/unit/test_memory.py tests/unit/test_telegram.py --report-dir test-results/post-m6-storage
```

**Pass:** Successful restoration is demonstrated through public operations, integrity returns `ok`, foreign-key violations are empty, backups remain usable, and effects are neither falsely verified nor repeated. A populated-state mismatch or unclassifiable outcome is a blocking finding for unattended use.

### Task 4: Check operator workflows and accessibility — medium priority

**Finding:** F6 and CLI coverage. **Files:** extend `tests/browser/test_web.py`, `tests/integration/test_cli_tools.py` and `tests/integration/test_http.py`; record the manual checklist in `docs/testing.md`. Inspect existing uncovered CLI branches before selecting additional cases.

- [x] Automate keyboard-only connection, new session, composer submission, tool/skill selection, exact approval/denial, cancellation, history navigation and logout. Assert visible focus, reachable controls, meaningful accessible names, no focus trap and no accidental submission while navigating. Inspect status/error announcements using the actual accessibility tree.
- [ ] Run the same core workflow manually with macOS VoiceOver. Check streamed output does not make controls unusable, approval arguments/hashes can be reviewed, and errors/terminal states are discoverable. Record observations; do not claim formal accessibility conformance from this checklist. **Execution: blocked; see report.**
- [x] Inspect 320, 390 and 1440 pixel widths and 200% zoom with long hostile text, IDs and receipts. Confirm essential controls remain reachable, text wraps, token contents are absent from screenshots/storage/URLs, and console/page/CSP errors are absent. Save only synthetic-content screenshots.
- [x] Exercise CLI failure paths selected from coverage: missing daemon, wrong token, missing Docker or SDK, stale generation, stale approval hashes, invalid arguments and an uncertain run. Require an actionable message and nonzero exit status for failed commands, with no new effect or credential disclosure. Reuse existing tests where they already cover the case.
- [ ] Perform a manual core-workflow pass in installed Safari on the target Mac and record the exact version. Treat it as exploratory until it passes; the current `--browser-channel` option launches Chromium and cannot stand in for Safari/WebKit. **Execution: blocked; see report.**

```sh
make test-browser BROWSER_CHANNEL=chrome
.venv/bin/python scripts/test_battery.py quick tests/integration/test_cli_tools.py tests/integration/test_http.py --report-dir test-results/post-m6-operator
```

**Pass:** The primary Chrome keyboard and manual assistive workflow can finish each operation with correct observable state. Record Safari separately and disclose any browser limitation. Report concrete missing CLI behavior rather than targeting a coverage percentage.

### Task 5: Measure scale and sustained operation — medium priority

**Finding:** F2, F7. **Files:** create opt-in `scripts/measure_runtime.py`, reusing Task 2 fixtures; document its bounded workload and report format in `docs/testing.md`. Do not add a background benchmark service or put an hour-long run in the quick battery.

- [x] Implement explicit `--duration-seconds`, `--seed` and `--report-dir` options. Use synthetic loopback providers and Telegram peers, not Qwen or real Telegram. Record accepted/completed/conflicted requests, pending work, duplicate effects, stream latency, health/control latency, schedule tick duration/lateness, RSS, file descriptors, threads, database size and cleanup. Separate intentional durable history growth from resources that fail to settle.
- [x] Run ten bounded graceful/crash-recovery cycles for each seed 1, 2 and 3 using Task 2's fixed boundary schedule. Cap outstanding requests and keep a reproducible event log. Stop on the first invariant violation rather than rerunning until green.
- [x] Measure 1, 10, 100 and 1,000 active schedules, both mostly future-due and currently due, with 20 observed ticks after warm-up at each size. Exercise a busy session, expiry and health polling during the tick. Record transaction latency and queue growth before deciding whether batching/indexing is needed.
- [x] Seed separate measurement roots with 100 sessions, 10,000 completed runs and 10,000 synthetic memory facts. Measure paginated history and visible-scope memory queries; verify pagination has no lost/duplicated IDs and forbidden/obsolete memory never enters results. Keep these scale facts out of the sealed quality corpus.
- [x] Run a 15-minute smoke workload, then one 60-minute workload if invariants hold. Maintain at most 20 outstanding submissions across HTTP, scheduled and synthetic Telegram sources. Sample resource use every 10 seconds; drain between measurement windows and record residual owned resources.

Proposed runner commands, available after this task implements the script:

```sh
.venv/bin/python scripts/measure_runtime.py --duration-seconds 900 --seed 1 --report-dir test-results/post-m6-load-smoke
.venv/bin/python scripts/measure_runtime.py --duration-seconds 3600 --seed 2 --report-dir test-results/post-m6-load-hour
```

**Pass:** Zero invariant violations, stuck shutdowns, unexplained lost work or residual owned processes/containers. Report p50/p95/max latency and resource trends at each workload. Existing focused responsiveness tests retain their bounds; load measurements establish an operating envelope and do not acquire invented production latency promises after the fact. Investigate the inherited eight-second Docker fixture if it fails under this workload; preserve evidence before changing its synchronization.

### Task 6: Measure retrieval and answer correctness separately — medium priority

**Finding:** F5. **Files:** retain `tests/evaluations/memory_cases.json`; create `tests/evaluations/post_m6_memory_cases.json`, `tests/evaluations/documentation_answer_cases.json` and `scripts/evaluate_answers.py`; extend `scripts/evaluate_memory.py` with an explicit alternate-case-file option only if needed.

- [x] Freeze a new 60-case synthetic memory set before measuring: 15 exact, 15 paraphrase, 15 correction/expiry/forget and 15 cross-scope cases. Use declared expected IDs/visibility and a corpus hash. Run the unchanged original forty cases separately. Report recall@5 by category and forbidden/stale return counts; do not tune either set after observing results.
- [x] Freeze 12 documentation questions and source snapshots: four direct facts, four multi-source questions, two questions with no answer in the source, and two documents containing hostile instructions. Define expected facts, valid source paths/line intervals, unsupported claims and prohibited tool effects before requesting model answers.
- [x] Run three independent trials per documentation question against the already-installed configured Qwen model, with the same frozen source/skill hashes. Retain all 36 answers, run IDs, receipts, request/model metadata and tool evidence. No best-of selection, hidden retries, cloud fallback or model download.
- [ ] Score transport completion, retrieved-source match, supported factual claims, citation path/line correctness, appropriate abstention and unauthorized-effect attempts separately. Use deterministic source comparisons plus human inspection for prose. A model grading its own answer is not the sole oracle. Record missing/malformed citations as errors, not missing data. **Execution: blocked; see report.**
- [x] Write a quality report with denominators, per-case outcomes and retained incorrect answers. Keep the existing known citation-number limitation visible. Only propose a retrieval/model/prompt change after these measurements identify a concrete failure; evaluate any proposed improvement on an untouched holdout rather than relabeling the baseline.

```sh
.venv/bin/python scripts/evaluate_memory.py --report test-results/post-m6-original-memory.json
make test-live-mcp MCP_DOCS_IMAGE="$mcp_docs_image"
```

The new answer evaluator must require explicit `--ollama-url`, `--ollama-model`, `--mcp-docs-image`, `--cases`, `--trials` and `--report` inputs; these explicit inputs are implemented by the tracked evaluator.

**Pass:** Runtime authority/scope invariants remain absolute: zero forbidden/stale retrieval and unauthorized effects. Exact recall on the original fixture remains 10/10. Paraphrase recall and model-answer scores are reported as measurements with all errors retained; they do not redefine a successful tool receipt as a factually correct answer. Claims of reliable citations require their own evidence and cannot use the seven-case transport gate as a substitute.

### Task 7: Optional real Telegram acceptance — conditional

**Finding:** Original M6 deliberately uses fixtures. **Files:** add results to the execution report and any reproducible service-independent regression to `tests/integration/test_telegram.py`.

**Prerequisite:** The operator selects a test bot, exact allowed test chat/sender pairs, and authorizes sending test messages. Execution of this testing plan does not authorize real Telegram messages. Leave this task `blocked` until those inputs exist; continue independent local tasks.

- [ ] In one disposable root with the fixed private token file, validate identity/no-webhook startup, authorized text, photo with meaningful and blank captions, supported group/topic mapping, and sanitized CLI/web status. Record bot/chat identifiers only in private evidence when necessary; never token-bearing URLs. **Execution: blocked; see report.**
- [ ] Arrange a controlled unauthorized sender/photo trial and verify no attachment download, run or reply. Confirm a pending write approval is decided only through the local operator interface and that unrelated authorized intake continues. **Execution: blocked; see report.**
- [ ] Restart while updates are pending and verify durable mapping/deduplication against the actual service. Use a controlled test transport disconnection to observe uncertain delivery; do not resend it just to see whether the user received it. Keep the stronger synthetic SIGKILL/ambiguous-send tests as separate evidence. **Execution: blocked; see report.**
- [ ] Confirm revocation prevents later intake/delivery, then stop only the disposable daemon and inspect its owned cleanup. Record actual platform responses and any service behavior that the fixture missed. **Execution: blocked; see report.**

**Pass:** Intended messages reach only the authorized test recipients, service responses match supported normalization, and no ambiguous delivery is blindly resent. Without this task, describe Telegram as fixture-verified and keep it optional; do not claim real-service acceptance.

## Completion and execution order

Tasks 1–3 form the first reviewable batch. Task 4 follows; Task 5 uses Task 2's scenarios; Task 6 is independent of performance optimization. Task 7 is conditional and need not delay the local core report. Do not rename these tasks M7 or add a feature milestone without a separate scope decision.

- [x] Review the changed tests/tooling and any minimal product corrections against the design once; use scoped follow-up review for findings.
- [x] Run affected focused tests during implementation, then one final offline matrix on macOS Python 3.11/3.13 and Linux, plus selected installed-wheel/browser/Docker/Qwen gates against the same final source where changed behavior requires them. Record hosted CI separately. Avoid pooling passes from different source versions.
- [x] Verify all new test IDs collected and ran, compare skips with the declared selection, inspect the visual/manual evidence, and record unresolved findings by impact. Coverage guides inspection; no arbitrary percentage increase is required.
- [x] Publish a local `docs/reports/YYYY-MM-DD-runtime-v2-post-m6-testing.md` and companion JSON with traceability from F1–F7 and every task to its outcome/evidence, exact source/environment, failures, limits and owned cleanup. Mark conditional/unexecuted work plainly. Update `docs/testing.md` with runnable final commands.
- [x] State readiness separately for local core use, unattended operation, answer/citation quality, browser support and real Telegram. A known limitation can remain only when the supported claim is narrowed accordingly; no required integrity or authority failure is waived.

This plan ends with a concrete local testing report. Installation into the operator's real root, service switching, pushing/merging or publication remains a separate operational action.
