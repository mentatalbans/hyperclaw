#!/usr/bin/env bash
# HyperClaw Installation Script
set -euo pipefail

echo "⚡ HyperClaw Installer v0.1.0-alpha"
echo "======================================"

# Check Python version
PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
REQUIRED="3.11"
if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"; then
    echo "✓ Python $PYTHON_VERSION (>= $REQUIRED required)"
else
    echo "✗ Python $PYTHON_VERSION detected. HyperClaw requires Python >= $REQUIRED"
    exit 1
fi

# Create virtual environment if not in one
if [ -z "${VIRTUAL_ENV:-}" ]; then
    echo "→ Creating virtual environment..."
    python3 -m venv .venv
    source .venv/bin/activate
    echo "✓ Virtual environment created at .venv/"
fi

# Install HyperClaw
echo "→ Installing HyperClaw..."
pip install -e ".[dev]" --quiet

echo "→ Initializing HyperClaw..."
hyperclaw init

echo ""
echo "✓ HyperClaw installed successfully!"
echo ""
echo "Next steps:"
echo "  hyperclaw start          # Boot the swarm"
echo "  hyperclaw state list     # List active states"
echo "  pytest tests/ -v         # Run test suite"
echo ""
echo "⚡ The AI that actually takes over."
