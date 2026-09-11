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
        (directory / 'pytest.ini').write_text(
            '[pytest]\nmarkers =\n    browser: installed browser required\n'
            '    docker: live Docker required\n    ollama: live model required\n'
        )
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
@pytest.mark.browser
def test_browser():
    assert True
@pytest.mark.ollama
@pytest.mark.docker
def test_combined(request):
    assert request.config.getoption('--mcp-docs-image') == 'sha256:' + 'a' * 64
''')
    summaries = {}
    for mode in ('quick', 'browser', 'docker', 'live', 'combined', 'all'):
        reports = tmp_path / f'reports-{mode}'
        result = subprocess.run([
            sys.executable, str(ROOT / 'scripts/test_battery.py'), 'live' if mode == 'combined' else mode, str(suite / 'test_contract.py'),
            *(['--with-docker'] if mode == 'combined' else []),
            '--mcp-docs-image', 'sha256:' + 'a' * 64,
            '--report-dir', str(reports),
        ], cwd=ROOT, text=True, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        summary_path, = reports.glob('*/summary.json')
        summaries[mode] = json.loads(summary_path.read_text())

    assert summaries['quick']['tests']['total'] == 1
    assert summaries['browser']['tests']['total'] == 1
    assert summaries['docker']['tests']['total'] == 1
    assert summaries['live']['tests']['total'] == 1
    assert summaries['all']['tests']['total'] == 4
    assert summaries['combined']['tests']['total'] == 2
    assert '--run-docker' in summaries['combined']['command']
    assert summaries['combined']['command'][-2:] == ['-m', 'ollama']
    assert summaries['quick']['command'][-2:] == ['-m', 'not ollama and not docker']
    assert '--run-docker' in summaries['docker']['command']
    assert summaries['docker']['command'][-2:] == ['-m', 'docker']
    assert '--run-ollama' not in summaries['docker']['command']
    assert '--run-ollama' in summaries['live']['command']
    assert '--run-docker' not in summaries['live']['command']
    assert summaries['live']['command'][-2:] == ['-m', 'ollama']
    assert '--run-browser' in summaries['browser']['command']
    assert summaries['browser']['command'][-2:] == ['-m', 'browser']
    assert '--run-ollama' in summaries['all']['command']
    assert '--run-docker' in summaries['all']['command']
    assert '--run-browser' not in summaries['all']['command']
    assert summaries['all']['command'].count('-m') == 1  # Python's ``-m pytest`` only.


def test_explicitly_selected_unavailable_browser_fails(pytest_process, miniature_suite, tmp_path):
    miniature_suite(tmp_path, '''
import pytest
@pytest.mark.browser
def test_browser(browser_page):
    pass
''')
    package = tmp_path / 'playwright'
    package.mkdir()
    (package / '__init__.py').write_text('')
    (package / 'sync_api.py').write_text('''
class Error(Exception):
    pass
class Chromium:
    def launch(self, **options):
        raise Error('synthetic missing browser')
class Playwright:
    chromium = Chromium()
class Context:
    def __enter__(self):
        return Playwright()
    def __exit__(self, *args):
        pass
def sync_playwright():
    return Context()
''')

    result = pytest_process(tmp_path, '--run-browser', '--browser-channel', 'synthetic-missing')

    assert result.returncode == 1
    assert "Requested browser channel 'synthetic-missing' is unavailable" in result.stdout
    assert 'synthetic missing browser' in result.stdout


def test_wheel_report_lists_unselected_public_gates_as_not_run(tmp_path):
    from scripts.verify_wheel import initial_report

    report = initial_report(
        variant='base', python='3.11', browser_channel=None, mcp_docs_image=None,
        report_dir=tmp_path,
    )

    assert report['gates'] == {
        'base': {'status': 'pending'},
        'mcp': {'status': 'not_run'},
        'browser': {'status': 'not_run'},
        'docker_mcp': {'status': 'not_run'},
    }


def test_wheel_cleanup_removes_owned_read_only_runtime_snapshot(tmp_path):
    from scripts.verify_wheel import remove_scratch

    scratch = tmp_path / 'owned-scratch'
    outside = tmp_path / 'outside-scratch'
    outside.mkdir()
    outside_marker = outside / 'keep.txt'
    outside_marker.write_text('must remain')
    snapshot = scratch / 'runtime' / 'mcp-docs' / 'content-hash'
    snapshot.mkdir(parents=True)
    document = snapshot / 'guide.md'
    document.write_text('admitted immutable copy')
    (scratch / 'outside-link').symlink_to(outside, target_is_directory=True)
    document.chmod(0o444)
    snapshot.chmod(0o555)
    try:
        remove_scratch(scratch)
    finally:
        if snapshot.exists():
            snapshot.chmod(0o700)

    assert not scratch.exists()
    assert outside_marker.read_text() == 'must remain'


def test_wheel_harness_includes_lazy_dependencies_of_selected_public_tests(tmp_path):
    from scripts.verify_wheel import copy_harness

    harness = tmp_path / 'harness'
    harness.mkdir()
    copy_harness(harness)
    probe = subprocess.run(
        [sys.executable, '-c', 'from tests.integration.test_scheduling import schedule_body'],
        cwd=harness, text=True, capture_output=True,
    )

    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert not (harness / 'tests/browser').exists()
    assert not (harness / 'tests/live').exists()
    assert not (harness / 'examples').exists()
