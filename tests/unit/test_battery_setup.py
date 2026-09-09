"""The battery must select dependencies explicitly and preserve failing outcomes."""

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def miniature_suite(tmp_path, source):
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nmarkers =\n    ollama: requires live Ollama\n    postgres: requires PostgreSQL\n",
    )
    plugin = ROOT / "tests" / "conftest.py"
    (tmp_path / "conftest.py").write_text(plugin.read_text() if plugin.exists() else "")
    path = tmp_path / "test_contract.py"
    path.write_text(source)
    return path


def pytest_process(tmp_path, *arguments):
    environment = {**os.environ, "PYTHON_DOTENV_DISABLED": "1", "HYPERCLAW_ROOT": str(tmp_path / "runtime"),
                   "PYTHONPATH": str(ROOT), "PYTEST_ADDOPTS": "", "PYTEST_PLUGINS": ""}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *arguments], cwd=tmp_path,
        env=environment, text=True, capture_output=True, timeout=30,
    )


def test_live_checks_are_deselected_by_default_and_fail_when_explicitly_run(tmp_path):
    miniature_suite(tmp_path, """
import pytest
def test_regular():
    assert True
@pytest.mark.ollama
def test_live():
    raise AssertionError('synthetic unavailable model')
""")
    default = pytest_process(tmp_path)
    assert default.returncode == 0, default.stdout + default.stderr
    assert "1 passed, 1 deselected" in default.stdout
    explicit = pytest_process(tmp_path, "--run-ollama")
    assert explicit.returncode == 1, explicit.stdout + explicit.stderr
    assert "synthetic unavailable model" in explicit.stdout


def test_missing_postgres_is_an_error_when_the_battery_requires_it(tmp_path):
    miniature_suite(tmp_path, """
from tests.unit.test_state_persistence import temporary_postgres
def test_database(temporary_postgres):
    assert temporary_postgres
""")
    optional = pytest_process(tmp_path, "--postgres-bin", str(tmp_path / "absent"))
    assert optional.returncode == 0, optional.stdout + optional.stderr
    assert "1 skipped" in optional.stdout
    required = pytest_process(tmp_path, "--postgres-bin", str(tmp_path / "absent"), "--require-postgres")
    assert required.returncode == 1, required.stdout + required.stderr
    assert "PostgreSQL" in required.stdout and "1 error" in required.stdout


def test_quick_selection_excludes_transitive_database_fixtures(tmp_path):
    miniature_suite(tmp_path, """
import pytest
@pytest.fixture
def temporary_postgres():
    raise AssertionError('database fixture must not run')
@pytest.fixture
def database(temporary_postgres):
    return temporary_postgres
def test_database(database):
    assert database
def test_regular():
    assert True
""")
    result = pytest_process(tmp_path, "-m", "not postgres")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed, 1 deselected" in result.stdout


def test_runner_keeps_user_root_untouched_and_reports_real_test_failure(tmp_path):
    supplied_root = tmp_path / "existing-root"
    supplied_root.mkdir()
    sentinel = supplied_root / "keep.txt"
    sentinel.write_text("original")
    case = tmp_path / "test_deliberate_failure.py"
    case.write_text("""
import os
from pathlib import Path
def test_isolated_write():
    root = Path(os.environ['HYPERCLAW_ROOT'])
    assert root != Path(os.environ['SYNTHETIC_ORIGINAL_ROOT'])
    assert os.environ['PYTHON_DOTENV_DISABLED'] == '1'
    assert os.environ['HYPERCLAW_ENABLE_DATABASE'] == '0'
    assert os.environ['HYPERCLAW_ENABLE_TOOLS'] == '0'
    assert 'HYPERCLAW_TOOL_TIMEOUT' not in os.environ
    assert 'DAILY_BUDGET_USD' not in os.environ
    assert 'PERSONA_FILE' not in os.environ
    (root / 'test-only.txt').write_text('isolated')
def test_deliberate_failure():
    raise AssertionError('synthetic regression')
""")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "test_battery.py"), "quick", str(case),
         "--report-dir", str(tmp_path / "reports")], cwd=ROOT,
        env={**os.environ, "HYPERCLAW_ROOT": str(supplied_root),
             "HYPERCLAW_ENABLE_TOOLS": "1", "HYPERCLAW_TOOL_TIMEOUT": "not-a-timeout",
             "DAILY_BUDGET_USD": "not-a-budget",
             "PERSONA_FILE": str(sentinel),
             "SYNTHETIC_ORIGINAL_ROOT": str(supplied_root)},
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    summaries = list((tmp_path / "reports").glob("*/summary.json"))
    assert len(summaries) == 1
    summary = json.loads(summaries[0].read_text())
    assert summary["exit_code"] == 1
    assert summary["tests"] == {"total": 2, "failures": 1, "errors": 0, "skipped": 0}
    assert "synthetic regression" in (summaries[0].parent / "pytest.log").read_text()
    assert sentinel.read_text() == "original"
    assert list(supplied_root.iterdir()) == [sentinel]
