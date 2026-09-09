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
| Broader process-kill recovery matrix and durable schedules | recovery/scheduling tests | M3, not shipped |
| Scope before ranking, corrections, fresh-store live memory tool | memory/evaluation tests | M4, not shipped |
| MCP protocol and reviewed skill packages | MCP/skill tests | M5, not shipped |
| Web and Telegram authorization/deduplication | adapter tests | M6, not shipped |

Legacy PostgreSQL, cloud routing, swarm, dashboards, trading, auto-learning and alternate entrypoint tests are retired with their implementations. They are not skipped successes.

Offline tests use disposable roots and loopback providers. Live tests require explicit selection and fail if the requested service/model is unavailable. Tests never download models or switch running services.

Run `make test` for offline checks, `make test-coverage` for statement/branch reports, `make test-live` for five Qwen scenarios, and `make test-docker` for the owned-container gates. `make test-all` explicitly selects offline, Ollama and Docker checks with coverage. Docker and model tests are excluded by default. Set `OLLAMA_URL`/`OLLAMA_MODEL` as Make variables to choose an already-installed target. The runner modes are `quick`, `docker`, `live`, and `all`; reports persist under ignored `test-results/<mode>-<timestamp>/` as summary JSON, JUnit, logs, and optional coverage.

The live M1 scenarios now use authenticated HTTP and real disposable daemon processes: text/provenance/usage, stream/provenance/usage, and an opaque conversation label across restart/new session/reset. M2 adds a synthetic red image and a real read/write workflow with an opaque marker and observed file hash. The three retained M1 scenarios explicitly use no tools to measure their single-request text/usage behavior. Fresh-store memory-tool testing remains M4.

Protocol basis: the transport uses [Ollama's Messages compatibility](https://docs.ollama.com/api/anthropic-compatibility). Reproducible installs use a checked [uv lockfile](https://docs.astral.sh/uv/concepts/projects/sync/).

Actual M1 results, gate mapping, coverage counts and a run/result/replay walkthrough are recorded in [the September 9 acceptance report](reports/2026-09-09-runtime-v2-m1.md). Python 3.11 and 3.13 each passed 88 offline tests; all three explicitly selected live Qwen cases passed. Hosted CI is configured but was not executed in this session.

M2 execution gates use real Workspace and Store with disposable roots. External backend failures are injected below the Executor; artifact effects, approvals and receipts stay real. Docker live cases cover parent/child termination, create/start reconciliation before queued work, read-only workspace enforcement, secret/network sentinels, resource settings and bounded output. Docker tests only remove containers carrying their unique installation labels. The tool image is digest-pinned in `containers/tool-runner/Dockerfile` and the backend; prepare that exact image before selected tests.

Public tests exercise the installed daemon and authenticated HTTP for checkpoint/restart/approval, observed artifact receipts, auth rejection and CLI decisions with exact hashes. Limits cover repeated requests, twelve rounds, attachment budgets, aggregate thinking signatures, content-block retention, output bounds and active execution budgets. The deadline test allows two seconds for real IO/SQLite scheduling while the fixture deliberately withholds completion; no 150 ms startup timing assumption remains.

M2 acceptance commands, platform results, review corrections and limits are recorded in [the M2 report](reports/2026-09-09-runtime-v2-m2.md). Hosted Linux CI includes an explicit Docker gate; a configured workflow alone is not claimed as an executed hosted run. A trusted disposable Linux verification runner, where used, is separate from the runtime's restricted command containers.
