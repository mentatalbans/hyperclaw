# Changelog

All notable changes to HyperClaw will be documented in this file.

## [1.1.0] - 2026-08-27

### Fixed
- TUI conversation repair (`clean_history`) now enforces the Messages API tool
  contract — ordering and adjacency of `tool_use`/`tool_result` pairs — instead
  of a global ID lookup. Fixes recurring 400 `unexpected tool_use_id` errors on
  resumed or trimmed sessions.
- Conversation reset preserves recent plain-text turns instead of wiping all
  context.
- Integration credentials (Telegram, ElevenLabs, GitHub, OpenAI, Slack, Discord)
  added to `~/.hyperclaw/.env` mid-session are picked up on the next tool call
  via `env_var()` — no restart or manual export needed. `HYPERCLAW_MODEL` is now
  read after `.env` loads, so it can live in the file.
- Onboarding rejects Supabase database URLs still containing the
  `[YOUR-PASSWORD]` template placeholder and re-prompts.

### Added
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