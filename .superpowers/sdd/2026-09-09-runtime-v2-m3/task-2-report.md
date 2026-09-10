# M3 Task 2 report: atomic scheduling core

Date: 2026-09-09

Branch/worktree: `refactor/runtime-v2` in `.worktrees/runtime-refactor`

Baseline: `77d07df2100537a380c15449063aef77dcf09faa`

## Result

Task 2 adds validated one-shot and fixed-interval schedule contracts, the additive Store v3 schema, atomic occurrence reservation plus normal run submission, immutable creation replay, explicit pause/retarget controls, and the small `Scheduler.tick()` boundary. It does not add daemon polling or runtime, HTTP, or CLI controls.

The shared private `Store._submit()` now owns the existing acceptance SQL. Ordinary submissions reject the reserved `schedule:` request namespace, while scheduled submissions use a bounded SHA-256-derived request ID and attach the schedule ID and fixed-precision nominal due instant to `run.queued`.

## Behavioral coverage

- One-shot create/replay/conflict/list/get behavior, including replay after pause and completion and comparison of every immutable creation field.
- Aware timestamp normalization to UTC, exact due boundaries, fixed microsecond storage, safe offset overflow rejection near both year bounds, and supported years 1970–9998.
- Strict interval bounds of 1–31,536,000 seconds and RunRequest-equivalent input/tool validation.
- Two simultaneous ticks producing one occurrence, run, user message, and queued event.
- Transaction rollback after an injected event-insert failure, leaving the occurrence, run, message, event, and due schedule state unchanged.
- Additive v2-to-v3 migration, no backup for the additive step, foreign-key integrity, and schedule/run recovery after reopen.
- Integer timedelta coalescing after one year of downtime and advancement from nominal cadence to the first instant strictly after the tick.
- Busy queued, running, and waiting-approval sessions leaving the earliest pending due instant and reservation untouched.
- Cancelled occurrences remaining consumed, session reset pausing schedules immediately, explicit generation retarget, manual pause without queued-run cancellation, completed retarget rejection, and immediate durable `uncertain_effect` pause that retarget cannot clear.
- Maximum-length schedule IDs producing internal RunRequest IDs within the 256-character bound.

## TDD evidence and commands

Relevant baseline before adding scheduling tests:

```text
uv run --locked pytest tests/unit/test_store.py tests/unit/test_execution_store.py -q
16 passed in 1.24s
```

Initial required red run before production edits:

```text
uv run --locked pytest tests/unit/test_scheduling.py -q
exit 1: 14 failed
```

Failures were the missing `ScheduleRequest`, Store schedule methods, `hyperclaw.scheduling`, and ordinary-submit namespace protection. After the minimal contracts/schema/Store/Scheduler implementation, the first green attempt reached all behaviors and exposed one test-only representation mismatch:

```text
uv run --locked pytest tests/unit/test_scheduling.py -q
13 passed, 1 failed in 0.41s
```

The database contained the expected single message; the assertion compared `sqlite3.Row` directly with a tuple. Converting the observed row to a tuple fixed the test without changing production behavior.

Self-review then identified a real retarget window: an uncertain scheduled run did not pause until the next tick. A new assertion was added before the tick and observed red:

```text
uv run --locked pytest tests/unit/test_scheduling.py::test_uncertain_occurrence_pauses_future_work_and_cannot_be_retargeted -q
1 failed in 0.12s: expected paused, observed active
```

`Store._finish()` now pauses an interval schedule as `uncertain_effect` in the same transaction that records the uncertain run. Targeted green:

```text
uv run --locked pytest tests/unit/test_scheduling.py -q
14 passed in 0.31s
```

Final Store/scheduling regression after strengthening immutable replay assertions:

```text
uv run --locked pytest tests/unit/test_scheduling.py tests/unit/test_store.py tests/unit/test_execution_store.py -q
30 passed in 0.49s
```

Broader unit and affected execution/recovery/runtime regressions:

```text
uv run --locked pytest tests/unit -q
129 passed in 7.73s

uv run --locked pytest tests/integration/test_execution.py tests/integration/test_recovery.py tests/integration/test_runtime.py -q
30 passed in 12.07s
```

Fresh full offline run on the final source and tests:

```text
uv run --locked pytest -q -k 'not approval_expires_while_another_session_occupies_worker and not failed_background_owner_makes_health_and_acceptance_unavailable'
188 passed, 18 deselected in 34.87s
```

The two explicit exclusions are controller-owned Task 3 RED acceptance cases for maintenance-driven approval expiry and failed background-owner health/acceptance. Docker and Ollama tests remain opt-in and were also deselected by the repository test configuration.

Additional checks:

```text
uv run --locked python -m compileall -q src/hyperclaw tests/unit/test_scheduling.py
exit 0

git diff --check
exit 0
```

## Self-review

- Schedule creation validates through `RunRequest` behavior and revalidates constructed values at the Store boundary.
- Equivalent offset timestamps compare as the same immutable UTC creation instant. SQLite ordering uses six-digit microseconds and `+00:00` consistently.
- The v3 migration only adds tables and an index. Existing v1-to-v2 backup and rollback behavior remains intact, and tests now identify v3 as the current version.
- Reservation, run, message, event, and next-due/status changes share one `BEGIN IMMEDIATE` transaction on the Store-owned connection. No model, tool, filesystem, process, or network work occurs inside it.
- Busy sessions are handled as a non-mutating tick outcome. Other submission conflicts abort the transaction rather than consuming a nominal occurrence.
- Downtime arithmetic uses integer timedelta division. Advancement happens only after enqueue succeeds, derives from the pending nominal instant, and completes safely if the next occurrence exceeds year 9998.
- Session reset and uncertain run completion pause schedules in their existing Store transactions. Retarget uses compare-and-set generation checks, requires the selected current session generation, and cannot clear uncertainty.
- Scheduled request IDs hash the schedule ID and nominal instant, so a 256-character schedule ID still produces a 73-character internal request ID.
- No runtime, API, CLI, task polling, dependency, service, personal root, or other worktree changes were made.

## Concerns and follow-up

- The controller-owned Task 3 background tests and plan edits remain intentionally unstaged. They require the later runtime maintenance/API implementation.
- Independent Task 2 review is intentionally left to the controller after this commit, per the handoff.
