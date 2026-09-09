# Test battery

The battery combines existing unit and regression tests with real-process HTTP tests, disposable PostgreSQL checks, and opt-in Ollama checks. It keeps runtime data in temporary directories, disables dotenv and background integrations, and removes ambient model selection and common credentials from the test process. It does not stop or reconfigure an existing HyperClaw or Ollama server.

## Commands

Install development dependencies once:

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e '.[dev]'
```

| Command | What runs | Required services |
| --- | --- | --- |
| `make test` | Deterministic unit/regression and process HTTP tests | None; HTTP fixtures bind their own loopback ports |
| `make test-full` | Deterministic tests and database tests | PostgreSQL server binaries; the fixture starts its own cluster |
| `make test-live` | Five real-model scenarios | Running Ollama with the selected model installed |
| `make test-all` | All of the above, plus coverage | PostgreSQL binaries and Ollama |
| `make test-coverage` | Full deterministic/database suite, plus coverage | PostgreSQL binaries |

Set `PYTHON=python` when using an already activated development environment. Make targets use `.venv/bin/python` by default.

The runner also accepts focused paths:

```bash
.venv/bin/python scripts/test_battery.py quick tests/integration/test_http_process.py
.venv/bin/python scripts/test_battery.py full tests/unit/test_memory_review_fixes.py --coverage
.venv/bin/python scripts/test_battery.py live --ollama-model qwen3.8:27b-mlx --ollama-url http://127.0.0.1:11434
make test-live OLLAMA_MODEL=qwen3.8:27b-mlx OLLAMA_URL=http://127.0.0.1:11434
```

Direct pytest remains supported. Cases depending on the disposable PostgreSQL fixture are automatically marked `postgres`, including indirect fixture dependencies. Parameterized cases that acquire it dynamically declare the marker on the database parameter. Real-model cases carry `ollama` and are deselected by default. Explicitly selecting live tests with no available model fails setup. It never pulls models.

```bash
.venv/bin/python -m pytest tests/ -m postgres --require-postgres -q
.venv/bin/python -m pytest tests/live --run-ollama -q
```

Use the runner for temporary-root and environment isolation; direct pytest expects the caller to supply its desired environment.

## Database prerequisites

Install PostgreSQL using your platform's package manager, such as `brew install postgresql@17` on macOS or `sudo apt-get install postgresql` on Ubuntu. Tests need `initdb`, `pg_ctl`, and `postgres`, and must run as a non-root user. They create their own cluster with TCP disabled and never use `DATABASE_URL`.

The fixture searches `PATH`, Homebrew PostgreSQL directories, and `/usr/lib/postgresql/*/bin`. To select a particular installation:

```bash
.venv/bin/python scripts/test_battery.py full --postgres-bin /opt/homebrew/opt/postgresql@17/bin
```

`full` and `all` fail if PostgreSQL cannot run. `quick` excludes database cases entirely. Plain pytest permits a clearly reported skip when server binaries are unavailable. The database tests exercise conversation/state persistence, transactions, text recall, session visibility, migrations, import rollback, and competing imports. They adapt the vector column for plain PostgreSQL; embedding quality and pgvector similarity operators are outside this battery.

## Process and model scenarios

Process tests launch the actual CLI and uvicorn on kernel-assigned ports, using a scripted loopback Messages provider. They check startup/profile/model metadata, JSON chat, both streaming routes, incremental SSE delivery, thinking separation, restart persistence, session isolation and reset, provider outages, and interrupted-stream recovery. Every child process and provider listener is cleaned up, with startup/output logs attached to failures.

Live checks use temporary data and the selected Ollama transport:

1. A nonempty plain response with local provenance and usage accounting.
2. Streamed text, persisted output, and one usage record per inference.
3. An opaque exhibit label recalled after runtime restart, absent from another session and after reset.
4. Real memory tool calls, durable storage, and a fresh-manager search result reaching the model.
5. A synthetic image reaching a model with vision support.

Each live scenario has a 120-second deadline. The suite checks model availability and capabilities first. It requires tool and vision support for those cases and reports a failure if either is absent. Prompts, images, and remembered facts are synthetic; live tests expose only the memory tools needed for the scenario. Model assertions check observable contracts and short opaque values.

## Reports and coverage

Every run prints a unique directory under `test-results/`, containing:

- `summary.json`: mode, command, Python version, start time, duration, exit status, and JUnit test counts.
- `junit.xml`: test outcomes, individual durations, and failure details. Live cases also record model/server version, elapsed time, and usage metadata.
- `pytest.log`: complete test output, warnings, slowest tests, and captured failure diagnostics.
- With `--coverage`: `coverage.json`, `coverage.xml`, and `htmlcov/index.html`.

Coverage measures statements and branches in `hyperclaw`, `core`, `memory`, `security`, and `models`. Python child processes are included through [Coverage.py's subprocess support](https://coverage.readthedocs.io/en/latest/subprocess.html). Other packages such as `civilization` and `integrations` still have tests but are not included in this coverage rollup. Existing optional connectors and legacy computer tools have large coverage gaps; a passing battery does not imply that every integration has been exercised. CI enforces test outcomes and reports coverage. Use the reports to prioritize gaps against the coverage target in CONTRIBUTING.md.

Timing reports are measurements, not performance thresholds. Live duration depends on model load and available hardware. Known pre-existing warnings remain visible: a Starlette deprecation, unawaited HyperShield audit coroutines, and additional `datetime.utcnow()` deprecations on Python 3.13. The existing stub-tool check may skip because this distribution ships no stub-tool filter.

## CI

`.github/workflows/tests.yml` runs `full --coverage` on Ubuntu with Python 3.11 and 3.13 for pushes, pull requests, and manual workflow dispatch. It installs PostgreSQL, requires the database tests to run, and uploads reports even after a failed test step. Actions are pinned to verified commit IDs. Live Ollama tests are kept out of hosted CI and can be run on the local machine with `make test-live` or `make test-all`.
