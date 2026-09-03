#!/bin/sh
# Load all JSON secrets from /mnt/secrets into env vars.
# Each file must be valid JSON with string values; keys become env var names.
SECRETS_DIR="/mnt/secrets"

if [ -d "$SECRETS_DIR" ]; then
    for f in "$SECRETS_DIR"/*; do
        [ -f "$f" ] || continue
        eval "$(python3 -c "
import json, sys, os
try:
    data = json.load(open('$f'))
    for k, v in data.items():
        if isinstance(v, str) and k == k.strip():
            # Only export if not already set
            if not os.environ.get(k):
                print(f'export {k}={repr(v)}')
except Exception:
    pass
")"
    done
fi

exec "$@"
