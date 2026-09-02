# Changelog

All notable changes to HyperClaw will be documented in this file.

> **Versioning note:** entries up to and including [0.2.0] (git tag `v0.2.0`)
> follow the repository's original 0.x scheme, while packages published to
> PyPI used 1.0.x numbering. From [1.1.0] onward the repository and package
> versions are unified in a single 1.x lineage; `v0.2.0` maps into it as the
> release immediately preceding 1.1.0.

## [1.2.0] - 2026-09-01

### Added
- **Provider routing**: capability-based slots (`primary`, `tools`, `vision`,
  `fast`, `embeddings`, `images`) over user-configured providers, declared in
  `models.yaml` (`providers:` + `slots:`). A provider is live only when every
  env var it names resolves; slot ladders are filtered to live providers, so
  unconfigured providers are never called and never warned about. Streaming
  failover: pre-first-token failures move to the next rung transparently;
  mid-stream deaths are marked, never re-answered. Every turn records
  `served_by`, which the identity line reports.
- `hyperclaw doctor` prints the provider/slot matrix; `--probe` sends one
  cheap request per live provider and reports latency.

### Changed
- ChatJimmy is configured solely via `CHATJIMMY_BASE_URL`/`_API_KEY`/`_MODEL`
  — the official Taalas API (by application), not the chatjimmy.ai demo;
  hardcoded URLs removed.
- Router/swarm/TUI model maps read from `models.yaml` instead of hardcoded
  model id strings.

### Fixed
- Memory writes no longer print `OPENAI_API_KEY not set` on every call —
  the embeddings backend is announced once at startup (semantic via a
  configured provider, or local hash matching).

## [1.1.0] - 2026-08-27

### Fixed
- **Swarm registry: dynamic agent discovery.** The hand-maintained import list
  silently dropped agents added later (VENTURE, the trading trio, ARBITER,
  SENTINEL). `AgentRegistry.build_default()` now walks `swarm.agents` and
  registers every `BaseAgent` subclass — 44 agents total — and agent_id
  collisions are domain-qualified (`HERALD-COMMS`, `SCOUT-TALENT`) instead of
  silently overwritten. Trading agents accept the shared dependency set.
- CLI and TUI banners report the real package version instead of a hardcoded
  `0.1.0-alpha`.
- **Thinking-block-safe response parsing**: reading `response.content[0].text`
  crashes on adaptive-thinking models (Claude 5 family, Opus/Sonnet 4.6+) whose
  first content block can be a thinking block. All 15 call sites now go through
  `hyperclaw.api_utils.extract_text()` / `extract_json()`, which join text
  blocks and skip thinking/tool_use blocks.
- **Swarm agents failing to load (0 of 56)**: `agents.yaml` was looked up at
  hardcoded paths that a fresh install never populates. Config files now ship
  inside the package (`hyperclaw/default_config/`) and `find_config()` seeds
  `~/.hyperclaw/config/` on first access; all four loaders use it.
- TUI conversation repair (`clean_history`) now enforces the Messages API tool
  contract — ordering and adjacency of `tool_use`/`tool_result` pairs — instead
  of a global ID lookup, and iterates the repair passes to a fixpoint — dropping
  a leading assistant message can orphan a tool_result that was valid a sweep
  earlier. Fixes recurring 400 `unexpected tool_use_id` errors on resumed or
  trimmed sessions.
- Conversation reset preserves recent plain-text turns instead of wiping all
  context.
- Integration credentials (Telegram, ElevenLabs, GitHub, OpenAI, Slack, Discord)
  added to `~/.hyperclaw/.env` mid-session are picked up on the next tool call
  via `env_var()` — no restart or manual export needed. `HYPERCLAW_MODEL` is now
  read after `.env` loads, so it can live in the file.
- Onboarding rejects Supabase database URLs still containing the
  `[YOUR-PASSWORD]` template placeholder and re-prompts.

### Added
- **Telegram file understanding**: send a document or photo and ask about it.
  PDFs and images reach the model as native Anthropic content blocks, small
  text files are inlined, oversized files (>20 MB Telegram cap) get an honest
  refusal. A caption on the upload is answered immediately; otherwise the file
  is staged for your next message. Final answers re-render with Markdown.
- **Identity & persona**: the assistant introduces itself from
  `~/.hyperclaw/config.json` (ai_name/user_name, with `config/settings.json`
  fallback), loads `~/.hyperclaw/CLAUDE.md` as persona/standing instructions,
  and states its backing model when asked.
- `hyperclaw-telegram` now runs the full Telegram bot (`hyperclaw.telegram_bot`)
  with a live thinking preview (italic, GIL-style; `TELEGRAM_SHOW_THINKING=0`
  disables), a typing indicator kept alive for the whole turn, and streaming
  answer edits. `ChatAgent.stream_events()` yields ("thinking"|"text", delta)
  tuples for UIs. Allowlist is the union of `TELEGRAM_ALLOWED_CHAT_IDS` and
  `TELEGRAM_CHAT_ID`, deny-by-default when both are empty; config loads from
  `~/.hyperclaw/.env` (works under launchd).
- `swarm_roster` TUI tool: lists all 44 specialist swarm agents by domain without
  instantiating them; `agent_status` description now points to it for the full swarm.
- `HYPERCLAW_MODEL` env override honored by the TUI (previously hardcoded).

### Fixed
- Replaced retired model IDs (`claude-sonnet-4-20250514`, `claude-opus-4-20250514`,
  `claude-sonnet-4-5-20241022`) with current ones (`claude-sonnet-4-6`,
  `claude-opus-4-8`, `claude-sonnet-4-5`) across the TUI, server, prometheus,
  solomon, swarm, and vision provider.
- `agents/learning.py` no longer crashes at import when the db directory is missing:
  parent dir is created and storage errors are logged instead of raised.
- Onboarding creates the full `~/.hyperclaw` subtree (memory, workspace, secrets,
  logs, config), not just the root.
- Added missing `__init__.py` to `swarm/agents/{comms,intelligence,trading}`.
- TUI: bounded API retry with exponential backoff and fail-fast on 4xx (clear
  retired-model hint on 404); silenced noisy HTTP client loggers; prompt shows the
  configured AI name.

### Changed
- Unified the version to 1.1.0 everywhere: `pyproject.toml` said 0.2.0,
  `hyperclaw/__init__.py` said 1.0.9 (the last published dist), and the CLI banner
  said 0.1.0-alpha.

## [0.2.0] - 2026-08-20

### Added
- **Claude 5 family support** with tiered routing: requests are classified and routed
  across `claude-sonnet-5` / `claude-opus-5` / `claude-fable-5` (env-configurable via
  `HYPERCLAW_*_MODEL` / `FABLE_MODEL`), with a `claude-fable` tier in the model router
  at current list pricing.
- **Automatic model failover**: on overload, rate-limit, or transient API errors the
  bridge walks fable -> opus -> sonnet instead of failing the turn; non-retryable
  errors fail fast.
- **Universal file delivery** (`send_file` tool + `media_hub` + per-conversation
  `outbox`): deliver any document, image, video, or audio file into the current
  conversation or explicitly via Telegram (auto photo/video/audio/document),
  iMessage attachments, email attachment, or open it locally.
- **Email suite** on the Gmail API: HTML bodies (`body_html`), in-thread replies,
  attachments, `email_forward` (body + attachments), `email_draft`
  (drafts-as-approval workflow), `email_mark` (read/archive/star), `email_search`.
- **Offsite heartbeat scaffold**: a dead-man's-switch launchd job template that pings
  an external monitor with per-service status every 5 minutes (inert until
  `HEARTBEAT_URL` is set).
- **Persona templating**: assistant identity loads from an untracked persona file
  (`persona.example.md`, `PERSONA_FILE`) instead of source code.
- `SECURITY.md`: DM exposure model and fail-closed channel allowlists
  (`IMESSAGE_ALLOWED_CONTACTS`, `TELEGRAM_CHAT_ID`).

### Fixed
- Model tier registry pointing every tier at one model; corrected per-model pricing.
- Anthropic API calls no longer send `temperature` to models that reject it.
- Raised default `max_tokens` for adaptive-thinking models.
- Loop guards in the agentic bridge (identical-call repetition, consecutive errors).

## [1.0.8] - 2026-03-31

### Added
- Production-ready documentation with complete API reference
- TUI command interface with signal handling and smart routing
- Database schema for PostgreSQL with pgvector support
- Cost-optimized model routing (ChatJimmy integration for 100x cheaper simple tasks)
- Enhanced multi-agent coordination system with 44 specialized agents
- Persistent memory architecture with working, episodic, and semantic layers

### Enhanced
- README with comprehensive architecture documentation
- API endpoints for chat, tasks, memory, and cost management
- CLI commands for setup, server management, and memory operations
- Workspace structure with proper configuration files

### Fixed
- Cloud deployment configuration moved to root directory
- Database connection handling and initialization
- Tool result handling and history management
- Telegram import made optional to prevent startup failures

## [1.0.7] - 2026-03-30

### Added
- Major AGI evolution architecture
- 29-agent specialized swarm deployment
- Enhanced integration bridges (Telegram, iMessage, Email)
- HyperClaw Command Center UI with real-time monitoring
- Parallel processing architecture
- Security enhancements and cognitive monitoring
- Complete swarm orchestration system

### Technical
- Cross-entity AI framework for the HyperClaw ecosystem
- Advanced context management and tool integration
- Multi-modal file handling system
- Continuous learning and optimization engines

## [1.0.6] - 2026-03-29

### Fixed
- Clean orphaned tool_results from session history
- Improved error handling with better user feedback
- History trimming before reset to prevent memory issues

## [1.0.5] - 2026-03-28

### Fixed
- Enhanced error handling and display
- Better session management and reset procedures
- Improved stability for long-running sessions

## [1.0.3] - 2026-03-27

### Fixed
- Made Telegram import optional to prevent startup failures
- Better handling of missing optional dependencies
- Improved graceful degradation

## [1.0.2] - 2026-03-26

### Added
- Initial multi-agent system
- Basic integration framework
- Core memory management

## [1.0.1] - 2026-03-25

### Added
- Basic chat functionality
- Simple agent system
- Initial configuration setup

## [1.0.0] - 2026-03-24

### Added
- Initial release of HyperClaw
- Core AI assistant functionality
- Basic setup and configuration