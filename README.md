# HyperClaw runtime v2

A local assistant with durable chat, image input, scoped file tools, explicit memory, reviewed skills, admitted documentation tools, and owned Docker commands in one Python package. M1 through M5 are implemented. [Testing](docs/testing.md) records verification and later milestones. The original platform remains in git at `dcad202`.

Python 3.11+ on macOS/Linux. For development and the full test suite, install with `uv sync --locked --extra dev --extra mcp`. The runtime's MCP extra is optional.

```sh
uv run --locked hyperclaw --help
uv run --locked hyperclaw init
uv run --locked hyperclaw doctor
uv run --locked hyperclaw doctor --probe
make test
```

The default root is `~/.hyperclaw-v2`, selected by `--root`, then `HYPERCLAW_ROOT`. Settings are CLI overrides over `root/config.toml` over shipped defaults. Default model: `qwen3.8:27b-mlx` at `http://127.0.0.1:11434`. There is no automatic model download or cloud fallback. Ordinary setup and doctor do not contact a model; `--probe` checks the installed catalog.

Initialization refuses nonempty unmarked roots and v1 data. M1 runtime-v2 databases migrate transactionally with a backup; already accepted M1 requests retain an empty tool allowlist. M2 databases receive additive schedule tables at schema version 3; M3 databases receive additive memory/FTS tables at schema version 4. M4 databases receive additive skill admission at schema version 5 and MCP admission at schema version 6. M6 adds Telegram session mappings, intake cursors/journals and delivery records at schema version 7. Existing accepted run and schedule tool lists stay unchanged. Personal data and existing running installations are not automatically adopted.

Start a disposable daemon, then submit from another terminal:

```sh
hyperclaw --root /tmp/hyperclaw-demo init
hyperclaw --root /tmp/hyperclaw-demo serve --port 0
hyperclaw --root /tmp/hyperclaw-demo chat "Describe a paper kite." --no-tools
hyperclaw --root /tmp/hyperclaw-demo chat "Draft a short note." --detach
hyperclaw --root /tmp/hyperclaw-demo run inspect RUN_ID
hyperclaw --root /tmp/hyperclaw-demo run events RUN_ID --after 3
hyperclaw --root /tmp/hyperclaw-demo run cancel RUN_ID
hyperclaw --root /tmp/hyperclaw-demo session reset SESSION_ID
```

Use a fresh demo directory. Default listener: `127.0.0.1:8011`; `serve --port 0` selects a free port. `root/daemon.json` contains discovery metadata, and `root/token` contains the private bearer token. All `/v1` requests require `Authorization: Bearer TOKEN`; public `/healthz` returns 503 if the worker or maintenance task becomes unavailable. Host must match the selected loopback endpoint; cross-origin requests are disabled.

`chat` reports session/run IDs on stderr; reuse `--session SESSION_ID` to continue. `--request-id ID` makes identical submissions idempotent; changing the payload conflicts. `--retry-of RUN_ID` links an explicit new attempt to a failed, cancelled or interrupted run in the same generation. Unknown outcomes are `uncertain` and cannot be retried through that operation. Only one active run occupies a session, including while waiting for approval; other sessions share one worker.

Ctrl-C while observing detaches. Explicit `run cancel` stops work and waits for owned process termination evidence. Prior effects are not undone. Only complete successful turns, with intact tool-call/result groups, enter later prompts. Partial text remains in events. Reset hides old messages without deleting old runs. Shutdown interrupts active work and preserves queued work. Startup reconciles owned invocations before starting the queue.

## Workspace and approvals

The default workspace is `root/workspace`. To select a project, explicitly set an absolute `workspace_path` in `root/config.toml` before launching the daemon. The current directory is never implicitly mounted. One daemon uses one workspace; grants bind its path and filesystem identity. The runtime root and operator home cannot be selected as the workspace.

New runs offer `workspace_read`, `workspace_list`, `workspace_search`, `workspace_write`, `command`, `memory_remember`, `memory_search`, `memory_correct`, and `memory_forget`. Use repeated `--tool NAME` to restrict a run, or `--no-tools` for chat only. File tools reject symlinks, parent traversal, absolute paths, hardlinks and nonregular files. Reads and writes are limited to 64 KiB; writes require existing parent directories. Search is literal and bounded.

File reads are admitted by default. File writes and commands have separate authority. Grant workspace writes once before submitting a file task:

```sh
hyperclaw --root /tmp/hyperclaw-demo workspace
hyperclaw --root /tmp/hyperclaw-demo grant write --workspace-id WORKSPACE_ID
hyperclaw --root /tmp/hyperclaw-demo chat "Write hello into answer.txt." --tool workspace_write
hyperclaw --root /tmp/hyperclaw-demo run receipts RUN_ID
```

A file write or command without its grant pauses for an exact invocation approval. Chat displays the call and detaches. Review the durable arguments and hashes, then approve or deny:

```sh
hyperclaw --root /tmp/hyperclaw-demo approval list
hyperclaw --root /tmp/hyperclaw-demo approval approve APPROVAL_ID --arguments-sha256 ARGUMENT_HASH --policy-sha256 POLICY_HASH
hyperclaw --root /tmp/hyperclaw-demo run events RUN_ID
```

`approval deny` takes the same IDs/hashes. Approvals expire after 24 hours and release the worker while pending. Resume uses persisted arguments and remaining execution time. Changing a grant, schema or workspace invalidates prior approval; cancel and submit a fresh request under the new policy. The model cannot approve itself or change grants. Persistent grants are configured through the authenticated operator interface.

A write receipt reports the observed SHA-256. Commands may request file/hash checks. A wrong expected hash fails verification and the run even if the model claims success. General answers have `verification=not_requested`; a complete answer is not proof of an external task.

## Commands and images

Commands execute argv inside Docker, with no host-shell fallback. A shell can be explicitly selected *inside* the container. The versioned image is pinned to:

```text
python:3.13.15-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e
```

Prepare it with `docker pull` using that exact reference. Containers have an unprivileged user, read-only root, dropped capabilities, no new privileges, no network, 1 CPU, 256 MiB memory, 64 PIDs, and a 16 MiB temporary filesystem. Only the selected workspace is mounted. Execution approval alone keeps the workspace read-only; a separate write grant makes it writable. Runtime credentials, operator home and Docker socket are not mounted. Docker unavailability produces an actionable failure; ordinary chat and scoped file tools remain usable. On Linux, run the daemon as your ordinary user so private workspace permissions match the container UID. A root daemon uses UID 65532 and requires an explicitly accessible workspace.

Each invocation records intent before effects and a receipt before cleanup. Cancellation stops/kills and inspects the container; closing the Docker client process is not termination evidence. Lost backend/outcome evidence produces `uncertain`, never an automatic re-execution. These controls are a deployment boundary, not a claim of absolute isolation; see [Security](SECURITY.md).

Image input supports PNG, JPEG, GIF and WebP with validated base64/type/size limits. The CLI reads only explicitly selected image files:

```sh
hyperclaw --root /tmp/hyperclaw-demo chat "Describe this image." --image sample.png --context-bytes 8388608 --no-tools
```

The default serialized conversation budget is 64 KiB; attachments can explicitly raise it to at most 8 MiB. The loop allows at most 12 model rounds and three identical tool calls, with 64 KiB tool output and tool deadlines up to 60 seconds. Default model/run deadlines are 120/600 seconds. Run time excludes queue and approval waits; after abrupt death, a running interval is conservatively charged until recovery because the crash instant is unknown.

## Explicit memory

Memory is stored only when an admitted memory tool runs. Ordinary conversation does not automatically create facts. Use the same session to store and retrieve a fact:

```sh
hyperclaw --root /tmp/hyperclaw-demo chat "Use memory_remember to store: the archive drawer label is juniper-482." --tool memory_remember
hyperclaw --root /tmp/hyperclaw-demo chat "Use memory_search to find the archive drawer label." --session SESSION_ID --tool memory_search
hyperclaw --root /tmp/hyperclaw-demo run receipts RUN_ID
```

Use the session ID reported by the first command. Writes default to that session; tool argument `scope=workspace` explicitly shares a fact with other sessions in the selected workspace. A session search sees its private facts plus shared workspace facts. A workspace search sees shared facts only. Session reset retains explicit memory while clearing conversation context.

`memory_correct` takes a record ID and replacement text, creates a new version and removes the old version from retrieval. `memory_forget` removes the selected active version from retrieval. Both require the record's exact scope; changing a shared record requires explicit workspace scope. Remember/correct accept optional `valid_until` with a timezone offset. Expired records are excluded at the expiry instant. Receipts retain IDs, scope, source run, observation/expiry times and version links. Forget preserves historical versions and receipts; it is not archive erasure.

Memory text is limited to 2,048 UTF-8 bytes and search queries to 1,024 bytes. Retrieval uses SQLite FTS5/BM25 over literal query words and returns at most five eligible records. Scope, active status and expiry filter candidates before ranking and the result limit; BM25 corpus statistics are global. This lexical baseline can miss paraphrases without shared words. There are no embeddings, automatic promotions or learned authority. Facts are unverified text: storing an instruction does not grant file-write or command permission. Memory effects, index updates and their receipts commit in one transaction.

## Reviewed skills

Skills are explicit instruction packages beneath `root/skills/NAME/`. Copy a reviewed package, such as [documentation-answer](examples/skills/documentation-answer/SKILL.md), there, inspect its complete content and hash through the daemon, then admit that exact hash:

```sh
hyperclaw --root /tmp/hyperclaw-demo skill inspect documentation-answer
hyperclaw --root /tmp/hyperclaw-demo skill admit documentation-answer --content-hash CONTENT_HASH
hyperclaw --root /tmp/hyperclaw-demo chat "Summarize the citation rules in this skill." --skill documentation-answer --no-tools
hyperclaw --root /tmp/hyperclaw-demo skill list
hyperclaw --root /tmp/hyperclaw-demo skill revoke documentation-answer
```

The supported `SKILL.md` subset has exactly single-line `name` and `description` front matter plus a Markdown instruction body. Metadata values may be plain or JSON-double-quoted strings. Ordinary Markdown links can reference bounded `.md`/`.txt` files beneath the package. External HTTP(S) citations remain text and are never fetched. Executable hooks, extra metadata such as `allowed-tools`, escaping paths, symlinks, hardlinks and nontext payloads are rejected. This deliberately narrow subset does not claim compatibility with every skill bundle.

Names use 1–64 lowercase letters, digits and hyphens, with no leading, trailing or consecutive hyphens. Descriptions are limited to 1,024 UTF-8 bytes; `SKILL.md` and each referenced file to 16,384 bytes; all loaded content to 32,768 bytes. A package may reference at most 16 resources, and traversal stops at 512 entries or depth 16. Unreferenced files are never injected into the prompt.

Each run may explicitly select up to four admitted skills. Selected hashes are bound when the run is submitted. A queued run fails if its package changes before execution. Once execution begins, its recorded instruction snapshot is used across approval waits; revoke or replacement admission blocks further use. New package content requires renewed admission for new runs. Instructions count toward the context budget and are not automatically carried into later unselected runs. Skill text cannot change the run's tool allowlist or grant file-write, command or MCP authority.

## Documentation MCP

The optional MCP integration serves an admitted copy of public Markdown and text documentation. Install the extra with `uv sync --locked --extra dev --extra mcp`, then [build the documentation image explicitly](examples/mcp-docs/README.md). Before starting the daemon, set `mcp_docs_path` in `root/config.toml` to an absolute public documentation directory and `mcp_docs_image` to the built image's immutable ID or repository digest. Both settings are required together. `mcp_protocol` defaults to `2026-07-28`; `2025-11-25` is also supported.

Inspect the file/catalog/image/protocol fingerprint, admit that exact value, then explicitly select the documentation tools:

```sh
hyperclaw --root /tmp/hyperclaw-demo mcp inspect
hyperclaw --root /tmp/hyperclaw-demo mcp admit --expected-sha256 FINGERPRINT
hyperclaw --root /tmp/hyperclaw-demo chat "What happens to schedules after session reset? Cite the documentation source." --tool mcp_docs_search --tool mcp_docs_read --skill documentation-answer
hyperclaw --root /tmp/hyperclaw-demo run receipts RUN_ID
hyperclaw --root /tmp/hyperclaw-demo mcp revoke
```

The skill in this example must first be admitted as described above. Documentation admission creates a separate snapshot beneath the runtime root. Each call runs in an owned container with only that snapshot mounted read-only at `/docs`, an unprivileged user, no network and bounded resources. It receives neither the workspace nor runtime credentials. Results identify source paths, line ranges and file hashes; the run records the admitted documentation revision. Source, catalog, image or protocol changes that alter the fingerprint require renewed admission and block pending use. Revoke retains historical manifests, snapshots and receipts; re-admitting the same content does not restore an old run's authority.

Source collection accepts at most 512 `.md`/`.txt` files, each up to 65,536 UTF-8 bytes and 8 MiB combined, with traversal capped at 4,096 entries and depth 32. Search returns at most five hits; read returns at most 200 lines and 8,192 UTF-8 bytes. Symlinks, hardlinks, nonregular files and path escapes are rejected. Tool results and wire frames have separate bounds. The supported SDK/protocol features and explicit Docker/model test commands are described in [Testing](docs/testing.md).

MCP tools support directly submitted runs, including `chat --detach`. Schedule creation rejects MCP tools; scheduled admission and revocation handling is deferred.

## Schedules

Schedules submit ordinary runs to the same worker and use the same tool policy, approvals and receipts. Use a session ID reported by `chat` or created through the authenticated session API:

```sh
hyperclaw --root /tmp/hyperclaw-demo schedule create daily-note "Draft a short note." --session SESSION_ID --due 2036-01-01T09:00:00Z --interval-seconds 86400 --no-tools
hyperclaw --root /tmp/hyperclaw-demo schedule list
hyperclaw --root /tmp/hyperclaw-demo schedule inspect daily-note
hyperclaw --root /tmp/hyperclaw-demo schedule occurrences daily-note
hyperclaw --root /tmp/hyperclaw-demo schedule pause daily-note
hyperclaw --root /tmp/hyperclaw-demo schedule retarget daily-note --expected-generation 0
```

Omit `--interval-seconds` for a one-shot. Due times require an explicit timezone and normalize to UTC; years 1970–9998 and intervals of 1–31,536,000 seconds are accepted. Schedule IDs accept 1–256 ASCII letters, digits, `-`, `_`, `.`, or `~`, except standalone `.` and `..`. Creation fetches the session's current generation. Repeating an ID with the same creation payload is idempotent; a changed payload conflicts. The `schedule:` request-ID prefix is reserved for scheduled runs.

While a session is occupied, its earliest pending due time stays pending. After downtime or a busy period, one run represents that pending occurrence and the next due time advances along the original interval cadence to a future instant. Missed intervals do not create a burst of runs. Reservation, enqueue and advancement commit together. Health, event replay, cancellation, approval expiry and schedule polling continue while the worker handles slow model or tool work.

Session reset pauses its schedules. `retarget` requires the schedule's expected generation and fetches the session's current generation before resuming. Pausing does not cancel queued runs; cancel those explicitly. An uncertain scheduled outcome pauses future occurrences as `uncertain_effect` and cannot be resumed through retarget. Completed schedules cannot be retargeted. Occurrences already accepted remain consumed after cancellation, failure or interruption.

## Local web client

Start the daemon as usual, then open the loopback URL printed by `hyperclaw serve` (by default `http://127.0.0.1:8011/`). Enter the operator token from the selected runtime root's `token` file. The token stays in page memory only and is cleared from the input after connecting and from memory when you disconnect or reload; it is never placed in the URL or browser storage.

The web client can create and select sessions, submit text with an explicit tool allowlist and admitted skills, inspect current and older-generation runs, reconnect event observation, cancel, reset the conversation generation, review receipts, and decide exact pending approvals. A submission whose HTTP outcome is ambiguous remains available as “Retry same request”; this deliberately reuses its original request ID. The browser never resubmits automatically during connection or stream recovery.

The web surface is local and operator-authenticated. Only the empty shell and its two fixed assets are public; every `/v1` state request still requires the bearer token. No service installation or switch is implied.


## Optional Telegram adapter

Telegram is disabled by default. It runs inside the daemon and uses the same Runtime, local model, Store and operator policy. Initialize a new root first, then have the operator create `root/telegram-token` containing the BotFather token and one optional final newline. Use a private editor or secret provisioning mechanism: the file must be owned by the daemon user, mode `0600`, a regular file with one hard link, and never a symlink. Do not place the token in TOML, a command argument or an environment variable. Initialization deliberately does not create this credential.

Set these nonsecret fields in `root/config.toml` before starting your chosen daemon:

```toml
telegram_enabled = true
telegram_allowed_pairs = ["123456789:123456789", "-1001234567890:987654321"]
```

Every private chat and group requires an explicit `CHAT_ID:SENDER_ID` pair. IDs use canonical decimal notation: a nonzero signed chat ID and positive sender ID, each less than 2^52 in magnitude, without whitespace, leading zeroes or a leading `+`; duplicates are rejected. Bot identity is checked with `getMe`. An existing webhook prevents startup and is never deleted or replaced. Production connects only to the official HTTPS Telegram API, with environment proxies and redirects disabled.

Supported input is ordinary text or one photo (Telegram's size variants count as one) with an optional caption. Downloads are authorized before metadata lookup and again before fetching, limited to 4 MiB, and checked using the runtime's image attachment validation. Photos use an explicit 8 MiB context budget; text uses 64 KiB. Edits, forwards, service messages, bots, anonymous/channel senders and other attachment types are rejected. Routing separates bot identity, chat, topic and sender into distinct sessions. An occupied session rejects another update explicitly; reset races retain the original generation instead of routing into a newer conversation.

Runs use the default nine tools, with no automatic skill/MCP admission or new grants. Waiting approvals send a run reference for review through the local web UI or CLI; Telegram has no approval or grant administration. A pending approval does not block intake from other sessions. Replies in shared groups can be read by other group members: the allowlist controls who can submit work, not group confidentiality.

```sh
hyperclaw --root /path/to/root telegram status
```

The same bounded, sanitized status is available at authenticated `GET /v1/telegram`. It includes the most recent 50 intake and 50 delivery records. Update IDs and normalized requests commit before submission; reopening reconciles the exact accepted run without replaying it. Poll offsets advance only with durable intake, including rejected updates without their message text. Each update has independent approval and terminal delivery phases. Replies are plain text, at most 3,500 UTF-16 units including the run ID and any truncation note, and target the originating message/topic.

A send records `sending` before HTTP and `sent` only with a valid Telegram message receipt. Explicit API rejection is `failed`; a lost/invalid response or restart during sending becomes `uncertain`. Neither is retried automatically, since Telegram may already have delivered the message. Review these records and the local run before manually following up. Authorization is checked again before delivery, including after a configuration change and restart. Disabling Telegram leaves its journal inspectable without reading the bot credential or contacting Telegram. Stop/start only the daemon you intend to operate; configuration changes do not adopt or restart another installation.
