# Runtime v2 testing

M1 replaces the legacy test suite; raw count parity is not a goal. Historical source and tests remain at dcad202.

| Retained behavior | Destination | Milestone |
| --- | --- | --- |
| Configuration precedence, explicit local model, root preservation | tests/unit/test_config.py | M1 |
| Session ordering, restart/reset isolation, deduplication, terminal transactions | tests/unit/test_store.py, tests/integration/test_runtime.py | M1 |
| Text/thinking separation, usage, malformed streams and early EOF | tests/unit/test_ollama.py | M1 |
| Loopback provider, real subprocess lifecycle, crash/replay, auth | tests/support, tests/integration/test_http.py | M1 |
| Live text/usage, incremental stream/usage, conversation restart/isolation/reset | tests/live/test_ollama.py | M1 |
| Environment isolation, explicit live selection, reports and subprocess coverage | scripts/test_battery.py, tests/unit/test_battery.py | M1 |
| Image/tool blocks, deadlines, repeated calls, allowlist, controlled writes and cancellation | execution tests | M2, not shipped |
| Detached tool recovery, invocation receipts, durable schedules | recovery/scheduling tests | M3, not shipped |
| Scope before ranking, corrections, fresh-store live memory tool | memory/evaluation tests | M4, not shipped |
| MCP protocol and reviewed skill packages | MCP/skill tests | M5, not shipped |
| Web and Telegram authorization/deduplication | adapter tests | M6, not shipped |

Legacy PostgreSQL, cloud routing, swarm, dashboards, trading, auto-learning and alternate entrypoint tests are retired with their implementations. They are not skipped successes.

Offline tests use disposable roots and loopback providers. Live tests require explicit selection and fail if the requested service/model is unavailable. Tests never download models or switch running services.

Run `make test` for offline checks, `make test-coverage` for statement/branch reports, and `make test-live` for the three M1 Qwen scenarios. `make test-all` runs offline and explicitly selected live checks with coverage. Set `OLLAMA_URL`/`OLLAMA_MODEL` as Make variables to choose an already-installed target. The runner modes are `quick`, `live`, and `all`; reports persist under ignored `test-results/<mode>-<timestamp>/` as summary JSON, JUnit, logs, and optional coverage.

The live M1 scenarios now use authenticated HTTP and real disposable daemon processes: text/provenance/usage, stream/provenance/usage, and an opaque conversation label across restart/new session/reset. Image and fresh-store memory-tool tests remain M2/M4 acceptance work, not skipped M1 checks.

Protocol basis: the transport uses [Ollama's Messages compatibility](https://docs.ollama.com/api/anthropic-compatibility). Reproducible installs use a checked [uv lockfile](https://docs.astral.sh/uv/concepts/projects/sync/).

Actual M1 results, gate mapping, coverage counts and a run/result/replay walkthrough are recorded in [the September 9 acceptance report](reports/2026-09-09-runtime-v2-m1.md). Python 3.11 and 3.13 each passed 88 offline tests; all three explicitly selected live Qwen cases passed. Hosted CI is configured but was not executed in this session.
