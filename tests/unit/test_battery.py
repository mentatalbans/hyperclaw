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
        (directory / 'pytest.ini').write_text('[pytest]\nmarkers =\n    ollama: live model required\n')
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
