#!/bin/bash
# HyperClaw offsite heartbeat — dead-man's switch.
# Pings HEARTBEAT_URL (healthchecks.io or similar) every run. If this Mac dies
# (power loss, crash), the pings stop and the monitoring service alerts you externally.
# Inert until HEARTBEAT_URL is set in ~/.hyperclaw/.env.
set -u
ENV_FILE="$HOME/.hyperclaw/.env"
[ -f "$ENV_FILE" ] && HEARTBEAT_URL=$(grep -E '^HEARTBEAT_URL=' "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '"')
[ -z "${HEARTBEAT_URL:-}" ] && exit 0

# Attach quick health context so the check's log shows WHAT was alive.
TG=$(pgrep -f telegram_direct.py >/dev/null && echo up || echo DOWN)
IM=$(pgrep -f imessage_daemon_v2.py >/dev/null && echo up || echo DOWN)
GD=$(pgrep -f "hyperclaw.daemon" >/dev/null && echo up || echo DOWN)
# launchd restart counts ("runs = N") make a crash-loop visible remotely.
RUNS_MAIN=$(launchctl print gui/$(id -u)/com.hyperclaw 2>/dev/null | awk '/runs =/ {print $3; exit}')
RUNS_IM=$(launchctl print gui/$(id -u)/com.hyperclaw.imessage 2>/dev/null | awk '/runs =/ {print $3; exit}')
curl -fsS -m 15 --retry 3 --data-raw "telegram=$TG imessage=$IM daemon=$GD runs_main=${RUNS_MAIN:-?} runs_imessage=${RUNS_IM:-?}" "$HEARTBEAT_URL" >/dev/null 2>&1
