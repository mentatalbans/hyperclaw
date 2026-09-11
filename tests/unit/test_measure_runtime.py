"""The opt-in runner must execute owned work, report it, and clean up."""
import json
import hashlib
import importlib.util
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import os
import shutil
import zlib

import pytest


ROOT = Path(__file__).resolve().parents[2]


def copied_measurement_tree(destination):
    # Match containers/verification/run.sh: source is copied without Git or build state.
    shutil.copytree(ROOT, destination, ignore=shutil.ignore_patterns(
        '.git', '.pytest_cache', '.superpowers', '.venv', '__pycache__',
        '*.egg-info', 'dist', 'test-results'))
    return destination


def unrelated_git_parent(root):
    """A valid unrelated parent checkout, without requiring Git in the test environment."""
    metadata = root / '.git'
    (metadata / 'refs/heads').mkdir(parents=True)
    (metadata / 'objects').mkdir()
    def obj(kind, content):
        raw = kind.encode() + b' ' + str(len(content)).encode() + b'\0' + content
        identifier = hashlib.sha1(raw).hexdigest()
        path = metadata / 'objects' / identifier[:2] / identifier[2:]
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(zlib.compress(raw))
        return identifier
    tree = obj('tree', b'')
    commit = obj('commit', (f'tree {tree}\nauthor Synthetic <synthetic@example.invalid> 1 +0000\n'
        'committer Synthetic <synthetic@example.invalid> 1 +0000\n\nUnrelated parent\n').encode())
    (metadata / 'HEAD').write_text('ref: refs/heads/synthetic\n')
    (metadata / 'refs/heads/synthetic').write_text(commit + '\n')
    return commit


def run_copied_measurement(copy, tmp_path):
    (tmp_path / 'copied-input-hashes.json').write_text(json.dumps({
        str(path.relative_to(copy)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in copy.rglob('*') if path.is_file()}, indent=2) + '\n')
    cwd = tmp_path / 'unrelated-cwd'
    cwd.mkdir()
    scratch = tmp_path / 'owned-tmp'
    scratch.mkdir()
    output = copy / 'measurement-output'  # Output inside the copy must not change its identity.
    command = [sys.executable, str(copy / 'scripts/measure_runtime.py'),
        '--duration-seconds', '.1', '--seed', '1', '--report-dir', str(output), '--phase', 'sustained']
    env = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT') if key in os.environ}
    env['TMPDIR'] = str(scratch)
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=35)
    (tmp_path / 'public-command.json').write_text(json.dumps({
        'command': command, 'cwd': str(cwd), 'tmpdir': str(scratch), 'returncode': result.returncode,
        'stdout': result.stdout, 'stderr': result.stderr}, indent=2) + '\n')
    return result, output, scratch


@pytest.mark.parametrize('ancestor', [False, True], ids=['no-git', 'unrelated-git-ancestor'])
def test_copied_tree_measures_without_borrowing_git_provenance(tmp_path, ancestor):
    parent = tmp_path / 'parent'
    parent.mkdir()
    if ancestor:
        unrelated_git_parent(parent)
    copy = copied_measurement_tree(parent / 'copied-source')
    result, output, scratch = run_copied_measurement(copy, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    report_path, = output.glob('measurement-*/report.json')
    report = json.loads(report_path.read_text())
    assert report['source']['commit'] is None
    assert report['source']['git']['status'] == 'unavailable'
    assert report['source']['git']['reason'] == 'root_has_no_git_metadata'
    assert report['source']['inventory']['kind'] == 'filesystem'
    inputs = json.loads((tmp_path / 'copied-input-hashes.json').read_text())
    assert all(inputs[name] == value for name, value in report['source']['files'].items())
    assert report['source']['files']['scripts/measure_runtime.py'] == hashlib.sha256(
        (copy / 'scripts/measure_runtime.py').read_bytes()).hexdigest()
    assert report['source']['files']['src/hyperclaw/runtime.py'] == hashlib.sha256(
        (copy / 'src/hyperclaw/runtime.py').read_bytes()).hexdigest()
    assert report['source']['files']['uv.lock'] == hashlib.sha256((copy / 'uv.lock').read_bytes()).hexdigest()
    assert not any('measurement-output' in name for name in report['source']['files'])
    assert not report_path.with_name('dirty.diff').exists()
    assert json.loads(report_path.with_name('git-provenance.json').read_text())['status'] == 'unavailable'
    assert report['phases']['sustained']['accepted'] == report['phases']['sustained']['completed'] == 3
    assert report['cleanup']['scratch_removed']
    assert report['cleanup']['journal_closed']
    assert report['cleanup']['residual_owned_processes'] == []
    assert list(scratch.iterdir()) == []


@pytest.mark.parametrize('stage,target', [('source', 'source_identity'), ('journal', 'Journal')])
def test_early_setup_error_is_reported_after_owned_cleanup(tmp_path, monkeypatch, stage, target):
    spec = importlib.util.spec_from_file_location('measure_runtime_early_setup_test', ROOT / 'scripts/measure_runtime.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    def fail(*args, **kwargs):
        raise OSError('synthetic setup IO failure')
    monkeypatch.setattr(runner, target, fail)
    assert runner.main(['--duration-seconds', '.1', '--seed', '1',
        '--report-dir', str(tmp_path), '--phase', 'sustained']) == 1
    report_path, = tmp_path.glob('measurement-*/report.json')
    report = json.loads(report_path.read_text())
    assert report['failure']['stage'] == stage
    assert report['failure']['type'] == 'OSError'
    assert report['failure']['message'] == 'synthetic setup IO failure'
    assert all(phase['status'] == 'not_run' for phase in report['phases'].values())
    assert report['cleanup']['scratch_removed'] and report['cleanup']['journal_closed']
    assert report['cleanup']['residual_owned_processes'] == []
    assert report['cleanup']['errors'] == []
    assert not Path(report['scratch']).exists()


def test_metadata_setup_failure_keeps_report_and_removes_owned_scratch(tmp_path):
    copy = copied_measurement_tree(tmp_path / 'copied-source')
    (copy / 'uv.lock').unlink()
    result, output, scratch = run_copied_measurement(copy, tmp_path)
    assert result.returncode == 1
    report_paths = list(output.glob('measurement-*/report.json'))
    assert len(report_paths) == 1, result.stdout + result.stderr
    report = json.loads(report_paths[0].read_text())
    assert report['status'] == 'failed' and report['failure']['stage'] == 'environment'
    assert report['failure']['type'] == 'FileNotFoundError' and 'uv.lock' in report['failure']['message']
    assert report['source']['files']['scripts/measure_runtime.py']
    assert all(phase['status'] == 'not_run' for phase in report['phases'].values())
    assert report['cleanup']['scratch_removed'] and report['cleanup']['journal_closed']
    assert report['cleanup']['residual_owned_processes'] == []
    assert list(scratch.iterdir()) == []


def test_short_measurement_records_real_work_and_cleans_owned_root(tmp_path):
    sentinel = tmp_path / 'keep.txt'
    sentinel.write_text('caller owned')
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/measure_runtime.py'),
        '--duration-seconds', '0.3', '--seed', '1', '--report-dir', str(tmp_path),
        '--phase', 'sustained'], cwd=ROOT, capture_output=True, text=True, timeout=35)
    assert result.returncode == 0, result.stdout + result.stderr
    reports = list(tmp_path.glob('measurement-*/report.json'))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report['status'] == 'passed'
    assert report['phases']['recovery']['status'] == 'not_run'
    assert report['phases']['schedules']['status'] == 'not_run'
    assert report['phases']['populated']['status'] == 'not_run'
    trial = report['phases']['sustained']
    assert trial['accepted'] == trial['completed'] >= 3
    assert trial['by_source']['http'] >= 1
    assert trial['by_source']['schedule'] >= 1
    assert trial['by_source']['telegram'] >= 1
    assert trial['max_outstanding'] <= 20
    assert trial['pending'] == trial['duplicate_effects'] == 0
    assert trial['latency_seconds']['stream_first_text']['count'] >= 1
    assert report['cleanup']['residual_owned_processes'] == []
    assert report['cleanup']['scratch_removed']
    assert not Path(report['scratch']).exists()
    assert report['source']['files']['uv.lock']
    assert sentinel.read_text() == 'caller owned'


@pytest.mark.parametrize('duration', ['0', '-1', 'nan', 'inf'])
def test_duration_requires_positive_finite_value(tmp_path, duration):
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/measure_runtime.py'),
        '--duration-seconds', duration, '--seed', '1', '--report-dir', str(tmp_path)],
        cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert 'positive finite' in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_first_failure_is_preserved_and_later_phases_do_not_run(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('measure_runtime_test', ROOT / 'scripts/measure_runtime.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    def failed_probe(scratch, journal, seed):
        (scratch / 'failure-evidence.txt').write_text('synthetic invariant violation')
        journal.write('observed_invariant', seed=seed, expected=1, actual=2)
        raise AssertionError('synthetic invariant violation')
    monkeypatch.setattr(runner, 'recovery', failed_probe)
    result = runner.main(['--duration-seconds', '.1', '--seed', '3',
        '--report-dir', str(tmp_path), '--phase', 'recovery', '--phase', 'sustained'])
    assert result == 1
    report_path, = tmp_path.glob('measurement-*/report.json')
    report = json.loads(report_path.read_text())
    assert report['status'] == report['phases']['recovery']['status'] == 'failed'
    assert report['phases']['sustained']['status'] == 'not_run'
    assert report['failure']['message'] == 'synthetic invariant violation'
    assert report['cleanup']['scratch_removed']
    evidence = [json.loads(line) for line in report_path.with_name('events.jsonl').read_text().splitlines()]
    assert any(row['kind'] == 'observed_invariant' and row['actual'] == 2 for row in evidence)
    assert any('failure-evidence.txt' in row.get('hashes', {}) for row in evidence)


def test_failed_daemon_cleanup_still_closes_owned_peers_and_keeps_failure(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('measure_runtime_cleanup_test', ROOT / 'scripts/measure_runtime.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    peers = []
    def failed_cleanup(scratch, journal, seed):
        with runner.owned_stack(scratch / 'cleanup-failure', journal) as (app, telegram, provider):
            peers.extend((telegram, provider))
            app.start()
            original_stop = app.stop
            def stop():
                original_stop()
                raise AssertionError('synthetic failed shutdown acceptance')
            app.stop = stop
    monkeypatch.setattr(runner, 'recovery', failed_cleanup)
    try:
        assert runner.main(['--duration-seconds', '.1', '--seed', '1',
            '--report-dir', str(tmp_path), '--phase', 'recovery']) == 1
        report_path, = tmp_path.glob('measurement-*/report.json')
        report = json.loads(report_path.read_text())
        assert report['failure']['message'] == 'synthetic failed shutdown acceptance'
        assert report['cleanup']['residual_owned_threads'] == []
        assert report['cleanup']['residual_owned_processes'] == []
        assert report_path.parent.joinpath('cleanup-failure/daemon.log').exists()
        assert all(not peer.thread.is_alive() for peer in peers)
    finally:
        for peer in peers:
            peer.close()


async def test_single_future_schedule_really_starts_in_the_future(tmp_path):
    spec = importlib.util.spec_from_file_location('measure_runtime_schedule_test', ROOT / 'scripts/measure_runtime.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    before = datetime.now(timezone.utc)
    await runner.seed_schedules(tmp_path, 1, 'future')
    store = await runner.Store.open(tmp_path)
    try:
        schedule = await store.get_schedule('scale-0')
        assert schedule.next_due_at > before
    finally:
        await store.close()


def test_graceful_recovery_finishes_prepared_photo_after_reopen(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('measure_runtime_recovery_test', ROOT / 'scripts/measure_runtime.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    original_stack = runner.owned_stack
    @contextmanager
    def checked_stack(path, journal, **kwargs):
        with original_stack(path, journal, **kwargs) as stack:
            yield stack
            app, telegram, provider = stack
            if (app.root / 'test-telegram-stage').read_text() == 'shutdown':
                def photo():
                    return next((row for row in app.client.get('/v1/telegram').json()['updates']
                        if row['update_id'] == 2 and row['run_id']), None)
                row = runner.wait_for(photo, app)
                assert runner.events(app, row['run_id'])[-1]['data']['status'] == 'succeeded'
                assert not provider.errors
    monkeypatch.setattr(runner, 'owned_stack', checked_stack)
    directory = tmp_path / 'report'
    directory.mkdir()
    journal = runner.Journal(directory)
    try:
        runner.recovery(tmp_path / 'scratch', journal, 1, case_filter=['graceful'])
    finally:
        journal.file.close()
