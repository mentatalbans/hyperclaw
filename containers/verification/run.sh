#!/bin/sh
set -eu

: "${UV_PROJECT_ENVIRONMENT:?set UV_PROJECT_ENVIRONMENT to a disposable mounted path}"
: "${UV_CACHE_DIR:?set UV_CACHE_DIR to a disposable mounted path}"
: "${VERIFY_REPORT_DIR:?set VERIFY_REPORT_DIR to a disposable mounted path}"
: "${VERIFY_SOURCE_DIR:?set VERIFY_SOURCE_DIR to a disposable mounted path}"

shared_tmp=$TMPDIR
native_tmp=/tmp/hyperclaw-native-$$
mkdir -p "$native_tmp"
python -c 'import platform,sys; print(platform.platform()); print(sys.version)'
docker version --format '{{json .Server}}'
python - "$PWD" "$VERIFY_SOURCE_DIR" <<'PY'
from pathlib import Path
import shutil
import sys

source, destination = map(Path, sys.argv[1:])
shutil.copytree(source, destination, ignore=shutil.ignore_patterns(
    '.git', '.pytest_cache', '.superpowers', '.venv', '__pycache__',
    '*.egg-info', 'dist', 'test-results',
))
PY
cd "$VERIFY_SOURCE_DIR"
uv sync --locked --extra dev --python 3.13
if [ "${VERIFY_QUICK:-1}" = 1 ]; then
    TMPDIR=$native_tmp "$UV_PROJECT_ENVIRONMENT/bin/python" scripts/test_battery.py quick --report-dir "$VERIFY_REPORT_DIR"
fi
if [ "${VERIFY_DOCKER:-1}" = 1 ]; then
    TMPDIR=$shared_tmp "$UV_PROJECT_ENVIRONMENT/bin/python" scripts/test_battery.py docker --report-dir "$VERIFY_REPORT_DIR"
fi
