"""The opt-in runner must execute owned work, report it, and clean up."""
import json
import importlib.util
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]


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
