# Security

## Reporting a vulnerability

Open a GitHub issue with the label `security`, or email the maintainer privately for
sensitive reports. Do not include exploit details in public issues.

## Threat model: your assistant answers DMs

HyperClaw connects LLM tool execution (shell, files, email) to chat channels. The single
most important configuration decision is **who is allowed to talk to it**. A stranger who
can message your bot can potentially read your files and send email as you.

### What happens when a stranger messages the bot

- **Telegram**: messages from any chat id other than `TELEGRAM_CHAT_ID` are ignored.
  Keep the bot username unlisted and never post it publicly; Telegram bots are
  discoverable by name and WILL receive spam probes.
- **iMessage**: senders not in `IMESSAGE_ALLOWED_CONTACTS` are ignored. The allowlist
  is **empty by default = deny everyone**. Add only your own numbers/emails.

### Recommended settings

1. `TELEGRAM_CHAT_ID` — set to your own chat id only. Group chats are not recommended:
   anyone added to the group inherits full tool access.
2. `IMESSAGE_ALLOWED_CONTACTS` — your numbers only, in every format iMessage may
   report them (`+15551234567,5551234567`).
3. Run the gateway on a machine you control; do not expose the HTTP server (`PORT`)
   to the internet without authentication in front of it.
4. `HEARTBEAT_URL` is a secret — anyone holding it can fake your machine's liveness.
5. Rotate any credential that ever lands in a chat transcript or log file.

### Secrets hygiene

- All credentials live in `.env` (gitignored). `.env.example` documents every variable.
- Bot tokens appear in httpx request-URL log lines at INFO level; keep `logs/` private
  and rotate the token if logs are ever shared.
- The pre-push hook in this repo runs a secret scan before anything reaches a public
  remote. Keep it installed.
