"""Import smoke tests for the 2026-08-20 upgrade surface.

Each module is imported in a subprocess so one bad module can't poison the test
process, and side effects stay contained.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

MODULES = [
    "hyperclaw.model_selector",
    "hyperclaw.outbox",
    "hyperclaw.media_hub",
]


@pytest.mark.parametrize("module", MODULES)
def test_module_imports(module):
    res = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True, text=True, cwd=ROOT, timeout=60,
    )
    assert res.returncode == 0, f"import {module} failed:\n{res.stderr[-2000:]}"


def test_scripts_compile():
    for script in ["scripts/telegram_direct.py", "scripts/telegram_supervisor.py",
                   "daemons/imessage_daemon_v2.py"]:
        res = subprocess.run(
            [sys.executable, "-m", "py_compile", str(ROOT / script)],
            capture_output=True, text=True, timeout=60,
        )
        assert res.returncode == 0, f"{script} does not compile:\n{res.stderr[-1000:]}"
