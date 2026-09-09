"""Selection and reporting must preserve failure while isolating caller state."""
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def miniature_suite():
    def create(directory, source):
        (directory / 'pytest.ini').write_text('[pytest]\nmarkers =\n    docker: live Docker required\n    ollama: live model required\n')
        (directory / 'conftest.py').write_text((ROOT / 'tests/conftest.py').read_text())
        (directory / 'test_contract.py').write_text(source)
    return create


@pytest.fixture
def pytest_process():
    def run(directory, *arguments):
        env = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL') if key in os.environ}
        env['HYPERCLAW_ROOT'] = str(directory / 'runtime')
        return subprocess.run([sys.executable, '-m', 'pytest', '-q', *arguments], cwd=directory,
                              env=env, text=True, capture_output=True, timeout=30)
    return run


def test_live_selection_is_explicit(pytest_process, miniature_suite, tmp_path):
    miniature_suite(tmp_path, '''
import pytest
def test_regular():
    assert True
@pytest.mark.ollama
def test_live():
    raise AssertionError('synthetic unavailable model')
''')
    default = pytest_process(tmp_path)
    assert default.returncode == 0, default.stdout + default.stderr
    assert '1 passed, 1 deselected' in default.stdout
    explicit = pytest_process(tmp_path, '--run-ollama')
    assert explicit.returncode == 1
    assert 'synthetic unavailable model' in explicit.stdout


def test_runner_preserves_user_root_and_records_real_failure(tmp_path):
    supplied = tmp_path / 'existing-root'
    supplied.mkdir()
    (supplied / 'keep.txt').write_bytes(b'original\x00personal-data')
    before = {p.name: p.read_bytes() for p in supplied.iterdir()}
    case = tmp_path / 'test_deliberate_failure.py'
    case.write_text('''
import os
from pathlib import Path
def test_isolated_write():
    root = Path(os.environ['HYPERCLAW_ROOT'])
    assert root.is_dir()
    assert root != Path(%r)
    assert Path(os.environ['TMPDIR']) == root
    for key in ('OPENAI_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'HTTP_PROXY', 'HTTPS_PROXY',
                'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy', 'OLLAMA_MODEL', 'PYTHONPATH'):
        assert key not in os.environ
    (root / 'test-only.txt').write_text('isolated')
def test_deliberate_failure():
    raise AssertionError('synthetic regression')
''' % str(supplied))
    env = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL') if key in os.environ}
    env.update({'HYPERCLAW_ROOT': str(supplied), 'OPENAI_API_KEY': 'synthetic-key',
                'ANTHROPIC_AUTH_TOKEN': 'synthetic-token', 'HTTP_PROXY': 'http://127.0.0.1:1',
                'https_proxy': 'http://127.0.0.1:1', 'OLLAMA_MODEL': 'not-a-real-model', 'PYTHONPATH': '/does-not-exist'})
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/test_battery.py'), 'quick', str(case),
                             '--report-dir', str(tmp_path / 'reports')], cwd=ROOT, env=env,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 1, result.stdout + result.stderr
    summaries = list((tmp_path / 'reports').glob('*/summary.json'))
    assert len(summaries) == 1
    summary = json.loads(summaries[0].read_text())
    assert summary['exit_code'] == 1
    assert summary['tests'] == {'total': 2, 'failures': 1, 'errors': 0, 'skipped': 0}
    assert 'synthetic regression' in (summaries[0].parent / 'pytest.log').read_text()
    assert len(ET.parse(summaries[0].parent / 'junit.xml').findall('.//failure')) == 1
    assert {p.name: p.read_bytes() for p in supplied.iterdir()} == before


def test_battery_modes_select_external_services_explicitly(tmp_path, miniature_suite):
    suite = tmp_path / 'suite'
    suite.mkdir()
    miniature_suite(suite, '''
import pytest
def test_regular():
    assert True
@pytest.mark.docker
def test_docker():
    assert True
@pytest.mark.ollama
def test_ollama():
    assert True
''')
    summaries = {}
    for mode in ('quick', 'docker', 'live', 'all'):
        reports = tmp_path / f'reports-{mode}'
        result = subprocess.run([
            sys.executable, str(ROOT / 'scripts/test_battery.py'), mode, str(suite / 'test_contract.py'),
            '--report-dir', str(reports),
        ], cwd=ROOT, text=True, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        summary_path, = reports.glob('*/summary.json')
        summaries[mode] = json.loads(summary_path.read_text())

    assert summaries['quick']['tests']['total'] == 1
    assert summaries['docker']['tests']['total'] == 1
    assert summaries['live']['tests']['total'] == 1
    assert summaries['all']['tests']['total'] == 3
    assert summaries['quick']['command'][-2:] == ['-m', 'not ollama and not docker']
    assert '--run-docker' in summaries['docker']['command']
    assert summaries['docker']['command'][-2:] == ['-m', 'docker']
    assert '--run-ollama' not in summaries['docker']['command']
    assert '--run-ollama' in summaries['live']['command']
    assert '--run-docker' not in summaries['live']['command']
    assert summaries['live']['command'][-2:] == ['-m', 'ollama']
    assert '--run-ollama' in summaries['all']['command']
    assert '--run-docker' in summaries['all']['command']
    assert summaries['all']['command'].count('-m') == 1  # Python's ``-m pytest`` only.
