# ⚡ HyperClaw

> **The AI that actually takes over.**

[![Tests](https://img.shields.io/badge/tests-377%20passing-brightgreen)](tests/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![Version](https://img.shields.io/badge/version-v0.1.0--alpha-orange)](https://github.com/hyperclawai/hyperclaw/releases)
[![Agents](https://img.shields.io/badge/agents-50%2B-purple)](swarm/)
[![Connectors](https://img.shields.io/badge/connectors-30%2B-blue)](integrations/)
[![GitHub Stars](https://img.shields.io/github/stars/hyperclawai/hyperclaw?style=social)](https://github.com/hyperclawai/hyperclaw)

HyperClaw is an open-source, self-hosted multi-agent AI orchestration platform. One system. Every domain. Whether you're running a company, planning your week, managing your health, or building something new — HyperClaw brings intelligent automation to whatever matters to you.

**One command. No limits. For everyone.**

---

## Coming from OpenClaw?

If you're running OpenClaw as your personal AI — HyperClaw is what it grows into.

OpenClaw handles your personal life with a single intelligent agent: your calendar, communications, research, and daily operations — all in one conversational interface. HyperClaw runs your *organization* with 50+ specialists, a self-expanding swarm under the SOLOMON overmind, a GENESIS Protocol that creates new agents on demand, and a Civilization Knowledge Base that learns how your business actually works.

The architecture is the same. The scale is not.

**OpenClaw → personal AI OS.** One agent. Your life.  
**HyperClaw → enterprise AI orchestration.** 50+ agents. Your organization.

One command still gets you started.

---

## Install

```bash
pip install hyperclaw
```

Or from source:

```bash
git clone https://github.com/hyperclawai/hyperclaw.git
cd hyperclaw
pip install -e ".[dev]"
```

## Quick Start

```bash
# First-time setup
hyperclaw init

# Boot PROMETHEUS — SOLOMON comes online
hyperclaw start

# Submit a goal to SOLOMON
hyperclaw swarm run "Analyze our Q1 revenue trends and identify the top 3 growth opportunities"

# Ingest your organization's knowledge
hyperclaw civ ingest ./docs/sops/

# See what's missing
hyperclaw civ gaps
```

---

## What HyperClaw Does

**For individuals:** Manages your entire life — health, finance, scheduling, research, communications — across every platform you already use.

**For organizations:** Learns how your organization actually works — ingests your SOPs, job descriptions, checklists, org charts, and client profiles — then operates against that knowledge as a deeply embedded team member across every domain.

**For civilization:** A self-expanding intelligence that grows its own capabilities, fills its own knowledge gaps, and compounds in intelligence every single day it runs.

---

## PROMETHEUS Architecture

```
SOLOMON (Overmind — always on, always watching)
├── 50+ Domain Specialists
│   Sciences, Law, Medicine, Finance, Engineering,
│   Philosophy, Business, Intelligence, Creative Arts...
├── GENESIS Protocol (self-expanding — creates new specialists)
├── Three-Tier Memory
│   ├── Session (fast, in-context)
│   ├── Domain (persistent per-specialist)
│   └── Civilizational Graph (permanent, growing — pgvector)
├── Civilization Knowledge Base
│   ├── SOPs + Workflows
│   ├── Job Descriptions + Roles
│   ├── Checklists + Runbooks
│   ├── Org Charts + Reporting Structures
│   ├── Client Profiles
│   └── Personal Routines
├── 30+ Platform Connectors (HyperClaw Gateway)
│   Telegram, Slack, Discord, WhatsApp, Teams, Signal,
│   Gmail, Google Workspace, Microsoft 365, Notion,
│   Salesforce, HubSpot, Jira, GitHub, Stripe, and more
├── HyperShield Security (zero-trust, hot-reload YAML)
├── UCB1 HyperRouter (self-improving model routing)
└── Three Human Gates
    ├── Ethical Gate
    ├── Authorization Gate
    └── Uncertainty Gate (confidence < 60%)
```

---

## Civilization Knowledge Base

The moat. Every other AI platform starts each task with zero organizational context. HyperClaw ingests all of it, versions it, and injects the relevant slice into every specialist's prompt before they touch a task. The result: agents that operate like people who've been inside your organization for years.

### What it captures

| Node Type | Description |
|---|---|
| **SOP** | Standard operating procedures with step-order preserved |
| **Job Description** | Roles, responsibilities, KPIs, reporting structure |
| **Role** | Operational accountability, decision authority, escalation paths |
| **Person** | Team members, expertise, preferences, reporting chain |
| **Checklist** | Recurring verification tasks with completion tracking |
| **Runbook** | Incident response and technical operational procedures |
| **Workflow** | Process graphs with decision nodes and SLA tracking |
| **Org Chart** | Full organizational hierarchy, traversable by any agent |
| **Client Profile** | Goals, pain points, health score, communication preferences |
| **Personal Routine** | Individual operating rhythms and non-negotiables |
| **Policy** | Organizational policies injected into relevant agent contexts |
| **Knowledge Article** | Freeform institutional knowledge |

### Three things that make this technically distinct

**1. Procedural chunker preserves step order** — standard RAG chunking destroys SOPs by splitting them across chunks. This chunker treats each step as a unit with its surrounding context baked in, so agents can retrieve step 4 without losing the fact that step 3 must happen first.

**2. Interview agent fills the tacit knowledge gap** — most organizational knowledge is in people's heads, never written down. The interview agent conducts Socratic sessions with your team to extract what they know and convert it into structured, versioned knowledge nodes.

**3. OrgGraph traversal** — SOLOMON knows the reporting chain, knows who owns which SOP, knows which clients a person serves. Context injection becomes role-aware, not just topic-aware.

### Commands

```bash
hyperclaw civ ingest <file_or_dir>   # Ingest documents
hyperclaw civ interview start         # Start knowledge elicitation
hyperclaw civ gaps                    # Detect missing knowledge
hyperclaw civ gaps fill               # Interview to fill top gap
hyperclaw civ nodes list              # All knowledge nodes
hyperclaw civ org chart               # Print org chart as tree
hyperclaw civ stats                   # Coverage score by type
hyperclaw civ staleness               # Nodes not updated in 90+ days
hyperclaw civ sync notion             # Sync from Notion
hyperclaw civ sync gdrive             # Sync from Google Drive
```

---

## 30+ Platform Connectors

Every inbound message from any platform routes to SOLOMON automatically via the HyperClaw Gateway. A Telegram message, a Slack slash command, and a WhatsApp voice note all hit the same agent pipeline.

### Messaging
Telegram · Slack · Discord · WhatsApp · Microsoft Teams · Signal · iMessage · SMS (Twilio) · Email (SMTP/Gmail/Outlook)

### Google Workspace
Gmail · Calendar · Drive · Docs · Sheets · Meet · Tasks · Analytics

### Microsoft 365
Outlook · OneDrive · SharePoint · Calendar

### Productivity
Notion · Airtable · Trello · Asana · Linear · Todoist

### Enterprise
Salesforce · HubSpot · Jira · Confluence

### Developer
GitHub · GitLab

### Finance
Stripe · QuickBooks

### Storage
Box · Dropbox

### Communication
Zoom · Twilio

### Automation
Zapier · Make

### Data
Supabase · PostgreSQL · Google Analytics

```bash
hyperclaw integrations list         # All connectors + status
hyperclaw integrations test <id>    # Health check a connector
hyperclaw gateway start             # Start inbound message router
hyperclaw gateway status            # Active platforms + message counts
```

---

## HyperCore: UCB1 Routing

HyperRouter uses the **UCB1 multi-armed bandit algorithm** to continuously learn which model performs best for each task type:

```python
ucb1_score = mean_reward + C * sqrt(ln(total_attempts) / attempts)
```

Models start unexplored (score = ∞) and are tried once before exploitation begins. The best model for each task type rises to the top over time. Budget and latency constraints filter candidates before scoring.

---

## HyperShield Security

All agent actions governed by YAML policies with per-agent granularity. Hot-reload without restart:

```yaml
agents:
  VITALS:
    network_mode: "isolated"
    hypershield_context: "health_data"
    require_explicit_consent: true
  LEDGER:
    hypershield_context: "financial_data"
    egress_allowlist:
      - "api.stripe.com"
      - "quickbooks.api.intuit.com"
```

Finance connectors (Stripe, QuickBooks) run in `financial_data` context. Health data in `health_data` context. Signal in `isolated` context. All enforced at the architecture level.

---

## GENESIS Protocol

When SOLOMON encounters a task outside the expertise of all 50+ current specialists, it doesn't fail — it builds:

```
GENESIS detects knowledge gap
  ↓
SCOUT researches domain (arXiv, GitHub, PubMed)
  ↓
ALCHEMIST builds specialist agent from research
  ↓
Certifier validates against test suite
  ↓ (if certified)
New specialist registered in PROMETHEUS permanently
  ↓
CALIBRATOR updates UCB1 routing scores
```

Zero blind spots. Self-expanding by design.

---

## Recursive Growth Engine

Every 6 hours, HyperClaw upgrades itself:

```
SCOUT → sweeps arXiv, GitHub, PubMed for new techniques
  ↓
ALCHEMIST → validates discoveries, implements, certifies
  ↓ (if certified)
CausalGraph ← skill node + causal edge written to HyperMemory
  ↓
CALIBRATOR → reads performance log, identifies routing inefficiencies
  ↓
HyperRouter ← updated UCB1 scores
```

---

## Database

HyperClaw uses PostgreSQL with pgvector for all memory, knowledge, and agent state. Supabase is the recommended host.

```bash
hyperclaw init          # Runs migrations automatically
hyperclaw doctor        # Verify DB + all system checks
```

**Migrations:**
- `001_hypermemory.sql` — HyperMemory causal graph (nodes, edges, impacts, sessions)
- `002_civilization.sql` — Civilization Knowledge Layer (nodes, versions, edges, interviews, gaps, sync log)

---

## CLI Reference

```bash
# Core
hyperclaw init                        # Initialize DB + config
hyperclaw start                       # Boot PROMETHEUS
hyperclaw doctor                      # System health check
hyperclaw version                     # Print version

# Swarm
hyperclaw swarm run "<goal>"          # Submit goal to SOLOMON
hyperclaw prometheus specialists      # List all 50+ specialists
hyperclaw prometheus genesis run      # Create specialist for new domain
hyperclaw prometheus memory stats     # Three-tier memory stats

# Civilization Knowledge
hyperclaw civ ingest <file>           # Ingest document
hyperclaw civ interview start         # Knowledge elicitation interview
hyperclaw civ gaps                    # Show knowledge gaps
hyperclaw civ org chart               # Print org chart
hyperclaw civ stats                   # Node count + coverage score
hyperclaw civ staleness               # Stale nodes (90+ days)

# Integrations
hyperclaw integrations list           # All 30+ connectors
hyperclaw integrations test <id>      # Test connector health
hyperclaw gateway start               # Start inbound message router
hyperclaw gateway status              # Active platforms

# Security
hyperclaw policy reload               # Hot-reload HyperShield policies
hyperclaw policy status               # Active policy summary
hyperclaw audit recent                # Recent audit log
hyperclaw audit blocked               # Blocked events (24h)

# Memory
hyperclaw memory stats                # HyperMemory node/edge counts
hyperclaw impact                      # Impact summary by domain
```

---

## Architecture Overview

| Component | Technology |
|---|---|
| Overmind | SOLOMON |
| Specialists | 50+ domain agents |
| Expansion | GENESIS Protocol |
| Memory | pgvector + PostgreSQL (Supabase) |
| Security | HyperShield (zero-trust, hot-reload YAML) |
| Routing | UCB1 bandit (self-improving) |
| Knowledge | Civilization Knowledge Base |
| Connectors | 30+ platform integrations |
| Growth | Recursive Growth Engine (SCOUT → ALCHEMIST → CALIBRATOR) |

---

## Contributing

MIT-licensed and open for contributions.

```bash
git clone https://github.com/hyperclawai/hyperclaw.git
cd hyperclaw
pip install -e ".[dev]"
python3 -m pytest tests/ -v
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines. Join the community on Discord.

---

## License

MIT — use it, fork it, build on it.

---

*Built by Hyper Nimbus. Powered by PROMETHEUS.*
