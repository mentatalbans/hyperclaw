# HyperClaw

**Your personal AI that actually gets things done.**

[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/downloads/)
[![PyPI](https://img.shields.io/pypi/v/hyperclaw)](https://pypi.org/project/hyperclaw/)

HyperClaw is a personal AI assistant that works across your entire life. Not just chat — it connects to your email, calendar, tasks, documents, and more. It remembers everything, learns your preferences, and coordinates 44 specialized AI agents to help you with anything.

---

## What Can It Do?

- **Manage your communications** — Email, Telegram, Slack, Discord, WhatsApp, Teams
- **Organize your work** — Calendar, tasks, projects, documents
- **Handle your data** — Notion, Airtable, Google Sheets, Salesforce, HubSpot
- **Support your business** — Invoicing, customer tracking, sales pipelines
- **Research anything** — Web search, document analysis, data synthesis
- **Remember everything** — Your preferences, history, context across all sessions
- **Optimize costs** — Smart model routing uses cheap models for simple tasks

One AI. Every platform. All working together.

---

## Architecture

### Cost-Optimized Model Router

HyperClaw intelligently routes tasks to the most cost-effective model:

| Model | Use Case | Cost |
|-------|----------|------|
| **ChatJimmy** (Llama 3.1 8B) | Simple queries, classification, quick lookups | ~$0.00001/1k tokens |
| **Claude Haiku** | Moderate tasks, basic analysis | ~$0.001/1k tokens |
| **Claude Sonnet** | Complex tasks, coding, writing | ~$0.003/1k tokens |
| **Claude Opus** | Deep reasoning, research, planning | ~$0.015/1k tokens |

Simple "what time is it?" goes to ChatJimmy. Complex "analyze this report and create a strategy" goes to Claude.

### Multi-Agent Coordination

44 specialized agents organized by domain:

- **Business (11):** Strategos, Herald, Pipeline, Ledger, Counsel, Talent, Nexus, Ops, Revenue, Sovereign, Venture
- **Personal (6):** Atlas, Midas, Vitals, Nourish, Navigator, Hearth
- **Scientific (5):** Medicus, Cosmos, Gaia, Oracle, Scribe
- **Communications (5):** Echo, Envoy, Pulse, Cipher, Herald
- **Talent (4):** Scout, Deal, Stage, Roster
- **Trading (3):** Prediction Strategist, Polymarket Trader, Global Prediction Engine
- **Technology (3):** Aegis, Bridge, Forge
- **Recursive (3):** Scout, Alchemist, Calibrator
- **Intelligence (2):** Sentinel, Arbiter
- **Creative (2):** Author, Lens

Tasks are automatically routed to the best agent based on domain and complexity.

### Persistent Memory

Memory persists across sessions:

- **Working Memory** — Current context and active tasks
- **Episodic Memory** — Conversation history and decisions
- **Semantic Memory** — Facts and knowledge
- **Instincts** — Learned behavioral patterns

---

## Getting Started

### Option 1: Quick Start (5 minutes)

**macOS:**
```bash
brew install pipx
pipx install hyperclaw
hyperclaw setup
```

**Linux/Windows:**
```bash
pip install hyperclaw
hyperclaw setup
```

Then start the server:
```bash
hyperclaw server
```

Or use interactive chat:
```bash
hyperclaw chat
```

### Option 2: Self-Host with Docker

```bash
# Clone the repo
git clone https://github.com/mentatalbans/hyperclaw.git
cd hyperclaw

# Copy the example config
cp .env.example .env

# Edit .env and add your Anthropic API key
# ANTHROPIC_API_KEY=sk-ant-your-key

# Optional: Add ChatJimmy for cheap simple tasks
# CHATJIMMY_API_KEY=your-taalas-key

# Start
docker-compose up
```

Open `http://localhost:8001` in your browser.

### Option 3: Production Setup

```bash
# Clone and setup
git clone https://github.com/mentatalbans/hyperclaw.git
cd hyperclaw
pip install -r requirements.txt

# Initialize workspace and config
python -m hyperclaw setup

# Initialize database (requires DATABASE_URL in .env)
python -m hyperclaw setup --init-db

# Start server
python -m hyperclaw server --port 8001
```

---

## Configuration

### Required
- **ANTHROPIC_API_KEY** — Powers the AI brain
  - Get one at [console.anthropic.com](https://console.anthropic.com)

### Recommended
- **DATABASE_URL** — PostgreSQL with pgvector for memory
  - Easiest: [Supabase](https://supabase.com) (free tier works)
  - Run `schema/init.sql` to create tables

- **CHATJIMMY_API_KEY** — Cheap model for simple tasks
  - Get one at [taalas.ai](https://taalas.ai)
  - Reduces costs by 90%+ for simple queries

### Optional Integrations

**Messaging:**
```bash
TELEGRAM_BOT_TOKEN=your-bot-token
SLACK_BOT_TOKEN=xoxb-your-token
```

**Email:**
```bash
GMAIL_CLIENT_ID=...
GMAIL_CLIENT_SECRET=...
GMAIL_REFRESH_TOKEN=...
```

See [.env.example](.env.example) for all available integrations.

---

## API Endpoints

### Chat
```bash
# Simple chat
curl -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What can you help me with?"}'

# Streaming chat
curl -X POST http://localhost:8001/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "Explain quantum computing", "stream": true}'
```

### Tasks
```bash
# Create a task
curl -X POST http://localhost:8001/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"goal": "Research competitors in AI space", "domain": "business"}'

# Get task status
curl http://localhost:8001/api/tasks/abc123

# List all tasks
curl http://localhost:8001/api/tasks
```

### Multi-Agent Coordination
```bash
# Coordinate complex goal across multiple agents
curl -X POST http://localhost:8001/api/coordinate \
  -H "Content-Type: application/json" \
  -d '{"goal": "Create a complete marketing strategy for product launch"}'
```

### Memory
```bash
# Store a memory
curl -X POST http://localhost:8001/api/memory/remember \
  -H "Content-Type: application/json" \
  -d '{"content": "User prefers concise responses", "importance": 0.8}'

# Recall memories
curl -X POST http://localhost:8001/api/memory/recall \
  -H "Content-Type: application/json" \
  -d '{"query": "user preferences"}'
```

### Cost Management
```bash
# Get current costs
curl http://localhost:8001/api/costs

# Set daily budget
curl -X POST "http://localhost:8001/api/costs/budget?budget_usd=5.0"

# List available models
curl http://localhost:8001/api/models
```

### System
```bash
# Health check
curl http://localhost:8001/health

# Full status
curl http://localhost:8001/status

# List agents
curl http://localhost:8001/api/agents

# List integrations
curl http://localhost:8001/api/integrations
```

---

## CLI Commands

```bash
# Setup workspace and configuration
hyperclaw setup
hyperclaw setup --init-db  # Also initialize database

# Start the server
hyperclaw server
hyperclaw server --port 8080

# Interactive chat
hyperclaw chat

# Check status
hyperclaw status

# Memory operations
hyperclaw memory list
hyperclaw memory recall "user preferences"
hyperclaw memory remember "Important note"

# Version
hyperclaw version
```

---

## Workspace Structure

After setup, HyperClaw creates:

```
~/.hyperclaw/
├── workspace/
│   ├── SOUL.md           # AI personality
│   ├── IDENTITY.md       # AI configuration
│   ├── USER.md           # Your profile
│   ├── MEMORY.md         # Working memory
│   └── secrets/
│       └── .env          # API keys
├── memory/
│   ├── instincts.md      # Learned behaviors
│   ├── core-episodes.md  # Key memories
│   └── daily/            # Daily logs
├── config/
│   └── hyperclaw.yaml    # System config
└── logs/
```

Edit these files to customize your assistant's behavior.

---

## Cost Optimization Tips

1. **Use ChatJimmy** — Add `CHATJIMMY_API_KEY` to route simple tasks to a model that costs 100x less

2. **Set a budget** — `hyperclaw` respects `DAILY_BUDGET_USD` and falls back to cheaper models when exceeded

3. **Enable prefer_cheap** — Set `PREFER_CHEAP_MODELS=true` to always prefer the cheapest capable model

4. **Monitor usage** — Check `/api/costs` to see spend by model

---

## Database Setup (Optional but Recommended)

For persistent memory across sessions, set up PostgreSQL with pgvector:

1. Create a Supabase project (free) or use any PostgreSQL
2. Enable the `vector` extension
3. Run `schema/init.sql` to create tables
4. Add `DATABASE_URL` to your `.env`

```bash
# Initialize database
python -m hyperclaw setup --init-db
```

---

## Privacy & Security

- **Your data stays yours** — Self-host means nothing leaves your machine
- **No tracking** — We don't collect anything
- **Open source** — Audit the code yourself
- **Per-agent permissions** — Control what each agent can access

---

## Troubleshooting

**"API key not working"**
- Make sure it starts with `sk-ant-`
- Check for extra spaces when pasting

**"Database connection failed"**
- Verify your DATABASE_URL is correct
- For Supabase, use the "Transaction pooler" connection string

**"Integration not connecting"**
- Run `hyperclaw integrations test <name>` to diagnose
- Check that API keys are in your `.env` file

**Need help?**
- Run `hyperclaw status` to check system health
- Open an issue on [GitHub](https://github.com/mentatalbans/hyperclaw/issues)

---

## Contributing

MIT licensed. Contributions welcome.

```bash
git clone https://github.com/mentatalbans/hyperclaw.git
cd hyperclaw
pip install -e ".[dev]"
python -m pytest tests/ -v
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## License

MIT — use it, modify it, build on it.
