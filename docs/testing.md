# Runtime v2 testing

M1 replaces the legacy test suite; raw count parity is not a goal. Historical source and tests remain at dcad202.

| Retained behavior | Destination | Milestone |
| --- | --- | --- |
| Configuration precedence, explicit local model, root preservation | tests/unit/test_config.py | M1 |
| Session ordering, restart/reset isolation, deduplication, terminal transactions | tests/unit/test_store.py, tests/integration/test_runtime.py | M1 |
| Text/thinking separation, usage, malformed streams and early EOF | tests/unit/test_ollama.py | M1 |
| Loopback provider, real subprocess lifecycle, crash/replay, auth | tests/support, tests/integration/test_http.py | M1 |
| Live text/usage, incremental stream/usage, conversation restart/isolation/reset | tests/live/test_ollama.py | M1 (pending task 5) |
| Environment isolation, explicit live selection, reports and subprocess coverage | scripts/test_battery.py, tests/unit/test_battery.py | M1 |
| Image/tool blocks, deadlines, repeated calls, allowlist, controlled writes and cancellation | execution tests | M2, not shipped |
| Detached tool recovery, invocation receipts, durable schedules | recovery/scheduling tests | M3, not shipped |
| Scope before ranking, corrections, fresh-store live memory tool | memory/evaluation tests | M4, not shipped |
| MCP protocol and reviewed skill packages | MCP/skill tests | M5, not shipped |
| Web and Telegram authorization/deduplication | adapter tests | M6, not shipped |

Legacy PostgreSQL, cloud routing, swarm, dashboards, trading, auto-learning and alternate entrypoint tests are retired with their implementations. They are not skipped successes.

Offline tests use disposable roots and loopback providers. Live tests require explicit selection and fail if the requested service/model is unavailable. Tests never download models or switch running services.
