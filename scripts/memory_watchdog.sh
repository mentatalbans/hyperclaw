#!/bin/bash
# Memory Watchdog - Assistant HyperClaw
# Runs hourly - ensures memory server is alive, prunes old daily logs

MEMORY_DIR="~/.hyperclaw/memory"
LOG="/tmp/gil-memory-watchdog.log"
echo "[$(date)] Memory watchdog running" >> "$LOG"

# Ensure memory server is alive on 8765
if ! curl -s http://localhost:8765/ > /dev/null 2>&1; then
    echo "[$(date)] Memory server DOWN - restarting" >> "$LOG"
    launchctl start com.gil.memory-server
fi

# Prune daily logs older than 90 days
find "$MEMORY_DIR" -name "2026-*.md" -mtime +90 -delete 2>/dev/null
echo "[$(date)] Watchdog complete" >> "$LOG"
