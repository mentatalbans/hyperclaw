# Second review fixes

The user authorized fixing the six validated findings in the review of `e8ea07f`.

- [x] Preserve the complete conversation snapshot when an attachment save fails, with retry and cancellation coverage.
- [x] Give independent agent instances independent sessions, while allowing explicit durable session reuse.
- [x] Apply budget routing only when the fast slot supports the actual request; retain a working route otherwise.
- [x] Honor existing per-tool and configured default timeouts within the overall turn deadline.
- [x] Route interactive memory tools through the canonical memory owner, preserving their schemas and existing data access.
- [x] Import old terminal history once, respecting named sessions and durable reset markers.
- [x] Run focused regressions, the full suite, and independent standards/spec reviews; resolve material findings.
- [x] Verify local Qwen behavior, restart the running app with the fixes, and commit the verified result.

Memory and session fixes run independently; the parent owns routing, tool deadlines, integration, and final verification. Tests use temporary data and mock transports until the final synthetic local-model check. Existing user data and unrelated services are preserved.

Independent standards and specification reviews approved the final fixes after the identified persistence issues were resolved. File memory mutations run in a worker and settle disk/cache changes before returning cancellation, with responsive-loop, failure, and concurrent-operation coverage. The explicit importer rejects database connection failures and records completion in the selected destination; PostgreSQL imports commit copied rows and completion together. Complete file imports are serialized within each memory manager to preserve copy-once behavior and prevent overlapping imports from restoring forgotten facts.

Final verification: `.venv/bin/python -m pytest tests/ -q` completed with **634 passed, 1 skipped, 8 pre-existing warnings**. Coverage includes real temporary PostgreSQL databases, actual CLI imports, destination switches, overlapping imports, transactional retry, and source preservation. Qwen `qwen3.8:27b-mlx` at local Ollama passed synthetic memory store/search through the runtime, fresh-manager recall, migrated terminal-history recall, and durable reset. All synthetic model data used temporary roots.

The native HyperClaw process was restarted with the saved local profile at `http://127.0.0.1:8001`. The dashboard returns HTTP 200, `/health` reports healthy, and `/api/models` reports Ollama `qwen3.8:27b-mlx`. Ollama and unrelated services were left running.
