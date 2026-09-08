#!/bin/sh
# Decode mounted JSON into the child environment without evaluating shell text.
exec python3 - "$@" <<'PY'
import json
import os
from pathlib import Path
import re
import sys

env = os.environ.copy()
mount = Path(env.get("SECRETS_MOUNT", "/mnt/secrets"))
if mount.is_dir():
    for path in sorted(mount.iterdir()):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for key, value in data.items():
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) and isinstance(value, str) and "\x00" not in value:
                env.setdefault(key, value)
        if path.name == "telegram":
            for source, target in {
                "BOT_TOKEN": "TELEGRAM_BOT_TOKEN",
                "ALLOWED_CHAT_IDS": "TELEGRAM_ALLOWED_CHAT_IDS",
                "WEBHOOK_SECRET": "TELEGRAM_WEBHOOK_SECRET",
            }.items():
                value = data.get(source)
                if isinstance(value, str) and "\x00" not in value:
                    env.setdefault(target, value)

if len(sys.argv) < 2:
    raise SystemExit("entrypoint requires a command")
os.execvpe(sys.argv[1], sys.argv[1:], env)
PY
