#!/usr/bin/env python3
"""Verify public behavior from fresh base and MCP wheel installations."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib
import traceback
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SUFFIXES = {".py", ".html", ".js", ".css"}
BASE_MCP_TESTS = [
    "tests/integration/test_mcp.py::test_selected_mcp_reports_missing_extra_when_sdk_absent",
]
MCP_TESTS = [
    "tests/integration/test_mcp.py::test_authenticated_http_cli_admission_and_revocation",
    "tests/integration/test_mcp.py::test_selected_mcp_rejects_reference_traversal_before_peer_start",
    "tests/integration/test_mcp.py::test_revoke_readmit_blocks_checkpointed_approval",
    "tests/integration/test_mcp.py::test_admitted_mcp_still_requires_direct_run_not_schedule",
]
DOCKER_MCP_TESTS = [
    "tests/integration/test_mcp.py::test_http_selected_read_search_grouped_sources_and_no_escalation",
]
PUBLIC_TEST_FILES = [
    "tests/integration/test_skills.py",
    "tests/integration/test_web_api.py",
    "tests/integration/test_telegram.py",
]
MEMORY_TESTS = "scope_and_provenance or correction_and_forget or cli_selects"
CORE_COPIED_FILES = [
    "pytest.ini",
    "tests/__init__.py",
    "tests/conftest.py",
    "tests/integration/__init__.py",
    "tests/integration/test_cli_tools.py",
    "tests/integration/test_mcp.py",
    "tests/integration/test_memory.py",
    "tests/integration/test_recovery.py",
    "tests/integration/test_scheduling.py",
    "tests/integration/test_skills.py",
    "tests/integration/test_telegram.py",
    "tests/integration/test_web_api.py",
    "tests/support/__init__.py",
    "tests/support/images.py",
    "tests/support/process.py",
    "tests/support/provider.py",
    "tests/support/recovery_daemon.py",
    "tests/support/telegram_daemon.py",
    "tests/support/telegram_peer.py",
    "tests/support/tool_provider.py",
]
BROWSER_COPIED_FILES = [
    "tests/browser/test_web.py",
]
DOCKER_COPIED_FILES = [
    "tests/live/__init__.py",
    "tests/live/test_recovery_docker.py",
]
WALKTHROUGH_COPIED_FILES = [
    "tests/browser/test_walkthrough.py",
    "examples/skills/documentation-answer/SKILL.md",
    "examples/skills/documentation-answer/citation-rules.txt",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_hashes(root: Path, prefix: Path) -> dict[str, str]:
    return {
        str(path.relative_to(prefix)): sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix in PACKAGE_SUFFIXES
    }


def test_counts(path: Path) -> dict[str, int] | None:
    if not path.exists():
        return None
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    return {
        label: sum(int(suite.get(attribute, "0")) for suite in suites)
        for label, attribute in (
            ("total", "tests"),
            ("failures", "failures"),
            ("errors", "errors"),
            ("skipped", "skipped"),
        )
    }


def initial_report(*, variant: str, python: str, browser_channel: str | None,
                   mcp_docs_image: str | None, report_dir: Path) -> dict:
    selected = {variant} if variant != "both" else {"base", "mcp"}
    return {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "dirty_diff_sha256": hashlib.sha256(subprocess.check_output(
            ["git", "diff", "HEAD", "--"], cwd=ROOT
        )).hexdigest(),
        "lock_sha256": sha256(ROOT / "uv.lock"),
        "requested_python": python,
        "requested_variant": variant,
        "browser_channel": browser_channel,
        "mcp_docs_image": mcp_docs_image,
        "report_dir": str(report_dir.resolve()),
        "host": {
            "python": sys.version,
            "sqlite": sqlite3.sqlite_version,
            "platform": platform.platform(),
            "architecture": platform.machine(),
        },
        "hosted": (
            {
                "status": "pending",
                "run_id": os.environ.get("GITHUB_RUN_ID"),
                "source_sha": os.environ.get("GITHUB_SHA"),
                "architecture": platform.machine(),
                "artifact_name": os.environ.get("HYPERCLAW_REPORT_ARTIFACT"),
                "reports": str(report_dir.resolve()),
            }
            if os.environ.get("GITHUB_ACTIONS") == "true"
            else {"status": "not_run"}
        ),
        "gates": {
            "base": {"status": "pending" if "base" in selected else "not_run"},
            "mcp": {"status": "pending" if "mcp" in selected else "not_run"},
            "browser": {"status": "pending" if browser_channel else "not_run"},
            "docker_mcp": {
                "status": "pending" if mcp_docs_image and "mcp" in selected else "not_run"
            },
        },
        "commands": [],
        "environments": {},
    }


class Verification:
    def __init__(self, state: dict, report: Path, scratch: Path):
        self.state = state
        self.report = report
        self.scratch = scratch
        self.current_gate: str | None = None

    def save(self) -> None:
        (self.report / "summary.json").write_text(
            json.dumps(self.state, indent=2) + "\n", encoding="utf-8"
        )

    def run(self, command: list[object], *, cwd: Path = ROOT,
            env: dict[str, str] | None = None, timeout: int = 1200) -> str:
        rendered = [str(value) for value in command]
        index = len(self.state["commands"]) + 1
        log_path = self.report / f"command-{index:02d}.log"
        entry = {
            "command": rendered,
            "cwd": str(cwd),
            "log": str(log_path.relative_to(self.report)),
            "gate": self.current_gate,
        }
        started = time.monotonic()
        process = subprocess.Popen(
            rendered, cwd=cwd, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, start_new_session=True,
        )
        try:
            output, _ = process.communicate(timeout=timeout)
            log_path.write_text(output, encoding="utf-8")
            print(output, end="", flush=True)
            exit_code = process.returncode
        except BaseException:
            terminate_process_group(process)
            output, _ = process.communicate()
            log_path.write_text(output, encoding="utf-8")
            print(output, end="", flush=True)
            raise
        finally:
            entry["elapsed_seconds"] = round(time.monotonic() - started, 3)
            entry["exit_code"] = process.returncode
            self.state["commands"].append(entry)
            self.save()
        if exit_code:
            terminate_process_group(process)
            raise RuntimeError(f"Command failed with exit status {exit_code}: {rendered!r}")
        return output.strip()


def selected_harness_files(*, browser: bool = False, docker: bool = False,
                           walkthrough: bool = False) -> list[str]:
    files = CORE_COPIED_FILES.copy()
    if browser:
        files += BROWSER_COPIED_FILES
    if docker:
        files += DOCKER_COPIED_FILES
    if walkthrough:
        if not browser or not docker:
            raise ValueError("The walkthrough requires both browser and Docker fixtures")
        files += WALKTHROUGH_COPIED_FILES
    return files


def copy_harness(destination: Path, *, browser: bool = False, docker: bool = False,
                 walkthrough: bool = False) -> dict[str, str]:
    copied = {}
    for relative in selected_harness_files(
        browser=browser, docker=docker, walkthrough=walkthrough,
    ):
        source = ROOT / relative
        if not source.is_file():
            raise FileNotFoundError(f"Required wheel verification input is missing: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied[relative] = sha256(source)
    return copied


def terminate_process_group(process: subprocess.Popen) -> None:
    """Stop descendants in the invocation-owned process group after a failure."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def remove_scratch(path: Path) -> None:
    """Remove only the owned scratch tree, including immutable admitted snapshots."""
    if not path.exists():
        return
    for directory, _, _ in os.walk(path):
        Path(directory).chmod(0o700)
    shutil.rmtree(path)


def clean_environment(venv: Path, temporary: Path) -> dict[str, str]:
    environment = {
        key: os.environ[key]
        for key in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT")
        if key in os.environ
    }
    environment.update({
        "PATH": str(venv / "bin") + os.pathsep + environment.get("PATH", ""),
        "PYTEST_ADDOPTS": "",
        "TMPDIR": str(temporary),
        "HYPERCLAW_ROOT": str(temporary / "unused-default-root"),
    })
    return environment


def pytest_command(python: Path, tests: list[str], junit: Path, *extra: str) -> list[str]:
    return [str(python), "-m", "pytest", *tests, "-q", "-ra", *extra,
            f"--junitxml={junit}"]


def verify_variant(verifier: Verification, *, variant: str, wheel: Path,
                   expected_hashes: dict[str, str], expected_version: str,
                   requested_python: str, browser_channel: str | None,
                   mcp_docs_image: str | None, uv: str) -> None:
    verifier.current_gate = variant
    directory = verifier.scratch / variant
    unrelated = directory / "unrelated"
    temporary = directory / "tmp"
    venv = directory / "venv"
    unrelated.mkdir(parents=True)
    temporary.mkdir()
    selected_docker = variant == "mcp" and bool(mcp_docs_image)
    selected_walkthrough = selected_docker and bool(browser_channel)
    copied = copy_harness(
        unrelated, browser=bool(browser_channel), docker=selected_docker,
        walkthrough=selected_walkthrough,
    )
    requirements = directory / "requirements.txt"
    extras = ["--extra", "dev"]
    if variant == "mcp":
        extras += ["--extra", "mcp"]
    if browser_channel:
        extras += ["--extra", "browser"]
    verifier.run([
        uv, "export", "--locked", *extras, "--no-emit-project",
        "--format", "requirements-txt", "--output-file", requirements,
    ])
    verifier.run([uv, "venv", "--python", requested_python, venv])
    python = venv / "bin" / "python"
    verifier.run([
        uv, "pip", "install", "--python", python, "--require-hashes",
        "--requirement", requirements,
    ])
    verifier.run([uv, "pip", "install", "--python", python, "--no-deps", wheel])
    environment = clean_environment(venv, temporary)
    probe = """
import hashlib, importlib.metadata, importlib.util, json, sys
from pathlib import Path
import hyperclaw, hyperclaw.memory, hyperclaw.skills
p = Path(hyperclaw.__file__).resolve().parent
files = {str(f.relative_to(p.parent)): hashlib.sha256(f.read_bytes()).hexdigest()
         for f in p.rglob('*') if f.is_file() and f.suffix in {'.py','.html','.js','.css'}}
print(json.dumps({'package': str(p), 'distribution_version': importlib.metadata.version('hyperclaw'),
                  'python': sys.version, 'mcp_installed': importlib.util.find_spec('mcp') is not None,
                  'source_sha256': files}))
"""
    info = json.loads(verifier.run([python, "-c", probe], cwd=unrelated, env=environment))
    if "site-packages" not in info["package"] or str(ROOT / "src") in info["package"]:
        raise AssertionError(f"Package did not import from isolated site-packages: {info['package']}")
    if info["mcp_installed"] != (variant == "mcp"):
        raise AssertionError(f"Unexpected MCP SDK state for {variant}: {info['mcp_installed']}")
    if info["distribution_version"] != expected_version:
        raise AssertionError((info["distribution_version"], expected_version))
    if info["source_sha256"] != expected_hashes:
        raise AssertionError("Installed Python/web package bytes differ from checkout and wheel")
    variant_report = {
        "installed_import": info,
        "cwd": str(unrelated),
        "python": str(python),
        "copied_test_inputs": copied,
        "requirements_sha256": sha256(requirements),
        "reports": {},
    }
    verifier.state["environments"][variant] = variant_report
    verifier.save()

    checks = [
        ("skills", PUBLIC_TEST_FILES[:1], []),
        ("memory", ["tests/integration/test_memory.py"], ["-k", MEMORY_TESTS]),
        ("m6-public", PUBLIC_TEST_FILES[1:], []),
    ]
    for label, tests, extra in checks:
        junit = verifier.report / f"{variant}-{label}.xml"
        verifier.run(pytest_command(python, tests, junit, *extra), cwd=unrelated, env=environment)
        variant_report["reports"][label] = {"path": str(junit), "tests": test_counts(junit)}
        verifier.save()

    mcp_tests = BASE_MCP_TESTS if variant == "base" else MCP_TESTS.copy()
    mcp_extra: list[str] = []
    if variant == "mcp" and mcp_docs_image:
        mcp_tests += DOCKER_MCP_TESTS
        mcp_extra = ["--run-docker", "--mcp-docs-image", mcp_docs_image]
        verifier.current_gate = "docker_mcp"
    junit = verifier.report / f"{variant}-mcp-public.xml"
    verifier.run(pytest_command(python, mcp_tests, junit, *mcp_extra), cwd=unrelated, env=environment)
    variant_report["reports"]["mcp-public"] = {"path": str(junit), "tests": test_counts(junit)}
    if variant == "mcp" and mcp_docs_image:
        verifier.state["gates"]["docker_mcp"] = {
            "status": "passed", "tests": test_counts(junit), "image": mcp_docs_image,
        }
    verifier.current_gate = variant
    verifier.save()

    verifier.run([venv / "bin" / "hyperclaw", "--help"], cwd=unrelated, env=environment)
    verifier.run([venv / "bin" / "hyperclaw", "telegram", "--help"], cwd=unrelated, env=environment)
    generated = unrelated / "test-results"
    if generated.exists():
        shutil.copytree(generated, verifier.report / f"{variant}-test-evidence", dirs_exist_ok=True)
    verifier.state["gates"][variant] = {
        "status": "passed",
        "tests": {
            label: details["tests"] for label, details in variant_report["reports"].items()
        },
    }
    verifier.save()

    if browser_channel:
        verifier.current_gate = "browser"
        junit = verifier.report / f"{variant}-browser.xml"
        browser_extra = [
            "--run-browser", "--browser-channel", browser_channel,
            "--browser-screenshot-dir", str(verifier.report / f"{variant}-screenshots"),
        ]
        if variant == "mcp" and mcp_docs_image:
            browser_extra += ["--run-docker", "--mcp-docs-image", mcp_docs_image]
        verifier.run(
            pytest_command(python, ["tests/browser"], junit, *browser_extra),
            cwd=unrelated, env=environment,
        )
        variant_report["reports"]["browser"] = {"path": str(junit), "tests": test_counts(junit)}
        prior = verifier.state["gates"].get("browser", {})
        runs = prior.get("runs", [])
        runs.append({"variant": variant, "tests": test_counts(junit)})
        verifier.state["gates"]["browser"] = {"status": "passed", "runs": runs}
        verifier.save()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, help="Python interpreter/version passed to uv venv")
    parser.add_argument("--variant", required=True, choices=("base", "mcp", "both"))
    parser.add_argument("--report-dir", required=True, type=Path)
    parser.add_argument(
        "--browser-channel", default=None,
        help="Explicit installed Playwright browser channel; omission leaves browser checks not_run",
    )
    parser.add_argument(
        "--mcp-docs-image", default=None,
        help="Explicit immutable MCP docs image ID; omission leaves Docker/MCP checks not_run",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(argv)
    started = datetime.now(timezone.utc)
    report = arguments.report_dir.resolve() / f"wheel-{started:%Y%m%dT%H%M%S%fZ}"
    report.mkdir(parents=True, exist_ok=False)
    state = initial_report(
        variant=arguments.variant, python=arguments.python,
        browser_channel=arguments.browser_channel,
        mcp_docs_image=arguments.mcp_docs_image, report_dir=report,
    )
    selected_mcp = arguments.variant in {"mcp", "both"}
    harness_inputs = selected_harness_files(
        browser=bool(arguments.browser_channel),
        docker=selected_mcp and bool(arguments.mcp_docs_image),
        walkthrough=(
            selected_mcp and bool(arguments.browser_channel) and bool(arguments.mcp_docs_image)
        ),
    )
    state["input_sha256"] = {
        path: sha256(ROOT / path)
        for path in ["pyproject.toml", "uv.lock", "scripts/verify_wheel.py", *harness_inputs]
        if (ROOT / path).is_file()
    }
    scratch = Path(tempfile.mkdtemp(prefix="hyperclaw-wheel-verify-")).resolve()
    state["scratch"] = str(scratch)
    verifier = Verification(state, report, scratch)
    verifier.save()
    exit_code = 0
    try:
        uv = shutil.which("uv")
        if not uv:
            raise RuntimeError("uv is required to build and install the locked wheel")
        if arguments.mcp_docs_image and arguments.variant in {"mcp", "both"}:
            if not arguments.mcp_docs_image.startswith("sha256:"):
                raise ValueError("--mcp-docs-image must be an inspected immutable sha256 image ID")
            verifier.current_gate = "docker_mcp"
            inspected = json.loads(verifier.run([
                "docker", "image", "inspect", arguments.mcp_docs_image, "--format", "{{json .}}",
            ]))
            if inspected.get("Id") != arguments.mcp_docs_image:
                raise AssertionError("Inspected MCP image ID differs from the selected immutable ID")
            state["docker_image"] = {
                "id": inspected["Id"], "architecture": inspected.get("Architecture"),
                "os": inspected.get("Os"),
            }
        verifier.current_gate = None
        verifier.run([uv, "build", "--out-dir", scratch / "dist"])
        wheels = list((scratch / "dist").glob("*.whl"))
        if len(wheels) != 1:
            raise AssertionError(f"Expected one wheel, found {wheels}")
        wheel = wheels[0]
        source_hashes = package_hashes(ROOT / "src" / "hyperclaw", ROOT / "src")
        with zipfile.ZipFile(wheel) as archive:
            packaged_hashes = {
                name: hashlib.sha256(archive.read(name)).hexdigest()
                for name in sorted(archive.namelist())
                if name.startswith("hyperclaw/") and Path(name).suffix in PACKAGE_SUFFIXES
            }
        if packaged_hashes != source_hashes:
            raise AssertionError("Wheel Python/web package bytes differ from checkout")
        state.update({
            "wheel": str(wheel), "wheel_sha256": sha256(wheel),
            "source_sha256": source_hashes, "source_files_matched": len(source_hashes),
        })
        verifier.save()
        expected_version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
        variants = [arguments.variant] if arguments.variant != "both" else ["base", "mcp"]
        for variant in variants:
            verify_variant(
                verifier, variant=variant, wheel=wheel, expected_hashes=source_hashes,
                expected_version=expected_version, requested_python=arguments.python,
                browser_channel=arguments.browser_channel,
                mcp_docs_image=arguments.mcp_docs_image, uv=uv,
            )
        state["status"] = "passed"
    except BaseException:
        exit_code = 1
        state["status"] = "failed"
        state["exception"] = traceback.format_exc()
        if verifier.current_gate:
            state["gates"][verifier.current_gate]["status"] = "failed"
            for gate in state["gates"].values():
                if gate["status"] == "pending":
                    gate["status"] = "not_run"
        else:
            for gate in state["gates"].values():
                if gate["status"] == "pending":
                    gate["status"] = "failed"
    finally:
        try:
            remove_scratch(scratch)
        except BaseException:
            exit_code = 1
            state["status"] = "failed"
            state["cleanup_exception"] = traceback.format_exc()
        state["scratch_cleaned"] = not scratch.exists()
        state["finished_at"] = datetime.now(timezone.utc).isoformat()
        if state["hosted"]["status"] == "pending":
            state["hosted"]["status"] = state.get("status", "failed")
        verifier.save()
        print(json.dumps({"report": str(report), "status": state.get("status")}), flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
