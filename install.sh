#!/usr/bin/env bash
# HyperClaw Installation Script
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "⚡ HyperClaw Installer v0.1.0-alpha"
echo "======================================"

# Find Python 3.11+
PYTHON_BIN=""
for candidate in /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 python3.13 python3.12 python3.11; do
    if command -v "$candidate" &>/dev/null; then
        if "$candidate" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>/dev/null; then
            PYTHON_BIN="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "✗ Python 3.11+ not found. Install with: brew install python@3.13"
    exit 1
fi

PYTHON_VERSION=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "✓ Python $PYTHON_VERSION found at $PYTHON_BIN"

# Create virtual environment if not present (or if wrong Python version)
if [ ! -d ".venv" ] || ! .venv/bin/python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>/dev/null; then
    echo "→ Creating virtual environment with $PYTHON_BIN..."
    rm -rf .venv
    "$PYTHON_BIN" -m venv .venv
    echo "✓ Virtual environment created at .venv/"
fi

# Activate venv
source .venv/bin/activate
echo "✓ Virtual environment activated"

# Install dependencies
echo "→ Installing Python dependencies..."
pip install --quiet --upgrade pip
pip install --quiet \
    python-telegram-bot[job-queue] \
    apscheduler \
    rich \
    anthropic \
    python-dotenv \
    fastapi \
    uvicorn \
    httpx \
    psutil

echo "✓ Dependencies installed"

# Verify imports
echo "→ Verifying dependencies..."
python3 -c "import telegram; import apscheduler; import rich; import anthropic; import fastapi; print('✓ All imports OK')"

# Create logs directory
mkdir -p logs
echo "✓ Logs directory ready"

# Syntax check all modules
echo "→ Syntax checking modules..."
python3 -m py_compile hyperclaw/solomon.py hyperclaw/telegram_bot.py hyperclaw/scheduler.py hyperclaw/tui.py run_hyperclaw.py
echo "✓ All modules syntax OK"

# Install LaunchAgent
LAUNCH_AGENT_DIR="$HOME/Library/LaunchAgents"
PLIST_NAME="com.hyperclaw.plist"
PLIST_SOURCE="$SCRIPT_DIR/$PLIST_NAME"
PLIST_DEST="$LAUNCH_AGENT_DIR/$PLIST_NAME"

mkdir -p "$LAUNCH_AGENT_DIR"

# Unload existing if present
if launchctl list 2>/dev/null | grep -q "com.hyperclaw"; then
    echo "→ Unloading existing LaunchAgent..."
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
fi

# Copy plist
cp "$PLIST_SOURCE" "$PLIST_DEST"
echo "✓ LaunchAgent installed at $PLIST_DEST"

# Load the agent
echo "→ Loading LaunchAgent..."
launchctl load "$PLIST_DEST"
echo "✓ LaunchAgent loaded"

echo ""
echo "======================================"
echo "✓ HyperClaw installed and running!"
echo "======================================"
echo ""
echo "Services:"
echo "  • FastAPI server: http://127.0.0.1:8001"
echo "  • Telegram bot: polling"
echo "  • Scheduler: heartbeat (10min), morning brief (8AM), restart (4AM)"
echo ""
echo "Commands:"
echo "  python3 hyperclaw/tui.py      # Launch TUI chat"
echo "  launchctl stop com.hyperclaw  # Stop service"
echo "  launchctl start com.hyperclaw # Start service"
echo "  tail -f logs/hyperclaw.log    # View logs"
echo ""
echo "⚡ HyperClaw is live."
