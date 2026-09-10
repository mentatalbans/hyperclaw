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
    parser.add_argument("mode", choices=("quick", "browser", "docker", "live", "all"), nargs="?", default="quick")
    parser.add_argument("paths", nargs="*", help="Optional focused test paths, relative to the repository")
    parser.add_argument("--report-dir", type=Path, default=ROOT / "test-results")
    parser.add_argument("--coverage", action="store_true", help="Record coverage for src/hyperclaw")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--ollama-model", default="qwen3.8:27b-mlx")
    parser.add_argument("--mcp-docs-image", default="")
    parser.add_argument("--browser-channel", default="chromium")
    parser.add_argument("--with-docker", action="store_true", help="Explicitly include combined model and Docker cases in live mode")
    arguments = parser.parse_args()
    if arguments.with_docker and arguments.mode != "live":
        parser.error("--with-docker is supported only by live mode")
    started = datetime.now(timezone.utc)
    report = arguments.report_dir.resolve() / f"{arguments.mode}-{started:%Y%m%dT%H%M%S%fZ}"
    report.mkdir(parents=True)
    paths = arguments.paths or (["tests/live"] if arguments.mode == "live" else ["tests"])
    command = [sys.executable, "-m", "pytest", *paths, "-q", "-ra", "--durations=15",
               f"--junitxml={report / 'junit.xml'}"]
    if arguments.mcp_docs_image:
        command += ["--mcp-docs-image", arguments.mcp_docs_image]
    if arguments.with_docker:
        command += ["--run-docker"]
    if arguments.mode == "quick":
        command += ["-m", "not ollama and not docker"]
    if arguments.mode == "browser":
        command += ["--run-browser", "--browser-channel", arguments.browser_channel,
                    "--browser-screenshot-dir", str(report / "screenshots"), "-m", "browser"]
    if arguments.mode == "docker":
        command += ["--run-docker", "-m", "docker"]
    if arguments.mode in {"live", "all"}:
        command += ["--run-ollama", "--ollama-url", arguments.ollama_url,
                    "--ollama-model", arguments.ollama_model]
        if arguments.mode == "live":
            command += ["-m", "ollama"]
    if arguments.mode == "all":
        command += ["--run-docker"]
    if arguments.coverage:
        command += ["--cov=src/hyperclaw"]
        command += [f"--cov-report=xml:{report / 'coverage.xml'}",
                    f"--cov-report=json:{report / 'coverage.json'}",
                    f"--cov-report=html:{report / 'htmlcov'}", "--cov-report=term:skip-covered"]
    print(f"Running {arguments.mode} battery. Reports: {report}", flush=True)
    elapsed = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="hyperclaw-battery-") as runtime_root:
        environment = {key: os.environ[key] for key in (
            "PATH", "LANG", "LC_ALL", "SYSTEMROOT",
        ) if key in os.environ}
        environment.update({"HYPERCLAW_ROOT": runtime_root, "TMPDIR": runtime_root,
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
    coverage_path = report / "coverage.json"
    if coverage_path.exists():
        totals = json.loads(coverage_path.read_text())["totals"]
        summary["coverage"] = {
            "statements": {"covered": totals["covered_lines"], "total": totals["num_statements"]},
            "branches": {"covered": totals["covered_branches"], "total": totals["num_branches"]},
        }
    (report / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Battery exit status: {exit_code}. Summary: {report / 'summary.json'}", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
