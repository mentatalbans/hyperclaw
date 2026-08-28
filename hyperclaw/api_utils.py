"""Helpers for reading Claude API responses and locating config files.

Adaptive-thinking models (the Claude 5 family, Opus/Sonnet 4.6 and later)
may return thinking blocks before — or instead of — text blocks, so
``response.content[0]`` is not guaranteed to be a TextBlock. Reading
``.content[0].text`` raises AttributeError on those responses. Always go
through :func:`extract_text` / :func:`extract_json` instead.
"""

import json
import os
import re
import shutil
from pathlib import Path


def extract_text(response, default: str = "") -> str:
    """Join all text blocks of a Messages API response, skipping thinking
    and tool_use blocks. Returns ``default`` when no text block exists."""
    blocks = getattr(response, "content", None) or []
    parts = [b.text for b in blocks if getattr(b, "type", "") == "text" and getattr(b, "text", "")]
    return "\n".join(parts).strip() or default


def extract_json(response):
    """extract_text + json.loads, tolerating ```json fences around the body."""
    text = extract_text(response)
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    return json.loads(text)


def find_config(name: str) -> Path:
    """Locate a HyperClaw config file (e.g. ``agents.yaml``).

    Order: the user's ``$HYPERCLAW_ROOT/config`` (seeded on first access),
    then a development checkout's ``config/`` directory, then the copy
    shipped inside the package. Always returns the user path so edits land
    in one place; callers should still check ``.exists()``.
    """
    root = Path(os.environ.get("HYPERCLAW_ROOT", Path.home() / ".hyperclaw"))
    user = root / "config" / name
    if user.exists():
        return user
    here = Path(__file__).resolve().parent
    for src in (here.parent / "config" / name, here / "default_config" / name):
        if src.exists():
            try:
                user.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(src, user)
                return user
            except OSError:
                return src
    return user
