# ⚡ HyperClaw

> **The AI that actually takes over.**

[![Tests](https://img.shields.io/badge/tests-184%20passing-brightgreen)](tests/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![Version](https://img.shields.io/badge/version-v0.1.0--alpha-orange)](https://github.com/mentatalbans/hyperclaw/releases)
[![Agents](https://img.shields.io/badge/agents-22-purple)](swarm/agents/)
[![GitHub Stars](https://img.shields.io/github/stars/mentatalbans/hyperclaw?style=social)](https://github.com/mentatalbans/hyperclaw)

HyperClaw is an open-source, self-hosted, Claude-native multi-agent AI swarm platform built to manage every domain of human life and work — from daily scheduling and personal health to enterprise operations, deep scientific research, and space exploration data analysis.

**One command. Full transparency. No limits.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-150%20passing-brightgreen.svg)](tests/)

---

## Install

```bash
pip install hyperclaw
```

Or from source:

```bash
git clone https://github.com/mentatalbans/hyperclaw.git
cd hyperclaw
pip install -e ".[dev]"
```

## Quick Start

```bash
# First-time setup
hyperclaw init

# Boot the full swarm + TUI
hyperclaw start

# Run a goal
hyperclaw swarm run "Analyze our Q1 revenue trends and identify top 3 growth opportunities"

# Trigger a research sweep
hyperclaw research sweep
```

---

## What HyperClaw Does

HyperClaw deploys a swarm of 23 specialist AI agents across 5 domains. Each agent is purpose-built, UCB1-optimized for its task types, and protected by HyperShield security policies. They collaborate, bid for tasks, and continuously improve through the Recursive Growth Engine.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    HyperClaw Platform                    │
├──────────────┬──────────────────┬───────────────────────┤
│  HyperCore   │   HyperSwarm     │   HyperMemory          │
│              │                  │                        │
│ HyperState   │ NEXUS (orch.)    │ CausalGraph            │
│ HyperRouter  │ BidProtocol      │ VectorStore            │
│ UCB1 Bandit  │ 23 Agents        │ AgentMemory            │
│ FastLoop     │ AutoGenBridge    │ ImpactTracker          │
│ SlowLoop     │ AgentRegistry    │ Migration Runner       │
├──────────────┴──────────────────┴───────────────────────┤
│            HyperShield Security Layer                    │
│  PolicyEngine  │  NetworkGuard  │  FilesystemGuard       │
│  AuditLogger   │  Hot-reload    │  Per-agent policies    │
├─────────────────────────────────────────────────────────┤
│          Recursive Growth Engine                         │
│  SCOUT → ALCHEMIST → CALIBRATOR (runs every 6h)         │
└─────────────────────────────────────────────────────────┘
```

---

## Agent Taxonomy

### Personal Domain (6 agents)
| Agent | Role | Primary Model |
|-------|------|---------------|
| **ATLAS** | Life Coordinator — schedules, habits, goals | Claude Sonnet |
| **MIDAS** | Personal Finance — budgets, investments | Claude Sonnet |
| **VITALS** | Health & Wellness — symptoms, wellness ⚕️ | Claude Sonnet |
| **NOURISH** | Nutrition & Fitness — meals, workouts | ChatJimmy + Claude |
| **NAVIGATOR** | Travel & Logistics — itineraries, routing | ChatJimmy + Claude |
| **HEARTH** | Home Management — maintenance, vendors | ChatJimmy |

### Business Domain (6 agents)
| Agent | Role | Primary Model |
|-------|------|---------------|
| **STRATEGOS** | Executive Intelligence — strategy, competitive intel | Claude Sonnet |
| **HERALD** | Output & Delivery — assembles final deliverables | Claude Sonnet |
| **PIPELINE** | Sales & Marketing — leads, outreach, campaigns | ChatJimmy + Claude |
| **LEDGER** | Financial Operations — reporting, forecasting | Claude Sonnet |
| **COUNSEL** | Legal & Compliance — research, analysis ⚖️ | Claude Sonnet |
| **TALENT** | HR & People — hiring, performance | Claude Sonnet |

### Scientific Domain (5 agents)
| Agent | Role | Primary Model |
|-------|------|---------------|
| **MEDICUS** | Healthcare & Biology — literature, genomics ⚕️ | Claude Sonnet |
| **COSMOS** | Space & Astronomy — orbital calculations, data | Claude Sonnet + Code |
| **GAIA** | Climate & Environment — emissions, sustainability | Claude Sonnet |
| **ORACLE** | Quantitative Analysis — statistical models, code | Claude Sonnet + Code |
| **SCRIBE** | Research Synthesis — triage and deep synthesis | ChatJimmy + Claude |

### Creative Domain (2 agents)
| Agent | Role | Primary Model |
|-------|------|---------------|
| **AUTHOR** | Long-form Writing — research-backed prose | Claude Sonnet |
| **LENS** | Research & Retrieval — fast lookup + synthesis | ChatJimmy + Claude |

### Recursive Domain (3 agents + NEXUS)
| Agent | Role | Primary Model |
|-------|------|---------------|
| **SCOUT** | Capability Research — arXiv, GitHub, PubMed | ChatJimmy + Claude |
| **ALCHEMIST** | Skill Integration — validates, implements, certifies | Claude Code |
| **CALIBRATOR** | Performance Optimization — routing analysis | ChatJimmy |
| **NEXUS** | Swarm Facilitator — orchestrates all agents | Claude Sonnet |

---

## HyperCore: UCB1 Routing

HyperRouter uses the **UCB1 multi-armed bandit algorithm** to continuously learn which model performs best for each task type:

```python
ucb1_score = mean_reward + C * sqrt(ln(total_attempts) / attempts)
```

Models start as "unexplored" (score = ∞) and are tried once before exploitation begins. Over time, the best model for each task type rises to the top. Budget and latency constraints filter candidates before scoring.

**Model fleet:**
| Model | Use | Cost | Latency |
|-------|-----|------|---------|
| `claude-sonnet-4-6` | Research, analysis, synthesis | $0.003/1k | ~2s |
| `chatjimmy` | Routing, classification, quick tasks | $0.000001/1k | ~50ms |
| `claude-code` | Code generation + certification | $0.003/1k | ~3s |
| `nim-local` | Local inference (zero cost) | Free | ~500ms |

---

## Certification Loop

HyperClaw certifies outputs empirically — no hallucinations, no guesses:

```
1. Claude generates code/analysis
2. ClaudeCodeSubagent executes in sandboxed subprocess
3. Test trace captured (stdout, stderr, exit code)
4. Certifier validates: test_trace ≠ ∅, result ≠ ∅, certified = True
5. CertifiedMethod written to HyperState
6. Causal edge written to HyperMemory (cause → effect)
```

> **ChatJimmy outputs are never auto-certified.** All ChatJimmy outputs must pass through Claude verification before certification. This is enforced at the architecture level.

---

## HyperMemory Causal Graph

HyperMemory is not just a vector store — it's a structured causal knowledge graph. Every certified method writes:
- A **cause node** (the action taken)
- An **effect node** (the result produced)
- A **causal edge** with confidence score

This lets HyperClaw answer: *"What caused this outcome?"* and *"What does this action usually produce?"*

---

## HyperShield Security

All agent actions are governed by YAML policies with per-agent granularity:

```yaml
agents:
  VITALS:
    network_mode: "isolated"      # no outbound connections
    require_explicit_consent: true
  SCOUT:
    egress_allowlist:
      - "arxiv.org"
      - "api.github.com"
    network_mode: "read_only"
```

Policies hot-reload without restart:
```bash
hyperclaw policy reload --path security/policies/custom.yaml
```

---

## Recursive Growth Engine

Every 6 hours, HyperClaw upgrades itself:

```
SCOUT → sweeps arXiv, GitHub, PubMed for new techniques
  ↓
ALCHEMIST → validates discoveries, implements via ClaudeCodeSubagent
  ↓ (if certified)
CausalGraph ← skill node + causal edge written
  ↓
CALIBRATOR → reads SwarmMessage log, identifies routing inefficiencies
  ↓
HyperRouter ← updated UCB1 scores
```

---

## Configuration

`config/hyperclaw.yaml`:
```yaml
database:
  url: "postgresql://localhost/hyperclaw"

models:
  default: "claude-sonnet-4-6"
  chatjimmy_url: "https://chatjimmy.ai/api"

router:
  fast_loop_enabled: true
  slow_loop_interval_seconds: 300

swarm:
  max_agents: 50
  default_domain: "business"
```

Environment variables:
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export DATABASE_URL="postgresql://localhost/hyperclaw"
```

---

## CLI Reference

```bash
# Core
hyperclaw init              # Initialize DB + config
hyperclaw start             # Boot swarm + TUI
hyperclaw version           # Print version

# States
hyperclaw state list        # List active HyperStates
hyperclaw state inspect <id> # Inspect a HyperState

# Swarm
hyperclaw swarm run "<goal>" # Submit goal to NEXUS
hyperclaw agent list         # All agents + domains + UCB1 scores
hyperclaw agent status <id>  # Specific agent detail

# Research
hyperclaw research sweep      # Trigger manual SCOUT sweep
hyperclaw research discoveries # Show recent discoveries
hyperclaw skills list         # Show certified skills

# Security
hyperclaw policy reload       # Hot-reload HyperShield policies
hyperclaw policy status       # Show active policy + agent policies
hyperclaw audit recent        # Last 20 audit events
hyperclaw audit blocked       # Blocked events (24h)

# Memory & Impact
hyperclaw impact              # Impact summary by domain
hyperclaw memory              # HyperMemory node/edge counts
```

---

## Domains

HyperClaw operates across **5 unlimited domains**:
- **Personal** — health, finance, scheduling, travel, nutrition, home
- **Business** — strategy, sales, finance, legal, HR, output
- **Scientific** — medicine, astronomy, climate, statistics, research
- **Creative** — writing, research, narrative
- **Recursive** — self-improvement, capability research, skill integration

---

## Contributing

HyperClaw is MIT-licensed and open for contributions.

```bash
git clone https://github.com/mentatalbans/hyperclaw.git
cd hyperclaw
pip install -e ".[dev]"
pytest tests/ -v --cov=core --cov=memory --cov=security --cov=models
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## License

MIT License — use it, fork it, build on it.

---

*Built by Hyper Nimbus. Powered by Claude.*
