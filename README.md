# HyperClaw

**Your personal AI that actually gets things done.**

[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)

HyperClaw is a personal AI assistant that works across your entire life. Not just chat — it connects to your email, calendar, tasks, documents, and more. It remembers everything, learns your preferences, and coordinates 50+ specialized AI agents to help you with anything.

---

## What Can It Do?

- **Manage your communications** — Email, Telegram, Slack, Discord, WhatsApp, Teams
- **Organize your work** — Calendar, tasks, projects, documents
- **Handle your data** — Notion, Airtable, Google Sheets, Salesforce, HubSpot
- **Support your business** — Invoicing, customer tracking, sales pipelines
- **Research anything** — Web search, document analysis, data synthesis
- **Remember everything** — Your preferences, history, context across all sessions

One AI. Every platform. All working together.

---

## Getting Started

### Option 1: Quick Start (5 minutes)

```bash
# Install
pip install hyperclaw

# Run setup
hyperclaw init
```

The setup walks you through everything — no technical knowledge needed.

### Option 2: Self-Host with Docker

```bash
# Clone the repo
git clone https://github.com/mentatalbans/hyperclaw.git
cd hyperclaw

# Copy the example config
cp .env.example .env

# Edit .env and add your Anthropic API key
# ANTHROPIC_API_KEY=sk-ant-your-key

# Start
docker-compose up
```

Open `http://localhost:8000` in your browser.

### Option 3: One-Click Cloud Deploy

Deploy to your favorite cloud platform:

| Platform | Deploy |
|----------|--------|
| Railway | [![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/template/hyperclaw) |
| Render | [![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/mentatalbans/hyperclaw) |
| Fly.io | `fly launch` |

---

## What You Need

### Required
- **Anthropic API Key** — Powers the AI brain
  - Get one free at [console.anthropic.com](https://console.anthropic.com)

### Optional (but recommended)
- **Database** — Enables memory across sessions
  - Easiest: [Supabase](https://supabase.com) (free tier works great)
  - Or: Any PostgreSQL with pgvector

### Integrations (connect what you use)
- **Messaging:** Telegram, Slack, Discord, WhatsApp, Teams
- **Email:** Gmail, Outlook
- **Productivity:** Google Calendar, Notion, Todoist, Linear
- **Business:** Salesforce, HubSpot, Stripe, QuickBooks
- **Developer:** GitHub, Jira

See [.env.example](.env.example) for all available integrations.

---

## How It Works

```
You → NEXUS (orchestrator) → 50+ Specialist Agents → Results

Example:
"Schedule a meeting with my team next week and send invites"

NEXUS coordinates:
├── Calendar agent (finds available times)
├── Email agent (drafts invites)
├── Memory agent (recalls team preferences)
└── Returns: "Meeting scheduled for Tuesday 2pm. Invites sent to 4 people."
```

### The Agents

| Domain | What They Do |
|--------|-------------|
| Business | Finance, contracts, market analysis |
| Communications | Email, messaging, scheduling |
| Personal | Health, fitness, reminders |
| Research | Web search, document analysis |
| Creative | Writing, design, brainstorming |
| Technical | Code, debugging, architecture |

---

## Commands

```bash
hyperclaw init          # First-time setup (guided)
hyperclaw start         # Start the system
hyperclaw doctor        # Check if everything's working

hyperclaw swarm "..."   # Ask anything
hyperclaw agent         # List all agents
hyperclaw integrations  # Manage connections
```

---

## Dashboard

HyperClaw includes a web dashboard at `http://localhost:8000`:

- **Chat** — Talk to NEXUS
- **Integrations** — Connect your services with visual setup wizards
- **Agents** — See all 50+ specialists
- **Memory** — View what HyperClaw remembers
- **Settings** — Configure your system

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
- Run `hyperclaw doctor` to check system health
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
