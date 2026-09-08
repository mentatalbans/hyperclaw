"""Explicit local-model profile, separate from credentials and cloud settings."""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse

import httpx

DEFAULT_MODEL = "qwen3.8:27b-mlx"
DEFAULT_URL = "http://127.0.0.1:11434"


def profile_path():
    root = Path(os.environ.get("HYPERCLAW_ROOT", Path.home() / ".hyperclaw"))
    return root / "config" / "local.json"


def load_profile():
    """Environment overrides win over a saved local profile."""
    path = profile_path()
    if path.exists():
        profile = json.loads(path.read_text())
        for key in ("HYPERCLAW_PROVIDER", "OLLAMA_MODEL", "OLLAMA_BASE_URL", "OLLAMA_THINK",
                    "HYPERCLAW_ENABLE_TOOLS", "HYPERCLAW_ENABLE_TELEGRAM", "HYPERCLAW_ENABLE_SCHEDULER",
                    "HYPERCLAW_ENABLE_DATABASE"):
            if key in profile:
                os.environ.setdefault(key, str(profile[key]))


def configure(model=DEFAULT_MODEL, base_url=DEFAULT_URL, probe=True):
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Ollama URL must be an HTTP(S) endpoint without embedded credentials")
    if not model.strip():
        raise ValueError("An Ollama model name is required")
    base_url = base_url.rstrip("/")
    if probe:
        response = httpx.get(base_url + "/api/tags", timeout=5)
        response.raise_for_status()
        available = {m["name"] for m in response.json().get("models", [])}
        if model not in available:
            raise ValueError(f"Model {model!r} is not installed on this Ollama server. Run: ollama pull {model}")
    profile = {
        "HYPERCLAW_PROVIDER": "ollama", "OLLAMA_MODEL": model,
        "OLLAMA_BASE_URL": base_url, "OLLAMA_THINK": "0",
        "HYPERCLAW_ENABLE_TOOLS": "1", "HYPERCLAW_ENABLE_TELEGRAM": "0",
        "HYPERCLAW_ENABLE_SCHEDULER": "0",
        "HYPERCLAW_ENABLE_DATABASE": "0",
    }
    path = profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(profile, indent=2) + "\n")
    temporary.replace(path)
    os.environ.update(profile)
    from .providers import reset_registry
    reset_registry()
    return path
