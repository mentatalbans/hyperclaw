#!/usr/bin/env python3
"""Opt-in, synthetic loopback runtime measurements. No external services."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fnmatch
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # Repository-only test tooling, never package import fallback.
from hyperclaw.contracts import Checkpoint, MemoryScope, Message, RunRequest, ScheduleRequest, ToolCall
from hyperclaw.memory import Memory
from hyperclaw.store import Store
from tests.integration.test_background import committed_prefix
from tests.integration.test_recovery import wait_for
from tests.integration.test_runtime_composition import (
    assert_serial, test_graceful_shutdown_settles_mixed_io_before_releasing_root,
    test_http_telegram_and_schedule_share_one_worker, test_mixed_intake_survives_process_death,
)
from tests.integration.test_scheduling import schedule_body
from tests.integration.test_telegram import process_stack
from tests.support.process import events, submit
from tests.support.provider import Reply
from tests.support.telegram_peer import update

PHASES = ('recovery', 'schedules', 'populated', 'sustained')
UTC = timezone.utc
COPY_EXCLUDES = (
    '.git', '.pytest_cache', '.superpowers', '.venv', '__pycache__', '*.egg-info',
    'dist', 'test-results', 'build', '.worktrees', '.coverage', '.coverage.*', '*.pyc',
)


def positive_duration(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError('duration must be positive finite seconds')
    return number


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_output(*arguments):
    # Explicit local metadata and no ambient GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE.
    return subprocess.check_output(['git', '--git-dir', str(ROOT / '.git'),
        '--work-tree', str(ROOT), *arguments], cwd=ROOT, stderr=subprocess.PIPE,
        env={key: value for key, value in os.environ.items() if not key.startswith('GIT_')})


def source_identity(*, excluded_paths=()):
    excluded_paths = tuple(path.resolve() for path in excluded_paths)
    def excluded(path):
        resolved = path.resolve()
        return any(resolved == item or item in resolved.parents for item in excluded_paths)
    # A copied source tree must never discover an unrelated checkout above ROOT.
    if not (ROOT / '.git').exists():
        git = {'status': 'unavailable', 'reason': 'root_has_no_git_metadata'}
    elif shutil.which('git') is None:
        git = {'status': 'unavailable', 'reason': 'git_executable_unavailable'}
    else:
        git = {'status': 'available'}
    if git['status'] == 'available':
        paths = git_output('ls-files', '-z', '--cached', '--others', '--exclude-standard').decode().split('\0')
        commit = git_output('rev-parse', 'HEAD').decode().strip()
        inventory = {'kind': 'git-index-and-unignored-files'}
    else:
        paths = []
        def walk_error(error):
            raise error  # An unreadable directory must not silently disappear from provenance.
        for directory, directories, filenames in os.walk(ROOT, onerror=walk_error):
            directories[:] = sorted(name for name in directories
                if not any(fnmatch.fnmatchcase(name, pattern) for pattern in COPY_EXCLUDES)
                and not excluded(Path(directory) / name))
            paths.extend(str((Path(directory) / name).relative_to(ROOT)) for name in filenames
                if not any(fnmatch.fnmatchcase(name, pattern) for pattern in COPY_EXCLUDES))
        commit = None
        inventory = {'kind': 'filesystem', 'excluded_patterns': list(COPY_EXCLUDES)}
    inventory['excluded_owned_output_paths'] = [str(path.relative_to(ROOT.resolve()))
        for path in excluded_paths if ROOT.resolve() in path.parents]
    files = {name: digest(ROOT / name) for name in sorted(set(paths))
             if name and (ROOT / name).is_file() and not excluded(ROOT / name)}
    return {'commit': commit, 'git': git, 'inventory': inventory, 'files': files,
            'sha256': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}


def environment_identity():
    data = {'python': sys.version, 'executable': sys.executable, 'sqlite': sqlite3.sqlite_version,
            'platform': platform.platform(), 'machine': platform.machine(),
            'distributions': sorted((d.metadata['Name'], d.version)
                                    for d in importlib.metadata.distributions()),
            'lock_sha256': digest(ROOT / 'uv.lock')}
    data['sha256'] = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    return data


def resources(pid):
    proc = Path('/proc') / str(pid)
    if proc.exists():
        status = dict(line.split(':', 1) for line in (proc / 'status').read_text().splitlines())
        return {'pid': pid, 'rss_bytes': int(status['VmRSS'].split()[0]) * 1024,
                'threads': int(status['Threads']), 'fds': len(list((proc / 'fd').iterdir())),
                'method': 'procfs'}
    rss = subprocess.check_output(['ps', '-o', 'rss=', '-p', str(pid)], text=True)
    threads = subprocess.check_output(['ps', '-M', '-p', str(pid)], text=True)
    fds = subprocess.check_output(['/usr/sbin/lsof', '-nP', '-a', '-p', str(pid), '-Ff'], text=True)
    return {'pid': pid, 'rss_bytes': int(rss.strip()) * 1024,
            'threads': len(threads.splitlines()) - 1,
            'fds': sum(line[1:].isdigit() for line in fds.splitlines() if line.startswith('f')),
            'method': 'ps rss KiB / ps -M / lsof numeric file descriptors'}


class Journal:
    def __init__(self, directory):
        self.directory = directory
        self.file = (directory / 'events.jsonl').open('w', buffering=1)
        self.sequence = 0
        self.processes = []

    def write(self, kind, **values):
        self.sequence += 1
        self.file.write(json.dumps({'seq': self.sequence, 'kind': kind,
                                   'monotonic': time.monotonic(), **values}) + '\n')


class Metrics:
    """Bounded live observation; exact full percentiles are computed from disk after stop."""
    def __init__(self, journal):
        self.journal = journal
        self.counts = Counter()

    def add(self, name, seconds):
        self.counts[name] += 1
        self.journal.write('latency', name=name, seconds=seconds)

    def summary(self):
        result = {}
        # One metric at a time, after owned work drains. No growing samples retained live.
        for name in self.counts:
            with (self.journal.directory / 'events.jsonl').open() as stream:
                values = sorted(row['seconds'] for line in stream
                                if (row := json.loads(line))['kind'] == 'latency' and row['name'] == name)
            result[name] = distribution(values)
        return result


def distribution(values):
    values = sorted(values)
    if not values:
        return {'count': 0, 'p50': None, 'p95': None, 'max': None}
    return {'count': len(values), 'p50': values[math.ceil(len(values) * .5) - 1],
            'p95': values[math.ceil(len(values) * .95) - 1], 'max': values[-1]}


class RequestCounter:
    """Replace only the peer's observer list; retain counts, never request bodies."""
    def __init__(self):
        self.counts = Counter()
        self.lock = threading.Lock()

    def append(self, item):
        with self.lock:
            self.counts[item[0]] += 1

    def count(self, method):
        with self.lock:
            return self.counts[method]


class ProviderCounter:
    def __init__(self):
        self.count = 0
        self.max_context_bytes = 0
        self.lock = threading.Lock()

    def put(self, payload):
        with self.lock:
            self.count += 1
            self.max_context_bytes = max(self.max_context_bytes,
                                         len(json.dumps(payload['messages']).encode()))


@contextmanager
def owned_stack(path, journal, *, measured=False, bounded=False):
    path.mkdir(parents=True)
    fixture = process_stack.__wrapped__(path)
    stack = next(fixture)
    app, telegram, provider = stack
    original_start = app.start
    def start():
        result = original_start()
        journal.processes.append(app.process)
        journal.write('process_started', pid=app.process.pid, root=str(app.root))
        return result
    app.start = start
    if measured:
        app.launcher = ROOT / 'tests/support/measurement_daemon.py'
    if bounded:
        app.logs = deque(maxlen=1000)
        app.diagnostics = lambda: ''.join(list(app.logs)[-100:])
        telegram.requests = RequestCounter()
        provider.requests = ProviderCounter()
    journal.write('root_open', root=str(app.root))
    try:
        yield stack
    finally:
        cleanup_errors = []
        try:
            next(fixture)
        except StopIteration:
            pass
        except BaseException as exc:
            cleanup_errors.append(exc)
            # A failed Process.stop acceptance assertion must not skip fixture peers.
            # Their cleanup cannot turn that failed shutdown into a passing result.
            for peer in (telegram, provider):
                try:
                    peer.close()
                except BaseException as peer_error:
                    cleanup_errors.append(peer_error)
            journal.write('cleanup_failure', errors=[str(error) for error in cleanup_errors])
        journal.write('root_closed', root=str(app.root), process_gone=app.process is None,
                      reader_alive=getattr(app, 'reader', None) is not None and app.reader.is_alive(),
                      peer_alive=telegram.thread.is_alive(), provider_alive=provider.thread.is_alive())
        assert app.process is None and not telegram.thread.is_alive() and not provider.thread.is_alive()
        archive = journal.directory / path.name
        archive.mkdir(exist_ok=True)
        (archive / 'daemon.log').write_text(''.join(app.logs))
        telemetry = app.root / 'measurement.jsonl'
        if telemetry.exists():
            shutil.copy2(telemetry, archive / telemetry.name)
        hashes = {str(p.relative_to(path)): digest(p) for p in path.rglob('*') if p.is_file()
                  and p.name not in {'token', 'telegram-token', 'daemon.json', 'config.toml'}}
        (archive / 'scratch-hashes.json').write_text(json.dumps(hashes, indent=2) + '\n')
        database = app.root / 'runtime.sqlite3'
        if database.exists():
            with sqlite3.connect(database) as db:
                assert db.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
                assert db.execute('PRAGMA foreign_key_check').fetchall() == []
                rows = {table: db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                        for table in ('sessions', 'runs', 'events', 'messages', 'memory_records',
                                      'schedule_occurrences', 'telegram_updates', 'telegram_deliveries')}
                pending = db.execute("SELECT count(*) FROM runs WHERE status IN ('queued','running','waiting_approval')").fetchone()[0]
                (archive / 'durable-counts.json').write_text(json.dumps(rows | {'pending': pending}) + '\n')
        if cleanup_errors:
            raise cleanup_errors[0]


def sample(app, journal, stage):
    result = {'stage': stage, 'daemon': resources(app.process.pid), 'harness': resources(os.getpid()),
              'database_bytes': sum(p.stat().st_size for p in app.root.glob('runtime.sqlite3*')),
              'observer_log_lines': len(app.logs)}
    journal.write('resources', **result)
    return result


def recovery(scratch, journal, seed, case_filter=None):
    cases = [('crash', boundary, attempt) for boundary in
             ('http', 'schedule', 'telegram-submit', 'telegram-bind') for attempt in (1, 2)]
    cases += [('graceful', None, None), ('release', None, None)]
    random.Random(seed).shuffle(cases)
    results = []
    omitted = []
    for index, (kind, boundary, attempt) in enumerate(cases):
        if case_filter and kind not in case_filter:
            omitted.append({'cycle': index + 1, 'mode': kind, 'boundary': boundary,
                            'attempt': attempt, 'status': 'not_run'})
            continue
        started = time.monotonic()
        journal.write('recovery_start', seed=seed, cycle=index + 1, boundary=boundary, attempt=attempt, mode=kind)
        with owned_stack(scratch / f'recovery-{seed}-{index + 1}', journal) as stack:
            if kind == 'crash':
                test_mixed_intake_survives_process_death(stack, boundary, attempt)
            elif kind == 'graceful':
                test_graceful_shutdown_settles_mixed_io_before_releasing_root(stack)
                stack[2].enqueue(Reply())  # Prepared photo resumes only after the original shutdown passes.
                stack[0].start()  # Reopen the graceful cycle and verify it remains healthy.
                assert stack[0].client.get('/healthz').status_code == 200
                def photo():
                    return next((row for row in stack[0].client.get('/v1/telegram').json()['updates']
                                 if row['update_id'] == 2 and row['run_id']), None)
                row = wait_for(photo, stack[0])
                assert events(stack[0], row['run_id'])[-1]['data']['status'] == 'succeeded'
                def photo_sent():
                    return next((delivery for delivery in stack[0].client.get('/v1/telegram').json()['deliveries']
                                 if delivery['update_id'] == 2 and delivery['status'] == 'sent'), None)
                wait_for(photo_sent, stack[0])
                assert stack[2].requests.qsize() == 3 and not stack[2].errors
                assert sum(method == 'sendMessage' for method, _ in stack[1].requests) == 2
            else:
                test_http_telegram_and_schedule_share_one_worker(stack, 'release')
                stack[0].restart()
                assert stack[0].client.get('/healthz').status_code == 200
        row = {'cycle': index + 1, 'mode': kind, 'boundary': boundary, 'attempt': attempt,
               'seconds': time.monotonic() - started, 'status': 'passed'}
        results.append(row)
        journal.write('recovery_complete', seed=seed, **row)
        print(f'recovery seed={seed} cycle={index + 1}/10 passed', flush=True)
    return {'status': 'passed', 'seed': seed, 'cycles': results, 'omitted_cycles': omitted, 'max_outstanding': 6,
            'fixed_deck': '8 Task 2 SIGKILL boundaries + graceful mixed IO shutdown/reopen + shared worker release/restart',
            'duplicate_effects': 0, 'latency_seconds': distribution([r['seconds'] for r in results])}


def telemetry_rows(path):
    if not path.exists():
        return []
    # Used only for bounded 20-tick experiments, never rescans the sustained log live.
    with path.open() as stream:
        return [json.loads(line) for line in stream if line.endswith('\n')]


async def seed_schedules(root, size, regime):
    store = await Store.open(root)
    try:
        sessions = [await store.create_session() for _ in range(min(size, 10))]
        expiry_session = await store.create_session()
        expired_run = await store.submit(RunRequest(session_id=expiry_session.id, generation=0,
            request_id='expiry', text='unapproved synthetic write', tools=('workspace_write',)))
        await store.next_run()
        await store.save_checkpoint(expired_run.id, Checkpoint(messages=[Message(role='user', content='write')]))
        invocation = await store.prepare_invocation(expired_run.id, ToolCall(id='write', name='workspace_write',
            arguments={'path': 'forbidden.txt', 'content': 'no'}), 'synthetic-policy', 'synthetic-workspace', 'write')
        approval = await store.require_approval(invocation.id)
        busy = await store.submit(RunRequest(session_id=sessions[0].id, generation=0,
            request_id='busy', text='hold the only worker', tools=()))
        now = datetime.now(UTC)
        for index in range(size):
            await store.create_schedule(ScheduleRequest(id=f'scale-{index}',
                session_id=sessions[index % len(sessions)].id, generation=0, input='synthetic schedule',
                next_due_at=now - timedelta(seconds=1) if regime == 'due' or (index == 0 and size > 1) else now + timedelta(days=1),
                interval_seconds=1, tools=()))
        # Test-only fixture timestamp, set before daemon ownership; real expiry transaction is observed.
        await store._call(lambda: store._db.execute('UPDATE approvals SET expires_at=? WHERE id=?',
            ((datetime.now(UTC) + timedelta(seconds=1.5)).isoformat(), approval.id)))
        return busy.id, expired_run.id, [s.id for s in sessions]
    finally:
        await store.close()


def schedules(scratch, journal, seed, sizes=(1, 10, 100, 1000), regimes=('future', 'due')):
    result = []
    for size in sizes:
        for regime in regimes:
            name = f'schedules-{size}-{regime}'
            with owned_stack(scratch / name, journal, measured=True, bounded=True) as (app, telegram, provider):
                busy, expiry, session_ids = asyncio.run(seed_schedules(app.root, size, regime))
                gate = threading.Event()
                provider.enqueue(Reply(gate=gate, disconnected=threading.Event()))
                app.start()
                committed_prefix(app, busy)
                baseline = sample(app, journal, name + '-busy')
                log = app.root / 'measurement.jsonl'
                wait_for(lambda: len([r for r in telemetry_rows(log) if r['kind'] == 'tick']) >= 2, app)
                warmup = len([r for r in telemetry_rows(log) if r['kind'] == 'tick'])
                health, control = [], []
                deadline = time.monotonic() + 5
                while len([r for r in telemetry_rows(log) if r['kind'] == 'tick']) < warmup + 20:
                    assert time.monotonic() < deadline, '20 schedule ticks did not settle in five seconds'
                    started = time.monotonic()
                    assert app.client.get('/healthz').status_code == 200
                    health.append(time.monotonic() - started)
                    started = time.monotonic()
                    response = app.client.post(f'/v1/sessions/{session_ids[0]}/reset', json={'generation': 0})
                    assert response.status_code == 409 and response.json()['error']['code'] == 'session_busy'
                    control.append(time.monotonic() - started)
                    time.sleep(.025)
                rows = telemetry_rows(log)
                ticks = [r for r in rows if r['kind'] == 'tick'][warmup:warmup + 20]
                assert len(ticks) == 20 and all(r['active'] == size for r in ticks)
                assert app.client.get(f'/v1/runs/{expiry}').json()['error']['code'] == 'approval_expired'
                assert app.client.get(f'/v1/runs/{expiry}/receipts').json() == []
                assert provider.requests.count == 1 and not provider.errors
                pending = max(sum(r['counts'].get(status, 0) for status in ('queued', 'running', 'waiting_approval')) for r in ticks)
                # Stop new occurrences before releasing; accepted queued work must then really drain.
                for index in range(size):
                    assert app.client.post(f'/v1/schedules/scale-{index}/pause').status_code == 200
                queued = sum(ticks[-1]['counts'].get(status, 0) for status in ('queued', 'running'))
                provider.enqueue(*(Reply() for _ in range(queued - 1)))
                gate.set()
                reservation_lateness, start_lateness = [], []
                for session_id in session_ids:
                    for run in app.client.get(f'/v1/sessions/{session_id}/runs', params={'limit': 100}).json():
                        replay = events(app, run['id'])
                        assert replay[-1]['data']['status'] == 'succeeded'
                        if 'nominal_due_at' in replay[0]['data']:
                            due = datetime.fromisoformat(replay[0]['data']['nominal_due_at'])
                            reservation_lateness.append((datetime.fromisoformat(replay[0]['at']) - due).total_seconds())
                            start_lateness.append((datetime.fromisoformat(next(e['at'] for e in replay if e['kind'] == 'run.started')) - due).total_seconds())
                assert not (app.root / 'workspace/forbidden.txt').exists()
                settled = sample(app, journal, name + '-drained')
                row = {'size': size, 'regime': regime, 'status': 'passed', 'session_ids': session_ids,
                    'mapping': 'schedule index modulo min(size,10); session zero held busy; recurring interval 1s',
                    'pattern': 'all currently due' if regime == 'due' else ('sole schedule +1 day' if size == 1 else 'index zero due; all others +1 day'),
                    'warmup_ticks': warmup, 'observed_ticks': len(ticks), 'ticks': ticks,
                    'tick_seconds': distribution([r['seconds'] for r in ticks]),
                    'tick_interval_lateness_seconds': distribution([r['interval_lateness_seconds'] for r in ticks]),
                    'nominal_due_to_queued_seconds': distribution(reservation_lateness),
                    'nominal_due_to_started_seconds': distribution(start_lateness),
                    'transaction_seconds': distribution([r['seconds'] for r in rows if r['kind'] == 'transaction'
                                                         and ticks[0]['at'] <= r['at'] <= ticks[-1]['at'] + ticks[-1]['seconds']]),
                    'health_seconds': distribution(health), 'control_seconds': distribution(control),
                    'max_outstanding': pending, 'queue_at_ticks': [r['counts'].get('queued', 0) for r in ticks],
                    'expiry': 'approval_expired; no receipt or effect', 'baseline': baseline, 'drained': settled}
            result.append(row)
            journal.write('schedule_experiment', **row)
            print(f'schedules size={size} regime={regime}: 20 ticks passed', flush=True)
    return {'status': 'passed', 'experiments': result,
            'omitted_experiments': [{'size': size, 'regime': regime, 'status': 'not_run'}
                for size in (1, 10, 100, 1000) for regime in ('future', 'due')
                if size not in sizes or regime not in regimes],
            'optimization': 'none; report measured operating envelope'}


async def populated_store(root, journal, seed):
    store = await Store.open(root)
    memory = Memory(store)
    try:
        started = time.monotonic()
        sessions = [await store.create_session() for _ in range(100)]
        expected_runs = {session.id: [] for session in sessions}
        for index in range(10000):
            session = sessions[index % 100]
            run = await store.submit(RunRequest(session_id=session.id, generation=0,
                request_id=f'synthetic-{seed}-{index}', text=f'Synthetic history {index}', tools=()))
            assert (await store.next_run()).id == run.id
            await store.finish(run.id, 'succeeded', output=f'Synthetic answer {index}')
            expected_runs[session.id].append(run.id)
            if index % 2000 == 1999:
                print(f'populated: {index + 1}/10000 completed runs', flush=True)
        history_seed_seconds = time.monotonic() - started
        clock = datetime.now(UTC)
        memory.clock = lambda: clock
        private = MemoryScope(workspace_id='synthetic-visible', session_id=sessions[0].id)
        shared = MemoryScope(workspace_id='synthetic-visible')
        other = MemoryScope(workspace_id='synthetic-visible', session_id=sessions[1].id)
        foreign = MemoryScope(workspace_id='synthetic-foreign', session_id=sessions[0].id)
        started = time.monotonic()
        # Ten records per independent query: two visible positives and eight exclusion traps.
        # Corrections add a second physical row, giving exactly 10000 stored facts total.
        queries = []
        for group in range(1000):
            query = f'scalefact{seed}x{group}'
            visible = await memory.remember(private, query + ' visible private')
            common = await memory.remember(shared, query + ' visible shared')
            await memory.remember(other, query + ' forbidden other session')
            await memory.remember(foreign, query + ' forbidden other workspace')
            await memory.remember(private, query + ' expired', valid_until=clock + timedelta(seconds=1))
            forgotten = await memory.remember(private, query + ' forgotten')
            await memory.forget(forgotten.id, private)
            obsolete = await memory.remember(private, query + ' obsolete')
            current = await memory.correct(obsolete.id, f'correctedfact{seed}x{group}', private)
            await memory.remember(other, query + ' forbidden exact duplicate')
            await memory.remember(foreign, query + ' forbidden ranked distraction')
            queries.append((query, visible.id, common.id, current.id))
        memory_seed_seconds = time.monotonic() - started
        clock += timedelta(seconds=2)
        assert await store._call(lambda: store._db.execute('SELECT count(*) FROM memory_records').fetchone()[0]) == 10000
        session_latencies, history_latencies, search_latencies = [], [], []
        seen_sessions, cursor = [], None
        while True:
            started = time.monotonic()
            page = await store.sessions(limit=17, before=cursor)
            session_latencies.append(time.monotonic() - started)
            if not page:
                break
            seen_sessions.extend(s.id for s in page)
            cursor = page[-1].id
        assert seen_sessions == [s.id for s in reversed(sessions)]
        seen_run_count = 0
        for session in sessions:
            seen, cursor = [], None
            while True:
                started = time.monotonic()
                page = await store.session_runs(session.id, generation=0, limit=17, before=cursor)
                history_latencies.append(time.monotonic() - started)
                if not page:
                    break
                assert all(r.status == 'succeeded' for r in page)
                seen.extend(r.id for r in page)
                cursor = page[-1].id
            assert seen == list(reversed(expected_runs[session.id]))
            assert len(set(seen)) == len(seen)
            seen_run_count += len(seen)
        random.Random(seed).shuffle(queries)
        for query, visible, common, current in queries:
            started = time.monotonic()
            found = await memory.search(private, query)
            search_latencies.append(time.monotonic() - started)
            assert {r.id for r in found} == {visible, common}
            assert [r.id for r in await memory.search(shared, query)] == [common]
            assert [r.id for r in await memory.search(private, query.replace('scalefact', 'correctedfact'))] == [current]
        assert seen_run_count == 10000
        journal.write('populated_ids', sessions=seen_sessions, run_ids=expected_runs,
                      query_cases=queries)
        return {'status': 'passed', 'sessions': len(seen_sessions), 'completed_runs': seen_run_count,
                'memory_facts': 10000, 'query_groups': 1000, 'scope_queries': 3000,
                'page_size': 17, 'lost_ids': 0, 'duplicated_ids': 0, 'forbidden_or_obsolete_hits': 0,
                'history_seed_seconds': history_seed_seconds, 'memory_seed_seconds': memory_seed_seconds,
                'sessions_page_seconds': distribution(session_latencies),
                'history_page_seconds': distribution(history_latencies),
                'visible_memory_search_seconds': distribution(search_latencies),
                'resources': resources(os.getpid()),
                'measurement_owner': 'isolated real Store worker in harness; no daemon in this phase'}
    finally:
        await store.close()


def populated(scratch, journal, seed):
    root = scratch / 'populated'
    root.mkdir()
    try:
        return asyncio.run(populated_store(root, journal, seed))
    finally:
        hashes = {str(p.relative_to(root)): digest(p) for p in root.rglob('*') if p.is_file()}
        journal.write('populated_closed', root=str(root), hashes=hashes)
        with sqlite3.connect(root / 'runtime.sqlite3') as db:
            assert db.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
            assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def sustained(scratch, journal, seed, duration):
    metrics = Metrics(journal)
    accepted = completed = conflicted = max_outstanding = 0
    sources = Counter()
    samples = []
    batches = 0
    with owned_stack(scratch / 'sustained', journal, measured=True, bounded=True) as (app, telegram, provider):
        app.start()
        http_session = app.client.post('/v1/sessions').json()
        schedule_session = app.client.post('/v1/sessions').json()
        telegram_session = None
        samples.append(sample(app, journal, 'baseline'))
        started = time.monotonic()
        deadline, next_sample = started + duration, started + 10
        reset_window = 0
        while time.monotonic() < deadline or batches == 0:
            batch_started = time.monotonic()
            batches += 1
            gate = threading.Event()
            provider.enqueue(Reply(gate=gate, disconnected=threading.Event()), Reply(), Reply())
            submitted = time.monotonic()
            active = submit(app, f'synthetic seed {seed} batch {batches}', http_session,
                            request_id=f'http-{seed}-{batches}', tools=[])
            accepted += 1
            sources['http'] += 1
            metrics.add('http_accept', time.monotonic() - submitted)
            prefix = committed_prefix(app, active['id'])
            metrics.add('stream_first_text', time.monotonic() - submitted)
            control_started = time.monotonic()
            conflict = app.client.post('/v1/runs', json=active['request'] | {'request_id': f'busy-{batches}'})
            assert conflict.status_code == 409 and conflict.json()['error']['code'] == 'session_busy'
            conflicted += 1
            metrics.add('conflicting_control', time.monotonic() - control_started)
            health_started = time.monotonic()
            assert app.client.get('/healthz').status_code == 200
            metrics.add('health', time.monotonic() - health_started)
            telegram.updates = [update(batches, text=f'synthetic Telegram seed {seed} batch {batches}')]
            telegram_at = time.monotonic()
            schedule_id = f'load-{seed}-{batches}'
            scheduled_at = time.monotonic()
            response = app.client.post('/v1/schedules', json=schedule_body(schedule_session, schedule_id,
                interval_seconds=None, due=datetime.now(UTC) - timedelta(seconds=1)))
            assert response.status_code == 200, response.text
            occurrence = wait_for(lambda: app.client.get(f'/v1/schedules/{schedule_id}/occurrences').json(), app)[0]
            def binding():
                status = app.client.get('/v1/telegram').json()
                assert status['error'] is None
                return next((r for r in status['updates'] if r['update_id'] == batches and r['run_id']), None)
            row = wait_for(binding, app)
            telegram_session = app.client.get(f"/v1/sessions/{row['session_id']}").json()
            accepted += 2
            sources.update(schedule=1, telegram=1)
            identifiers = [active['id'], occurrence['run_id'], row['run_id']]
            assert [app.client.get(f'/v1/runs/{rid}').json()['status'] for rid in identifiers] == ['running', 'queued', 'queued']
            max_outstanding = max(max_outstanding, 3)
            assert max_outstanding <= 20
            gate.set()
            replays = [events(app, rid) for rid in identifiers]
            assert_serial(replays)
            assert replays[0][:len(prefix)] == prefix
            assert all(replay[-1]['data']['status'] == 'succeeded' for replay in replays)
            nominal_due = datetime.fromisoformat(occurrence['nominal_due_at'])
            metrics.add('schedule_nominal_due_to_queued',
                        (datetime.fromisoformat(replays[1][0]['at']) - nominal_due).total_seconds())
            metrics.add('schedule_nominal_due_to_started', (datetime.fromisoformat(
                next(e['at'] for e in replays[1] if e['kind'] == 'run.started')) - nominal_due).total_seconds())
            metrics.add('http_completion', time.monotonic() - submitted)
            metrics.add('schedule_completion', time.monotonic() - scheduled_at)
            metrics.add('telegram_completion', time.monotonic() - telegram_at)
            def delivery():
                rows = app.client.get('/v1/telegram').json()['deliveries']
                return next((r for r in rows if r['update_id'] == batches and r['status'] == 'sent'), None)
            wait_for(delivery, app)
            assert telegram.requests.count('sendMessage') == batches
            assert provider.requests.count == batches * 3 and not provider.errors
            for rid in identifiers:
                run = app.client.get(f'/v1/runs/{rid}').json()
                replay = app.client.post('/v1/runs', json=run['request'])
                assert replay.status_code == (422 if run['request']['request_id'].startswith('schedule:') else 202)
                if replay.status_code == 202:
                    assert replay.json()['id'] == rid
                assert app.client.get(f'/v1/runs/{rid}/receipts').json() == []
            completed += 3
            journal.write('batch', batch=batches, accepted=accepted, completed=completed,
                outstanding=0, run_ids=identifiers, schedule_id=schedule_id,
                telegram_update_id=batches, session_generations={s['id']: s['generation']
                    for s in (http_session, schedule_session, telegram_session)},
                event_hashes=[hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest() for r in replays],
                duration_seconds=time.monotonic() - batch_started)
            telegram.updates = []
            provider.gates.clear()  # This batch's provider IO has finished.
            if time.monotonic() >= next_sample:
                samples.append(sample(app, journal, 'drained-window'))
                reset_window += 1
                resets = []
                for session in (http_session, schedule_session, telegram_session):
                    reset_started = time.monotonic()
                    response = app.client.post(f"/v1/sessions/{session['id']}/reset", json={'generation': session['generation']})
                    assert response.status_code == 200, response.text
                    resets.append(response.json())
                    metrics.add('reset_control', time.monotonic() - reset_started)
                http_session, schedule_session, telegram_session = resets
                journal.write('window_drained_reset', window=reset_window, sessions=resets,
                              accepted=accepted, completed=completed, pending=0)
                next_sample += 10
            # Target one batch per second; a sample boundary can shorten the interval.
            remaining = min(batch_started + 1, deadline, next_sample) - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
        elapsed = time.monotonic() - started
        samples.append(sample(app, journal, 'final-drained'))
        assert accepted == completed and not provider.errors
        assert list((app.root / 'workspace').iterdir()) == []
        assert app.client.get('/v1/workspace').json()['grants'] == []
        max_context = provider.requests.max_context_bytes
    # Percentiles are calculated only after peers/process stop; telemetry stays on disk live.
    tick_values, late_values, transaction_values = [], [], []
    with (journal.directory / 'sustained/measurement.jsonl').open() as stream:
        for line in stream:
            row = json.loads(line)
            if row['kind'] == 'tick':
                tick_values.append(row['seconds'])
                if row['interval_lateness_seconds'] is not None:
                    late_values.append(row['interval_lateness_seconds'])
            elif row['kind'] == 'transaction':
                transaction_values.append(row['seconds'])
    latencies = metrics.summary()
    latencies.update(schedule_tick=distribution(tick_values),
                     schedule_tick_interval_lateness=distribution(late_values),
                     transaction=distribution(transaction_values))
    durable = json.loads((journal.directory / 'sustained/durable-counts.json').read_text())
    assert durable['pending'] == 0 and durable['runs'] == accepted
    assert durable['schedule_occurrences'] == durable['telegram_updates'] == durable['telegram_deliveries'] == batches
    return {'status': 'passed', 'requested_duration_seconds': duration, 'actual_duration_seconds': elapsed,
            'accepted': accepted, 'completed': completed, 'conflicted': conflicted,
            'pending': 0, 'duplicate_effects': 0, 'by_source': dict(sources),
            'duplicate_effects_definition': 'extra Telegram sendMessage calls or deliveries, duplicated canonical run identity or schedule occurrence; each must have exact batch count',
            'file_effect_coverage': 'text-only sustained workload: no write/command tools dispatched; empty receipts/workspace and unchanged empty grants; duplicate-write recovery is exercised separately by Task 2 recovery cycles',
            'batches': batches, 'max_outstanding': max_outstanding, 'samples': samples,
            'durable_counts': durable,
            'cadence': 'one HTTP + one one-shot schedule + one Telegram update per batch; target approximately one batch per second; sample-boundary wakeups can shorten the interval between batches; drain each batch; no catch-up bursts',
            'session_policy': 'three fixed sessions (Telegram topic 0); generation resets after each drained 10s window; durable prior generations retained',
            'latency_seconds': latencies, 'provider_max_messages_json_bytes': max_context,
            'schedule_lateness_definitions': {
                'schedule_tick_interval_lateness': 'max(0, current tick start - previous tick end - 0.1s); interval jitter, not occurrence delay',
                'schedule_nominal_due_to_queued': 'durable run.queued timestamp minus occurrence nominal due; includes intentionally backdated 1s due',
                'schedule_nominal_due_to_started': 'durable run.started timestamp minus nominal due; includes held-worker queue wait'},
            'durable_growth': 'runs/events/messages, schedules/occurrences, Telegram journal and deliveries intentionally retained',
            'observer_policy': 'provider bodies discarded after size/count; Telegram method counters only; one update; zero retained completed gates; last 1000 daemon log lines; compact metrics/event log streamed to disk',
            'acceptance': 'correctness/drain/owned cleanup; no post-hoc latency or RSS threshold'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--duration-seconds', type=positive_duration, required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--report-dir', type=Path, required=True)
    parser.add_argument('--phase', choices=(*PHASES, 'all'), action='append',
                        help='repeat to select phases; default all; recovery uses this invocation seed')
    parser.add_argument('--recovery-case', choices=('crash', 'graceful', 'release'), action='append',
                        help='optional focused recovery modes; omitted fixed-deck cycles are not_run')
    parser.add_argument('--schedule-size', choices=(1, 10, 100, 1000), type=int, action='append')
    parser.add_argument('--schedule-regime', choices=('future', 'due'), action='append')
    args = parser.parse_args(argv)
    selected = PHASES if not args.phase or 'all' in args.phase else tuple(dict.fromkeys(args.phase))
    args.report_dir.mkdir(parents=True, exist_ok=True)
    directory = args.report_dir / ('measurement-' + datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ'))
    directory.mkdir()
    scratch = journal = None
    report = {'status': 'failed', 'command': [sys.executable, str(Path(__file__).resolve()),
              *(sys.argv[1:] if argv is None else argv)],
              'source': {'status': 'not_run'}, 'environment': {'status': 'not_run'}, 'seed': args.seed,
              'requested_duration_seconds': args.duration_seconds, 'selected_phases': selected,
              'scratch': None, 'phases': {p: {'status': 'not_run'} for p in PHASES},
              'cleanup': {'residual_owned_processes': [], 'scratch_removed': False,
                          'journal_closed': False, 'errors': []}}
    baseline_threads = {thread.ident for thread in threading.enumerate()}
    started = time.monotonic()
    stage = 'scratch'
    try:
        scratch = Path(tempfile.mkdtemp(prefix='hyperclaw-measure-'))
        report['scratch'] = str(scratch)
        stage = 'journal'
        journal = Journal(directory)
        stage = 'source'
        report['source'] = {'status': 'failed'}
        report['source'] = source_identity(excluded_paths=(directory,))
        stage = 'git_provenance'
        (directory / 'git-provenance.json').write_text(json.dumps(report['source']['git'], indent=2) + '\n')
        if report['source']['git']['status'] == 'available':
            (directory / 'dirty.diff').write_bytes(git_output('diff', 'HEAD', '--'))
        stage = 'environment'
        report['environment'] = {'status': 'failed'}
        report['environment'] = environment_identity()
        stage = 'workload'
        for phase in selected:
            report['phases'][phase] = {'status': 'failed'}
            journal.write('phase_start', phase=phase, seed=args.seed)
            if phase == 'sustained':
                value = sustained(scratch, journal, args.seed, args.duration_seconds)
            elif phase == 'recovery' and args.recovery_case:
                value = recovery(scratch, journal, args.seed, args.recovery_case)
            elif phase == 'schedules':
                value = schedules(scratch, journal, args.seed,
                    sizes=tuple(dict.fromkeys(args.schedule_size or (1, 10, 100, 1000))),
                    regimes=tuple(dict.fromkeys(args.schedule_regime or ('future', 'due'))))
            else:
                value = globals()[phase](scratch, journal, args.seed)
            report['phases'][phase] = value
            journal.write('phase_complete', phase=phase)
        stage = 'source_verification'
        assert source_identity(excluded_paths=(directory,)) == report['source'], 'Source files changed during the measurement'
        report['status'] = 'passed'
    except BaseException as exc:
        report['failure'] = {'stage': stage, 'type': type(exc).__name__, 'message': str(exc),
                             'traceback': traceback.format_exc()}
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            report['failure']['stderr'] = exc.stderr.decode(errors='replace')
        if journal is not None:
            try:
                journal.write('failure', **report['failure'])
            except OSError as log_error:
                report['cleanup']['errors'].append(str(log_error))
        traceback.print_exc()
    finally:
        report['duration_seconds'] = time.monotonic() - started
        report['cleanup']['residual_owned_threads'] = [t.name for t in threading.enumerate()
            if t.ident not in baseline_threads and t.is_alive()]
        processes = journal.processes if journal is not None else []
        report['cleanup']['owned_processes'] = [{'pid': p.pid, 'returncode': p.poll()} for p in processes]
        report['cleanup']['residual_owned_processes'] = [p.pid for p in processes if p.poll() is None]
        # Hash failures must not skip deleting scratch or closing an opened journal.
        try:
            if scratch is not None and journal is not None:
                journal.write('scratch_final_hashes', hashes={str(p.relative_to(scratch)): digest(p)
                    for p in scratch.rglob('*') if p.is_file() and p.name not in
                    {'token', 'telegram-token', 'daemon.json', 'config.toml'}})
        except OSError as exc:
            report['cleanup']['errors'].append(str(exc))
        finally:
            try:
                if scratch is not None:
                    shutil.rmtree(scratch)
                report['cleanup']['scratch_removed'] = scratch is None or not scratch.exists()
            except OSError as exc:
                report['cleanup']['errors'].append(str(exc))
            finally:
                if journal is not None:
                    try:
                        journal.file.close()
                    except OSError as exc:
                        report['cleanup']['errors'].append(str(exc))
                    report['cleanup']['journal_closed'] = journal.file.closed
                else:
                    report['cleanup']['journal_closed'] = True  # No journal was opened.
        if (report['cleanup']['residual_owned_threads'] or report['cleanup']['residual_owned_processes']
                or report['cleanup']['errors']):
            report['status'] = 'failed'
        (directory / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(str(directory / 'report.json'), flush=True)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
