"""The module entrypoint must expose the installed CLI's local setup command."""

import json
import os
import subprocess
import sys
from pathlib import Path


def test_python_module_runs_canonical_local_setup(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "hyperclaw", "local", "--setup-only", "--no-probe"],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "HYPERCLAW_ROOT": str(tmp_path), "PYTHON_DOTENV_DISABLED": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    profile = json.loads((tmp_path / "config" / "local.json").read_text())
    assert profile["HYPERCLAW_PROVIDER"] == "ollama"
    assert profile["OLLAMA_MODEL"] == "qwen3.8:27b-mlx"
    assert profile["HYPERCLAW_ENABLE_TELEGRAM"] == "0"
    assert profile["HYPERCLAW_ENABLE_DATABASE"] == "0"
