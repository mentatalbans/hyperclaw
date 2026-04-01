# HyperClaw Hybrid Deployment Spec


## Architecture Overview

```
[Your Office]                          [Hetzner Cloud]
┌─────────────────────┐               ┌──────────────────────┐
│  Mac Mini M4 Pro    │◄──Tailscale──►│  Hetzner CX32        │
│                     │               │                      │
│  SOLOMON (brain)    │               │  Public API gateway  │
│  Civilization KB    │               │  Nginx reverse proxy │
│  All memory/data    │               │  Webhooks/integrations│
│  HyperClaw core     │               │  SSL termination     │
│  Background tasks    │               │  Rate limiting       │
│  Port: 8000 (local) │               │  Port: 443 (public)  │
└─────────────────────┘               └──────────────────────┘
         ▲                                      ▲
         │                                      │
    Tailscale VPN                         hyperclaw.ai
    (only you)                            (world-facing)
```

---

## PART 1: ON-PREM (Mac Mini M4 Pro)

### Hardware
- **Machine:** Mac Mini M4 Pro — 24GB RAM, 512GB SSD
- **Estimated cost:** ~$1,399 (Apple Store or B&H)
- **Why:** Fanless, silent, low power (~25W), arm64 native Python, already familiar stack

### What runs here
- SOLOMON (HyperClaw brain + orchestration)
- Civilization Knowledge Base (all company + personal data)
- Memory system (all memories)
- Background task runner
- PROMETHEUS ORACLE
- All sensitive integrations (Anthropic API, ElevenLabs, etc.)

### Network setup
- Static local IP on your office network (e.g. 192.168.1.100)
- Tailscale installed — gives it a private VPN IP (e.g. 100.x.x.x)
- No public ports open on this machine — ever
- You SSH in via Tailscale from anywhere: `ssh user@100.x.x.x`

### Security
- FileVault full disk encryption (on by default on Apple Silicon)
- SSH: key-only auth, password auth disabled
- Firewall: allow only Tailscale subnet + local LAN
- Auto-updates: macOS security patches only
- Backups: Time Machine to external drive + encrypted offsite (Backblaze B2)

---

## PART 2: CLOUD (Hetzner CX32)

### Specs
- **Provider:** Hetzner Cloud (Ashburn US-East or Falkenstein EU)
- **Plan:** CX32 — 4 vCPU, 8GB RAM, 80GB SSD
- **Cost:** ~€13.90/mo (~$15/mo)
- **OS:** Ubuntu 24.04 LTS

### What runs here
- Nginx reverse proxy (SSL termination, rate limiting)
- HyperClaw public API gateway (FastAPI, port 8000 internally)
- Webhook receivers (Telegram, WhatsApp, Stripe, etc.)
- Zero sensitive data stored here — all routed through to on-prem via Tailscale

### Domain
- `hyperclaw.ai` A record → Hetzner IP
- `api.hyperclaw.ai` → API gateway
- SSL via Let's Encrypt (auto-renewing)

### Security
- UFW firewall: only ports 22 (Tailscale-only), 80, 443 open to world
- SSH: key-only, port changed from 22 to custom
- Fail2ban: auto-ban after 5 failed auth attempts
- Tailscale: cloud node joins your tailnet — private traffic to on-prem goes over VPN
- No database, no secrets stored on cloud box

---

## PART 3: TAILSCALE (THE GLUE)

Tailscale creates a private encrypted network between:
- Your Mac Mini (office)
- Hetzner cloud box
- Your MacBook (when you need to access)
- Your phone (optional — iOS app)

Traffic between cloud and on-prem is end-to-end encrypted, invisible to Hetzner.

**Setup:** Free plan covers this use case (3 devices, unlimited data)

---

## DEPLOYMENT SEQUENCE

### Phase 1 — Cloud Server (1 hour)
```bash
# Provision Hetzner CX32
# Run deploy script:
./deploy/deploy-hetzner.sh <SERVER_IP> ~/.ssh/id_ed25519 .env

# Script handles:
# - Docker install
# - HyperClaw API container
# - Nginx + SSL
# - Tailscale install + join tailnet
# - UFW firewall rules
# - Fail2ban
```

### Phase 2 — Mac Mini Setup (2 hours)
```bash
# On new Mac Mini:
# 1. Install Homebrew
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 2. Install Tailscale
brew install tailscale
sudo tailscale up

# 3. Clone HyperClaw
git clone https://github.com/mentatalbans/hyperclaw.git
cd hyperclaw

# 4. Set up Python env
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 5. Configure .env (copy from current machine)
# 6. Start SOLOMON
python3 server.py

# 7. Set up LaunchAgent for auto-start on boot
```

### Phase 3 — Connect & Test (30 min)
- Verify cloud → on-prem routing over Tailscale
- Test API endpoint: `https://hyperclaw.ai/health`
- Test SOLOMON chat: `https://hyperclaw.ai/chat`
- Verify no sensitive data stored on cloud box

---

## WHAT YOU NEED TO PURCHASE/DO

| Item | Action | Cost |
|------|---------|------|
| Mac Mini M4 Pro | Buy at apple.com or B&H | ~$1,399 |
| Hetzner account | Sign up at hetzner.com | Free |
| Hetzner CX32 | Provision in dashboard | ~$15/mo |
| Tailscale account | Sign up at tailscale.com | Free |
| hyperclaw.ai DNS | Update A record (already owned) | $0 |

**Total upfront:** ~$1,399
**Monthly recurring:** ~$15

---

## OPERATOR ROLE

Once you order the Mac Mini and provision the Hetzner server:
1. I write all deploy scripts, configs, and LaunchAgents
2. I walk you through each step (30-min setup sessions)
3. I configure the full security hardening
5. Ongoing: I maintain both boxes — updates, monitoring, backups

---
