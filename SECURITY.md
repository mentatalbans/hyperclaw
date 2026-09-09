# Security model

Runtime v2 targets one local operator on macOS/Linux. Default root: `~/.hyperclaw-v2`. Setup refuses existing unmarked/v1 roots. Tests use disposable data. The token stays private (0600), and the root stays private (0700). One daemon locks the root and owns SQLite, model IO, and tool processes.

Every `/v1` route requires the operator bearer token, including approvals, grants, workspace metadata and receipts. `/healthz` reports liveness only. Host validation and origin rejection apply. Tokens do not appear in URLs or daemon discovery metadata. Client disconnect never cancels a run.

M2's closed tool catalog validates arguments and the per-run allowlist, records intent, and checks authority immediately before effects. File writes and command execution have separate workspace grants. Without a grant, operator approval binds exact invocation arguments, schema/policy hash, workspace identity and expiry. Model output cannot grant authority. Changes invalidate old approvals.

Host file tools traverse relative to owned directory descriptors and reject symlinks, parent escapes, absolute paths, hardlinks, devices and FIFOs. Reads, searches and outputs are bounded. Publication uses a temporary file and descriptor-relative rename, then observes the final content hash. Publication that cannot be verified is uncertain. User projects require an explicit workspace selection; cwd is never implicitly selected.

Commands run in digest-pinned Docker containers with an unprivileged user, read-only root, dropped capabilities, no-new-privileges, CPU/memory/PID limits, bounded temporary storage/logs/output, and no network. Only the chosen workspace is mounted. An execution approval does not grant a writable mount. No host-shell fallback, Docker socket mount, runtime database mount, operator-home mount or ambient environment forwarding is available to the command tool. Run the Linux daemon as the workspace owner; root uses the unprivileged fallback UID documented in README.

Docker is a chosen isolation boundary, not proof against kernel or Docker vulnerabilities. Explicitly selecting a workspace exposes that workspace's contents to admitted tools; review its contents and grants accordingly. Docker ownership labels and persisted IDs support cancellation/reconciliation without touching foreign installations. Receipts prevent replay of completed invocations but cannot promise exactly-once external effects. Unknown outcomes remain uncertain and never automatically retry. A command/file check failure overrides model claims of success.

The configured Ollama endpoint is explicit; there is no provider fallback or model download. Loopback does not prove the model server itself is offline. Untrusted image/text/model content has no path to operator approval APIs through the tool catalog. Memory, imported skills, MCP and messaging adapters have not shipped. Switching an existing running service remains a separate operational action.
