#!/usr/bin/env python3
"""Run a selected test battery in temporary runtime storage, preserving reports."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


def test_counts(path: Path) -> dict | None:
    if not path.exists():
        return None
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    return {label: sum(int(suite.get(attribute, "0")) for suite in suites)
            for label, attribute in (("total", "tests"), ("failures", "failures"),
                                     ("errors", "errors"), ("skipped", "skipped"))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("quick", "full", "live", "all"), nargs="?", default="quick")
    parser.add_argument("paths", nargs="*", help="Optional focused test paths, relative to the repository")
    parser.add_argument("--report-dir", type=Path, default=ROOT / "test-results")
    parser.add_argument("--coverage", action="store_true", help="Record coverage for the five main packages")
    parser.add_argument("--postgres-bin", help="Directory containing initdb, pg_ctl, and postgres")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--ollama-model", default="qwen3.8:27b-mlx")
    arguments = parser.parse_args()
    started = datetime.now(timezone.utc)
    report = arguments.report_dir.resolve() / f"{arguments.mode}-{started:%Y%m%dT%H%M%S%fZ}"
    report.mkdir(parents=True)
    paths = arguments.paths or (["tests/live"] if arguments.mode == "live" else ["tests"])
    command = [sys.executable, "-m", "pytest", *paths, "-q", "-ra", "--durations=15",
               f"--junitxml={report / 'junit.xml'}"]
    if arguments.mode == "quick":
        command += ["-m", "not postgres and not ollama"]
    elif arguments.mode == "full":
        command += ["-m", "not ollama", "--require-postgres"]
    if arguments.mode in {"live", "all"}:
        command += ["--run-ollama", "--ollama-url", arguments.ollama_url,
                    "--ollama-model", arguments.ollama_model]
        if arguments.mode == "all":
            command += ["--require-postgres"]
        else:
            command += ["-m", "ollama"]
    if arguments.postgres_bin:
        command += ["--postgres-bin", arguments.postgres_bin]
    if arguments.coverage:
        command += [f"--cov={package}" for package in ("hyperclaw", "core", "memory", "security", "models")]
        command += [f"--cov-report=xml:{report / 'coverage.xml'}",
                    f"--cov-report=json:{report / 'coverage.json'}",
                    f"--cov-report=html:{report / 'htmlcov'}", "--cov-report=term:skip-covered"]
    print(f"Running {arguments.mode} battery. Reports: {report}", flush=True)
    elapsed = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="hyperclaw-battery-") as runtime_root:
        environment = dict(os.environ)
        # Tests supply synthetic provider and integration configuration themselves.
        for key in tuple(environment):
            if key.startswith("HYPERCLAW_") or key.endswith(("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD")) or key in {
                "DATABASE_URL", "HYPERCLAW_PROVIDER", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN",
                "OPENAI_BASE_URL", "OLLAMA_BASE_URL", "OLLAMA_MODEL", "OLLAMA_THINK", "PYTEST_PLUGINS",
                "DAILY_BUDGET_USD", "PREFER_CHEAP_MODELS", "PYTHONPATH", "PERSONA_FILE",
                "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy",
            }:
                environment.pop(key, None)
        environment.update({"HYPERCLAW_ROOT": runtime_root,
                            "SECRETS_MOUNT": str(Path(runtime_root) / "secrets"),
                            "PYTHON_DOTENV_DISABLED": "1", "HYPERCLAW_ENABLE_DATABASE": "0",
                            "HYPERCLAW_ENABLE_TELEGRAM": "0", "HYPERCLAW_ENABLE_SCHEDULER": "0",
                            "HYPERCLAW_ENABLE_TOOLS": "0",
                            "COVERAGE_FILE": str(report / ".coverage"), "PYTEST_ADDOPTS": ""})
        with (report / "pytest.log").open("w", encoding="utf-8") as log:
            with subprocess.Popen(command, cwd=ROOT, env=environment, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True) as process:
                try:
                    for line in process.stdout:
                        print(line, end="", flush=True)
                        log.write(line)
                        log.flush()
                    exit_code = process.wait()
                except KeyboardInterrupt:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    exit_code = 130
    summary = {
        "mode": arguments.mode, "started_at": started.isoformat(),
        "elapsed_seconds": round(time.monotonic() - elapsed, 3),
        "exit_code": exit_code, "python": sys.version.split()[0],
        "command": command, "tests": test_counts(report / "junit.xml"),
    }
    (report / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Battery exit status: {exit_code}. Summary: {report / 'summary.json'}", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
