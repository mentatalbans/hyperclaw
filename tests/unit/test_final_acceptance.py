"""Offline pressure controls for selected wheel gates and walkthrough cleanup."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest


PASSED = '<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase name="executed"/></testsuite>'
EVIDENCE = {
    'passed': PASSED,
    'nonzero': PASSED,
    'skipped': '<testsuite tests="1" failures="0" errors="0" skipped="1"><testcase name="selected"><skipped/></testcase></testsuite>',
    'mixed-skip': '<testsuite tests="2" failures="0" errors="0" skipped="1"><testcase name="executed"/><testcase name="selected"><skipped/></testcase></testsuite>',
    'missing': None,
    'malformed': '<testsuite',
    'empty': '<testsuites/>',
    'counts-only': '<testsuite tests="1" failures="0" errors="0" skipped="0"/>',
    'failure': '<testsuite tests="1" failures="1" errors="0" skipped="0"><testcase name="selected"><failure/></testcase></testsuite>',
    'error': '<testsuite tests="1" failures="0" errors="1" skipped="0"><testcase name="selected"><error/></testcase></testsuite>',
}


@pytest.mark.parametrize('target', ['skills', 'memory', 'm6-public', 'mcp-public', 'browser', 'docker-mcp-public'])
@pytest.mark.parametrize('evidence_kind', EVIDENCE)
def test_wheel_runner_requires_executed_selected_junit(tmp_path, monkeypatch, target, evidence_kind):
    from scripts import verify_wheel as wheel

    variant = 'mcp' if target == 'docker-mcp-public' else 'base'
    label = 'mcp-public' if target == 'docker-mcp-public' else target
    image = 'sha256:' + 'a' * 64
    scratch = tmp_path / 'owned-wheel-scratch'
    scratch.mkdir()
    monkeypatch.setattr(wheel.tempfile, 'mkdtemp', lambda **kwargs: str(scratch))
    monkeypatch.setattr(wheel.shutil, 'which', lambda name: name)
    original_run = wheel.Verification.run
    selected_commands = []

    def synthetic_run(self, command, **kwargs):
        argv = list(map(str, command))
        if argv[:2] == ['uv', 'build']:
            dist = Path(argv[-1])
            dist.mkdir()
            with zipfile.ZipFile(dist / 'synthetic.whl', 'w') as archive:
                for name in wheel.package_hashes(wheel.ROOT / 'src/hyperclaw', wheel.ROOT / 'src'):
                    archive.write(wheel.ROOT / 'src' / name, name)
        elif argv[:2] == ['uv', 'export']:
            Path(argv[-1]).write_text('# Synthetic locked installation control\n')
        elif argv[:3] == ['docker', 'image', 'inspect']:
            return json.dumps({'Id': image, 'Architecture': 'synthetic', 'Os': 'synthetic'})
        elif len(argv) > 1 and argv[1] == '-c':
            return json.dumps({
                'package': str(scratch / variant / 'venv/lib/site-packages/hyperclaw'),
                'distribution_version': wheel.tomllib.loads((wheel.ROOT / 'pyproject.toml').read_text())['project']['version'],
                'mcp_installed': variant == 'mcp',
                'source_sha256': wheel.package_hashes(wheel.ROOT / 'src/hyperclaw', wheel.ROOT / 'src'),
            })
        elif argv[1:3] == ['-m', 'pytest']:
            selected_commands.append(argv)
            junit = Path(next(arg.split('=', 1)[1] for arg in argv if arg.startswith('--junitxml=')))
            contents = EVIDENCE[evidence_kind] if junit.name == f'{variant}-{label}.xml' else PASSED
            # Exercise the real command recorder and exit-zero acceptance path.
            script = 'from pathlib import Path; import sys; Path(sys.argv[1]).write_text(sys.argv[2])'
            synthetic = [sys.executable, '-c', script, str(junit), contents] if contents is not None else [sys.executable, '-c', 'pass']
            if evidence_kind == 'nonzero' and junit.name == f'{variant}-{label}.xml':
                synthetic[2] += '; sys.exit(7)'
            return original_run(self, synthetic, **kwargs)
        return ''

    monkeypatch.setattr(wheel.Verification, 'run', synthetic_run)
    arguments = ['--python', sys.executable, '--variant', variant, '--report-dir', str(tmp_path / 'reports')]
    if target == 'browser':
        arguments += ['--browser-channel', 'synthetic']
    if variant == 'mcp':
        arguments += ['--mcp-docs-image', image]
    code = wheel.main(arguments)
    report_path, = (tmp_path / 'reports').glob('wheel-*/summary.json')
    report = json.loads(report_path.read_text())
    accepted = evidence_kind == 'passed'
    assert code == (0 if accepted else 1), report
    assert report['status'] == ('passed' if accepted else 'failed')
    gate = 'docker_mcp' if variant == 'mcp' else 'browser' if target == 'browser' else variant
    assert report['gates'][gate]['status'] == report['status']
    details = report['environments'][variant]['reports'][label]
    assert details['status'] == report['status']
    assert Path(details['path']).exists() is (evidence_kind != 'missing')
    if evidence_kind not in {'missing', 'malformed'}:
        assert details['tests'] is not None
    assert report['commands'][-1]['exit_code'] == (7 if evidence_kind == 'nonzero' else 0)
    assert report['scratch_cleaned'] is True and not scratch.exists()
    assert report['input_sha256']['scripts/verify_wheel.py'] == wheel.sha256(wheel.ROOT / 'scripts/verify_wheel.py')
    assert not (Path(report['environments'][variant]['cwd']) / 'src').exists()
    if target == 'mcp-public':
        assert wheel.BASE_MCP_TESTS[0] in selected_commands[-1]
    if not accepted:
        assert details['exception'] and report['exception']


def exception_leaves(error):
    if isinstance(error, BaseExceptionGroup):
        return [leaf for child in error.exceptions for leaf in exception_leaves(child)]
    return [error]


@pytest.mark.parametrize('failing_actions', [('stop',), ('cleanup',), ('close',), ('observe',), ('stop', 'cleanup', 'close', 'observe'), ()])
def test_walkthrough_attempts_all_finalizers_and_preserves_body_failure(tmp_path, monkeypatch, failing_actions):
    from tests.browser import test_walkthrough as walkthrough

    calls = []
    original = AssertionError('original walkthrough failure')
    errors = {action: RuntimeError(f'injected {action} failure') for action in failing_actions}

    def action(name):
        calls.append(name)
        if name in errors:
            raise errors[name]
        return []

    def start():
        raise original

    app = SimpleNamespace(root=tmp_path / 'runtime', start=start, stop=lambda: action('stop'))
    peer = SimpleNamespace(url='http://synthetic.invalid', close=lambda: action('close'))
    monkeypatch.setattr(walkthrough, 'Process', lambda *args, **kwargs: app)
    monkeypatch.setattr(walkthrough, 'ProviderStub', lambda: peer)
    monkeypatch.setattr(walkthrough, 'configure', lambda *args, **kwargs: None)
    monkeypatch.setattr(walkthrough, 'cleanup_owned', lambda root: action('cleanup'))
    monkeypatch.setattr(walkthrough, 'owned_container_ids', lambda root: action('observe'))
    page = SimpleNamespace(on=lambda *args: None)
    request = SimpleNamespace(config=SimpleNamespace(getoption=lambda name: 'sha256:synthetic'))

    with pytest.raises(BaseException) as raised:
        walkthrough.test_one_root_walkthrough_survives_recovery_without_repeating_effects(tmp_path, page, request)

    assert calls == ['stop', 'cleanup', 'close', 'observe']
    leaves = exception_leaves(raised.value)
    assert leaves[0] is original
    assert leaves[1:] == list(errors.values())


@pytest.mark.parametrize('ancestor_git', [False, True], ids=['git-free', 'unrelated-ancestor-git'])
def test_wheel_report_from_git_free_source_is_honest(tmp_path, monkeypatch, ancestor_git):
    from scripts import verify_wheel as wheel
    from tests.unit.test_measure_runtime import unrelated_git_parent

    no_git = tmp_path / 'empty-path'
    no_git.mkdir()
    monkeypatch.setenv('PATH', str(no_git))
    assert shutil.which('git') is None
    if ancestor_git:
        unrelated_git_parent(tmp_path)
    source = tmp_path / 'copied-source'
    (source / 'scripts').mkdir(parents=True)
    shutil.copy2(wheel.ROOT / 'scripts/verify_wheel.py', source / 'scripts/verify_wheel.py')
    shutil.copy2(wheel.ROOT / 'uv.lock', source / 'uv.lock')
    probe = subprocess.run([sys.executable, '-c',
        'import json; from pathlib import Path; from scripts.verify_wheel import initial_report; '
        'print(json.dumps(initial_report(variant="base", python="3.13", browser_channel=None, '
        'mcp_docs_image=None, report_dir=Path("reports"))))'],
        cwd=source, capture_output=True, text=True,
        env=wheel.clean_environment(Path(sys.executable).parent.parent, tmp_path) | {'PATH': str(no_git)})
    assert probe.returncode == 0, probe.stderr
    report = json.loads(probe.stdout)
    assert report['source_commit'] is None
    assert report['dirty_diff_sha256'] is None
    assert report['source_git'] == {'status': 'unavailable', 'reason': 'root_has_no_git_metadata', 'root': str(source)}
    assert report['lock_sha256'] == wheel.sha256(source / 'uv.lock')


@pytest.mark.parametrize('failing_actions', [('stop',), ('cleanup',), ('close',), ('observe',), ('stop', 'cleanup', 'close', 'observe'), ()])
def test_walkthrough_finalizers_preserve_shutdown_failures_without_body_failure(monkeypatch, failing_actions):
    from tests.browser import test_walkthrough as walkthrough

    calls = []
    errors = {action: RuntimeError(f'injected {action} failure') for action in failing_actions}

    def action(name):
        calls.append(name)
        if name in errors:
            raise errors[name]
        return []

    app = SimpleNamespace(root=Path('synthetic-root'), stop=lambda: action('stop'))
    peer = SimpleNamespace(close=lambda: action('close'))
    monkeypatch.setattr(walkthrough, 'cleanup_owned', lambda root: action('cleanup'))
    monkeypatch.setattr(walkthrough, 'owned_container_ids', lambda root: action('observe'))
    caught = None
    try:
        with walkthrough.finalize_walkthrough(app, peer):
            pass
    except BaseException as exc:
        caught = exc
    assert calls == ['stop', 'cleanup', 'close', 'observe']
    assert (exception_leaves(caught) if caught else []) == list(errors.values())
