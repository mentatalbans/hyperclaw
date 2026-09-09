# Current Claw alternatives: evidence checked 2026-09-09

This is a comparison of documented architecture and release activity, not a benchmark or security audit. Only official repositories, documentation, and release records support the findings. No project was installed or executed, and no models were downloaded. Default-branch documentation is pinned below; its features can be newer than the listed stable release. Publication dates were checked against GitHub's release API, rather than search-index timestamps.

The useful distinction is the boundary each project chooses: per-session containers, configurable execution policy, capability-limited tool extensions, a compact deployable agent, or persistent learning and recall. Early-2026 feature tables miss substantial changes in these projects.

| Project and canonical repository | Latest stable release observed | Publication date (UTC) |
|---|---|---|
| NanoClaw, now `nanocoai/nanoclaw` | [v2.3.0 — release notes][N2] | 2026-08-24 |
| ZeroClaw, `zeroclaw-labs/zeroclaw` | [v0.8.5 — release notes][Z2] | 2026-09-05 |
| IronClaw, `nearai/ironclaw` | [ironclaw-v1.4.0 — release notes][I2] | 2026-08-28; release title says 2026-08-27 |
| PicoClaw, `sipeed/picoclaw` | [v0.3.1 — release notes][P2] | 2026-07-03; exclude the separately maintained prerelease `nightly` tag |
| Hermes Agent, `NousResearch/hermes-agent` | [v0.21.1, tag v2026.9.7 — release notes][H2] | 2026-09-07 |

Opening the original [qwibitai/nanoclaw repository](https://github.com/qwibitai/nanoclaw) redirects to `nanocoai/nanoclaw`. ZeroClaw's own [repository notice][Z1] identifies `zeroclaw-labs/zeroclaw` as canonical; similarly named forks should not supply the comparison evidence. All five repositories were unarchived when checked.

| Project | Documented implementation and deployment choices | Limits and implications |
|---|---|---|
| **NanoClaw** | A Node host routes work into Docker containers; its normal harness is the Claude Agent SDK. Channel/provider additions copy modules into the user's fork through skills. Codex, OpenCode, and local Ollama are documented alternatives. Agent groups have workspaces, memory, skills, and MCP templates. Setup requires Node 22+, pnpm, Docker, and Claude Code for customization. Outbound credentials go through OneCLI's vault. [README][N1] | The architecture draft describes separate inbound/outbound SQLite databases per session, each with one writer; multiple sessions can share an agent group's filesystem. It explicitly warns that portions describe intent and may drift from code. [Architecture][N3] v2.3.0 keeps Docker and SQLite as defaults while adding driver boundaries, stronger scheduled-task lifecycle rules, and adoption of running sessions after host restart. [Release][N2] |
| **ZeroClaw** | A Rust binary with provider, tool, channel, and memory components; the README names SQLite and embeddings. It supports Ollama/OpenAI-compatible endpoints, custom MCP servers, skills, cron management, and event-triggered procedures with approvals and resumable runs. Supervised autonomy is the advertised default. [README][Z1], [release][Z2] | OS sandbox choice depends on platform/configuration: Linux options include Landlock, Bubblewrap, Firejail, and Docker; macOS includes Seatbelt. Auto-selection can end at `none`. Default sandboxed tools have outbound network access; Landlock does not confine networking. Docker runtime executes shell invocations in ephemeral containers. External CLI providers have a separate permission boundary. These details prevent treating “sandboxed” as one uniform guarantee. [Sandboxing][Z3] |
| **IronClaw** | Rust runtime; documented WASM extensions use explicit capabilities, host-side credential injection, endpoint allowlisting, and resource limits. MCP, dynamic tools, scheduled/event routines, and persistent memory with hybrid retrieval are described. Current setup supplies local application state and installers. [README][I1] Ollama and OpenAI-compatible local services including vLLM/LM Studio are documented. [Providers][I3] | v1.4.0 adds persistent per-user Docker sandbox containers, managed egress, background children, and a durable notification inbox for outcomes/approvals; its Railway profile still uses ephemeral workers plus checkpointed workspaces. It also fixes libSQL write starvation. [Release][I2] The README retains an older PostgreSQL-versus-SQLite heritage comparison, so neither “PostgreSQL is mandatory” nor “WASM replaces Docker everywhere” is a sound current summary. [README][I1] |
| **PicoClaw** | Sipeed describes an independent Go implementation inspired by NanoBot. It distributes binaries for constrained systems, supports Ollama/vLLM, workspace `SKILL.md` skills and ClawHub registries, and native MCP with stdio/SSE/HTTP configurations. Web launcher builds additionally require Node and pnpm. The README still advises against production deployment before v1.0. [README][P1] | Workspace memory and sessions persist; cron jobs survive restart. Built-in guards constrain file paths and command lines, but the documentation explicitly says they do not recursively inspect child processes launched by build tools. It recommends a container/VM when stronger isolation is needed. Optional evolution modes observe completed work, draft skills, or apply accepted drafts; evolution is disabled by default. [Configuration][P3] |
| **Hermes Agent** | Focuses on agent-created reusable skills, recall, and background learning. It documents cron delivery, MCP, configurable providers, and terminal backends including local, Docker, SSH, Singularity, Modal, Daytona, and Vercel Sandbox. It also offers an OpenClaw migration path for settings, memories, skills, and keys. [README][H1] | Built-in memory is bounded `MEMORY.md`/`USER.md`; session search reads SQLite FTS5 history. Optional approval gates stage memory/skill changes. Managed local llama-server reviews wait for idle time, but that deferred queue is in memory and pending reviews disappear on exit. Separate profiles are required for separate agents sharing a host. These are concrete mechanisms, not proof that autonomous skill edits improve task quality. [Memory][H3] |

For HyperClaw, the engineering inference is to evaluate the particular subsystem needed before choosing a whole replacement. NanoClaw is useful for studying session/container lifecycle and credential separation; ZeroClaw for provider portability and configurable execution policy; IronClaw for capability boundaries and durable approvals; PicoClaw for constrained deployment; Hermes for curated memory and skill-review workflows. These are evaluation directions, not a tested ranking.

Local inference support also needs a separate capacity assessment: a small agent binary does not include the model's RAM/VRAM, inference server, browser, or container overhead. This review deliberately does not rank RAM usage, startup speed, cost, security, or reliability. Repository stars and maintainers' performance slogans are not measurements. A meaningful comparison would pin versions, models, tools, permissions, hardware, workload, and restart/failure scenarios.

The source inventory below contains fifteen substantive primary documents, counting the five releases above. File dates are **observation dates**, not claims about publication; immutable commit links preserve the inspected content. The initial NanoClaw redirect and GitHub metadata were used only for provenance/activity checks. All prose above paraphrases sources; no direct quotations are reproduced.

| ID | Official source title | Date |
|---|---|---|
| N1 | [NanoClaw README][N1] | Observed 2026-09-09 |
| N2 | [NanoClaw v2.3.0 release][N2] | Published 2026-08-24 |
| N3 | [NanoClaw Architecture (Draft)][N3] | Observed 2026-09-09 |
| Z1 | [ZeroClaw README][Z1] | Observed 2026-09-09 |
| Z2 | [ZeroClaw v0.8.5 release][Z2] | Published 2026-09-05 |
| Z3 | [ZeroClaw Sandboxing][Z3] | Observed 2026-09-09 |
| I1 | [IronClaw README][I1] | Observed 2026-09-09 |
| I2 | [IronClaw 1.4.0 release][I2] | Published 2026-08-28; label 2026-08-27 |
| I3 | [IronClaw LLM Providers][I3] | Observed 2026-09-09 |
| P1 | [PicoClaw README][P1] | Observed 2026-09-09 |
| P2 | [PicoClaw v0.3.1 release][P2] | Published 2026-07-03 |
| P3 | [PicoClaw Configuration Guide][P3] | Observed 2026-09-09 |
| H1 | [Hermes Agent README][H1] | Observed 2026-09-09 |
| H2 | [Hermes Agent v0.21.1 release][H2] | Published 2026-09-07 |
| H3 | [Hermes Agent Persistent Memory][H3] | Observed 2026-09-09 |

[N1]: https://github.com/nanocoai/nanoclaw/blob/2c754a2234390fcc597273cef6344d99e8ac03d0/README.md
[N2]: https://github.com/nanocoai/nanoclaw/releases/tag/v2.3.0
[N3]: https://github.com/nanocoai/nanoclaw/blob/2c754a2234390fcc597273cef6344d99e8ac03d0/docs/architecture.md#L1-L37
[Z1]: https://github.com/zeroclaw-labs/zeroclaw/blob/96c05632c3edafe6412149e6938aa0806b4d7400/README.md
[Z2]: https://github.com/zeroclaw-labs/zeroclaw/releases/tag/v0.8.5
[Z3]: https://github.com/zeroclaw-labs/zeroclaw/blob/96c05632c3edafe6412149e6938aa0806b4d7400/docs/book/src/security/sandboxing.md
[I1]: https://github.com/nearai/ironclaw/blob/0280dd1a48a4f813f123735b31558c459d1ce032/README.md
[I2]: https://github.com/nearai/ironclaw/releases/tag/ironclaw-v1.4.0
[I3]: https://github.com/nearai/ironclaw/blob/0280dd1a48a4f813f123735b31558c459d1ce032/docs/capabilities/llm-providers.md#L63-L96
[P1]: https://github.com/sipeed/picoclaw/blob/bbf6893ca7afad27f1d00a0f5a45982a549c6ed6/README.md
[P2]: https://github.com/sipeed/picoclaw/releases/tag/v0.3.1
[P3]: https://github.com/sipeed/picoclaw/blob/bbf6893ca7afad27f1d00a0f5a45982a549c6ed6/docs/guides/configuration.md
[H1]: https://github.com/NousResearch/hermes-agent/blob/6f3e630b47e5afb709a7e30738d66f82bdaa2459/README.md
[H2]: https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.7
[H3]: https://github.com/NousResearch/hermes-agent/blob/6f3e630b47e5afb709a7e30738d66f82bdaa2459/website/docs/user-guide/features/memory.md
