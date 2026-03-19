# ⚡ HyperClaw

> **The AI that actually takes over.**

HyperClaw is a fully original, open-source, MIT-licensed, unlimited-domain multi-agent AI swarm platform. It is not a fork or wrapper of any existing product.

## What is HyperClaw?

HyperClaw orchestrates autonomous AI agents across any domain — business, scientific research, personal productivity, creative work, and recursive self-improvement. It uses a UCB1 multi-armed bandit router to continuously learn which models and agents perform best for each task type, then routes work accordingly.

## Architecture

```
HyperCore
├── HyperState     — Pydantic v2 state management + method certification
├── HyperRouter    — UCB1 bandit routing (FastLoop + SlowLoop)
└── HyperShield    — Policy enforcement + audit logging

HyperSwarm
├── HyperNexus     — Central agent coordination hub
├── BidProtocol    — Agent task bidding
└── AutoGenBridge  — AutoGen compatibility layer

HyperMemory
├── VectorStore    — pgvector semantic memory
├── CausalGraph    — Cause-effect tracking
├── AgentMemory    — Per-agent episodic memory
└── ImpactTracker  — Business/scientific impact measurement

Models
├── ClaudeClient         — Anthropic Claude (async, retry, streaming)
├── ChatJimmyClient      — Taalas HC1 / Llama 3.1 8B @ 17k tok/sec
├── ClaudeCodeSubagent   — Empirical code certification loop
└── ModelRouter          — UCB1-driven model selection
```

## Model Stack

| Model | Use Case | Cost | Latency |
|-------|----------|------|---------|
| `claude-sonnet-4-6` | Research, analysis, synthesis, code | $0.003/1k | ~2s |
| `chatjimmy` | Routing, classification, quick tasks | $0.000001/1k | ~50ms |
| `claude-code` | Specialized code generation | $0.003/1k | ~3s |
| `nim-local` | Local inference (zero cost) | Free | ~500ms |

> **Note:** ChatJimmy outputs are **never auto-certified**. All ChatJimmy outputs route through Claude verification before certification. This is enforced at the architecture level.

## Certification System

HyperClaw certifies methods empirically:

1. Claude generates code/analysis
2. Output executes in sandboxed subprocess
3. Test trace captured
4. If tests pass → `Certifier.certify()` promotes to `CertifiedMethod`
5. Certified methods stored in `HyperState.certified_methods`

## Quick Start

```bash
# Install
pip install hyperclaw

# Initialize DB and config
hyperclaw init

# Start the swarm
hyperclaw start

# List active states
hyperclaw state list

# Inspect a state
hyperclaw state inspect <state-id>
```

## Development

```bash
# Install with dev deps
pip install -e ".[dev]"

# Run tests
pytest tests/ -v --cov=core --cov=models --cov-report=term-missing

# Run specific test file
pytest tests/unit/test_bandit.py -v
```

## License

MIT License — see LICENSE file.

## Status

`v0.1.0-alpha` — HyperCore + Multi-Model Stack implemented.
HyperSwarm full orchestration, HyperMemory vector ops, and TUI in `v0.2.0`.
