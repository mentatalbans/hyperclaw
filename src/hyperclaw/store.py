"""Single-owner SQLite storage. Every state transition and its events commit together."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone, timedelta
import hashlib
import fcntl
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from pydantic import ValidationError

from hyperclaw.config import ensure_root
from hyperclaw.contracts import (
    Conflict, Failure, InvalidRequest, Message, NotFound, RootInUse, Run,
    RunEvent, RunRequest, RunStatus, Schedule, ScheduleOccurrence, ScheduleRequest,
    Session, StorageFailure, TERMINAL,
    UnsupportedSchema, canonical, Approval, Artifact, Checkpoint, Invocation, ToolCall, ToolReceipt,
    schedule_instant,
)

# Each migration is (destructive, statements), executed in a single transaction.
MIGRATIONS = ((False, (
    'CREATE TABLE schema_version (version INTEGER PRIMARY KEY)',
    'INSERT INTO schema_version VALUES (0)',
    'CREATE TABLE sessions (id TEXT PRIMARY KEY, generation INTEGER NOT NULL DEFAULT 0 CHECK(generation >= 0))',
    """CREATE TABLE runs (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
        generation INTEGER NOT NULL, request_id TEXT NOT NULL, payload_json TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled','interrupted')),
        output TEXT, error_json TEXT, created_at TEXT NOT NULL,
        UNIQUE(session_id, generation, request_id))""",
    "CREATE UNIQUE INDEX one_active_run_per_session ON runs(session_id, generation) WHERE status IN ('queued','running')",
    """CREATE TABLE messages (id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
        role TEXT NOT NULL CHECK(role IN ('user','assistant')), content TEXT NOT NULL, UNIQUE(run_id, role))""",
    """CREATE TABLE events (run_id TEXT NOT NULL REFERENCES runs(id), seq INTEGER NOT NULL CHECK(seq > 0),
        kind TEXT NOT NULL, at TEXT NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(run_id, seq))""",
)),)


# Rebuild the checked run table and its two dependent tables in one backed-up
# transaction. Copy children before dropping parents; foreign keys stay enabled.
MIGRATIONS += ((True, (
    'CREATE TEMP TABLE saved_messages AS SELECT * FROM messages',
    'CREATE TEMP TABLE saved_events AS SELECT * FROM events',
    'DROP TABLE messages', 'DROP TABLE events',
    'DROP INDEX one_active_run_per_session',
    'ALTER TABLE runs RENAME TO runs_v1',
    """CREATE TABLE runs (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
        generation INTEGER NOT NULL, request_id TEXT NOT NULL, payload_json TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('queued','running','waiting_approval','succeeded','failed','cancelled','interrupted','uncertain')),
        output TEXT, error_json TEXT, created_at TEXT NOT NULL,
        checkpoint_json TEXT, elapsed_s REAL NOT NULL DEFAULT 0, active_since TEXT,
        verification TEXT NOT NULL DEFAULT 'not_requested',
        UNIQUE(session_id,generation,request_id))""",
    'INSERT INTO runs(id,session_id,generation,request_id,payload_json,status,output,error_json,created_at) SELECT * FROM runs_v1',
    "UPDATE runs SET payload_json=json_set(payload_json,'$.tools',json('[]'),'$.images',json('[]'),'$.context_bytes',65536)",
    'DROP TABLE runs_v1',
    "CREATE UNIQUE INDEX one_active_run_per_session ON runs(session_id,generation) WHERE status IN ('queued','running','waiting_approval')",
    """CREATE TABLE messages (id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
        role TEXT NOT NULL CHECK(role IN ('user','assistant')), content TEXT NOT NULL, UNIQUE(run_id,role))""",
    """CREATE TABLE events (run_id TEXT NOT NULL REFERENCES runs(id), seq INTEGER NOT NULL CHECK(seq > 0),
        kind TEXT NOT NULL, at TEXT NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(run_id,seq))""",
    'INSERT INTO messages SELECT * FROM saved_messages', 'INSERT INTO events SELECT * FROM saved_events',
    'DROP TABLE saved_messages', 'DROP TABLE saved_events',
    'CREATE TABLE installation (id TEXT PRIMARY KEY)',
    "INSERT INTO installation VALUES (lower(hex(randomblob(16))))",
    'CREATE TABLE grants (workspace_id TEXT NOT NULL, capability TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(workspace_id,capability))',
    """CREATE TABLE invocations (id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
        call_id TEXT NOT NULL, call_json TEXT NOT NULL, arguments_sha256 TEXT NOT NULL,
        policy_sha256 TEXT NOT NULL, workspace_id TEXT NOT NULL, capability TEXT NOT NULL,
        status TEXT NOT NULL, approved INTEGER NOT NULL DEFAULT 0, container_id TEXT, receipt_json TEXT,
        UNIQUE(run_id,call_id))""",
    """CREATE TABLE approvals (id TEXT PRIMARY KEY, invocation_id TEXT NOT NULL UNIQUE REFERENCES invocations(id),
        expires_at TEXT NOT NULL, status TEXT NOT NULL)""",
    """CREATE TABLE artifacts (invocation_id TEXT NOT NULL REFERENCES invocations(id),
        path TEXT NOT NULL, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL,
        PRIMARY KEY(invocation_id,path))""",
)),)


MIGRATIONS += ((False, (
    """CREATE TABLE schedules (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
        generation INTEGER NOT NULL CHECK(generation >= 0), input TEXT NOT NULL,
        next_due_at TEXT NOT NULL, interval_seconds INTEGER,
        tools_json TEXT NOT NULL, creation_json TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('active','paused','completed')),
        pause_reason TEXT CHECK(pause_reason IN ('operator','stale_generation','uncertain_effect')),
        CHECK((status='paused') = (pause_reason IS NOT NULL)))""",
    """CREATE TABLE schedule_occurrences (
        schedule_id TEXT NOT NULL REFERENCES schedules(id), nominal_due_at TEXT NOT NULL,
        run_id TEXT NOT NULL UNIQUE REFERENCES runs(id), PRIMARY KEY(schedule_id,nominal_due_at))""",
    'CREATE INDEX due_schedules ON schedules(status,next_due_at)',
)),)


def now():
    return datetime.now(timezone.utc).isoformat()


def instant(value):
    return schedule_instant(value).isoformat(timespec='microseconds')


class Store:
    def __init__(self, root):
        self.root = root
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='hyperclaw-store')
        self._db = None
        self._lock = None
        self._closed = False
        self._closing = None

    @classmethod
    async def open(cls, root: Path):
        store = cls(Path(root))
        try:
            await store._call(store._open)
        except BaseException:
            await store.close()
            raise
        return store

    def _open(self):
        ensure_root(self.root)
        self._lock = os.open(self.root / 'owner.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self._lock)
            self._lock = None
            raise RootInUse() from None
        database = self.root / 'runtime.sqlite3'
        if database.is_symlink():
            raise StorageFailure()
        self._db = sqlite3.connect(database, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        for statement in ('PRAGMA foreign_keys=ON', 'PRAGMA journal_mode=DELETE',
                          'PRAGMA synchronous=FULL', 'PRAGMA busy_timeout=5000'):
            self._db.execute(statement)
        tables = self._db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if tables:
            try:
                versions = self._db.execute('SELECT version FROM schema_version').fetchall()
                if len(versions) != 1:
                    raise UnsupportedSchema()
                version = versions[0][0]
            except sqlite3.Error:
                raise UnsupportedSchema() from None
            if type(version) is not int or version < 1 or version > len(MIGRATIONS):
                raise UnsupportedSchema()
        else:
            version = 0
        for index in range(version, len(MIGRATIONS)):
            destructive, statements = MIGRATIONS[index]
            if destructive and version > 0:
                backup_path = self.root / f'backup-v{index}-{uuid4().hex}.sqlite3'
                # A failed migration's original backup must never be overwritten.
                fd = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                os.close(fd)
                with closing(sqlite3.connect(backup_path)) as backup:
                    self._db.backup(backup)
            def migrate():
                for statement in statements:
                    self._db.execute(statement)
                self._db.execute('UPDATE schema_version SET version=?', (index + 1,))
            self._transaction(migrate)

    async def _settle(self, future):
        cancelled = False
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:
                cancelled = True
        # Surface an actual storage error even if cancellation raced with it.
        result = future.result()
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def _call(self, fn):
        if self._closed:
            raise StorageFailure()
        def execute():
            try:
                return fn()
            except (sqlite3.Error, OSError):
                raise StorageFailure() from None
        future = asyncio.get_running_loop().run_in_executor(self._executor, execute)
        return await self._settle(future)

    def _transaction(self, fn):
        self._db.execute('BEGIN IMMEDIATE')
        try:
            result = fn()
            self._db.execute('COMMIT')
            return result
        except BaseException:
            self._db.execute('ROLLBACK')
            raise

    async def close(self):
        if self._closing is None:
            self._closed = True
            def cleanup():
                try:
                    if self._db is not None:
                        self._db.close()
                        self._db = None
                finally:
                    if self._lock is not None:
                        os.close(self._lock)
                        self._lock = None
            self._closing = asyncio.get_running_loop().run_in_executor(self._executor, cleanup)
            self._executor.shutdown(wait=False)
        await self._settle(self._closing)

    def _session(self, session_id):
        row = self._db.execute('SELECT * FROM sessions WHERE id=?', (session_id,)).fetchone()
        if row is None:
            raise NotFound()
        return Session(**dict(row))

    def _run(self, run_id):
        row = self._db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
        if row is None:
            raise NotFound()
        return Run(id=row['id'], request=RunRequest.model_validate_json(row['payload_json']),
                   status=row['status'], output=row['output'], verification=row['verification'],
                   elapsed_s=self._elapsed(row), artifacts=self._artifacts(run_id),
                   error=Failure.model_validate_json(row['error_json']) if row['error_json'] else None)

    def _schedule(self, schedule_id):
        row = self._db.execute('SELECT * FROM schedules WHERE id=?', (schedule_id,)).fetchone()
        if row is None:
            raise NotFound()
        return Schedule(id=row['id'], session_id=row['session_id'], generation=row['generation'],
                        input=row['input'], next_due_at=datetime.fromisoformat(row['next_due_at']),
                        interval_seconds=row['interval_seconds'], tools=tuple(json.loads(row['tools_json'])),
                        status=row['status'], pause_reason=row['pause_reason'])

    def _event(self, run_id, kind, data):
        seq = self._db.execute('SELECT coalesce(max(seq),0)+1 FROM events WHERE run_id=?', (run_id,)).fetchone()[0]
        event = RunEvent(run_id=run_id, seq=seq, kind=kind, at=datetime.now(timezone.utc), data=data)
        self._db.execute('INSERT INTO events VALUES (?,?,?,?,?)',
                         (run_id, seq, kind, event.at.isoformat(), canonical(event.data)))
        return event

    async def create_session(self):
        def create():
            session = Session(id=uuid4().hex, generation=0)
            self._db.execute('INSERT INTO sessions VALUES (?,?)', (session.id, 0))
            return session
        return await self._call(lambda: self._transaction(create))

    async def get_session(self, session_id):
        return await self._call(lambda: self._session(session_id))

    async def reset_session(self, session_id, generation):
        if type(generation) is not int or generation < 0:
            raise InvalidRequest()
        def reset():
            session = self._session(session_id)
            if session.generation != generation:
                raise Conflict('stale_generation', 'Session generation changed.')
            if self._db.execute("SELECT 1 FROM runs WHERE session_id=? AND status IN ('queued','running','waiting_approval')", (session_id,)).fetchone():
                raise Conflict('session_busy', 'Session has an active run.')
            self._db.execute('UPDATE sessions SET generation=generation+1 WHERE id=?', (session_id,))
            self._db.execute("UPDATE schedules SET status='paused',pause_reason='stale_generation' "
                             "WHERE session_id=? AND status='active'", (session_id,))
            return self._session(session_id)
        return await self._call(lambda: self._transaction(reset))

    def _submit(self, request: RunRequest, schedule=None):
        session = self._session(request.session_id)
        if session.generation != request.generation:
            raise Conflict('stale_generation', 'Session generation changed.')
        payload = canonical(request.model_dump())
        previous = self._db.execute('SELECT id,payload_json FROM runs WHERE session_id=? AND generation=? AND request_id=?',
                                    (session.id, session.generation, request.request_id)).fetchone()
        if previous:
            if schedule is not None:
                raise Conflict('schedule_request_conflict', 'Scheduled work cannot adopt an existing run.')
            if canonical(RunRequest.model_validate_json(previous['payload_json']).model_dump()) != payload:
                raise Conflict('request_conflict', 'Request ID was used with a different payload.')
            return self._run(previous['id'])
        if request.retry_of:
            source = self._run(request.retry_of)
            if (source.request.session_id != session.id or source.request.generation != session.generation
                    or source.status not in {'failed', 'cancelled', 'interrupted'}):
                raise Conflict('invalid_retry', 'Retry source must be a failed, cancelled or interrupted run in this generation.')
        if self._db.execute("SELECT 1 FROM runs WHERE session_id=? AND generation=? AND status IN ('queued','running','waiting_approval')", (session.id, session.generation)).fetchone():
            raise Conflict('session_busy', 'Session has an active run.')
        run_id = uuid4().hex
        self._db.execute('INSERT INTO runs(id,session_id,generation,request_id,payload_json,status,output,error_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
                         (run_id, session.id, session.generation, request.request_id, payload, 'queued', None, None, now()))
        self._db.execute("INSERT INTO messages(run_id,role,content) VALUES (?,'user',?)", (run_id, request.text))
        event_data = {} if schedule is None else {
            'schedule_id': schedule[0], 'nominal_due_at': schedule[1],
        }
        self._event(run_id, 'run.queued', event_data)
        return self._run(run_id)

    async def submit(self, request: RunRequest):
        try:
            request = RunRequest.model_validate(request.model_dump())
        except (ValidationError, ValueError, AttributeError):
            raise InvalidRequest() from None
        if request.request_id.startswith('schedule:'):
            raise InvalidRequest()
        return await self._call(lambda: self._transaction(lambda: self._submit(request)))

    async def create_schedule(self, request: ScheduleRequest):
        try:
            request = ScheduleRequest.model_validate(request.model_dump())
        except (ValidationError, ValueError, AttributeError):
            raise InvalidRequest() from None
        payload = canonical(request.model_dump(mode='json'))
        def create():
            existing = self._db.execute('SELECT creation_json FROM schedules WHERE id=?',
                                        (request.id,)).fetchone()
            if existing:
                if existing['creation_json'] != payload:
                    raise Conflict('schedule_conflict', 'Schedule ID was used with a different creation payload.')
                return self._schedule(request.id)
            session = self._session(request.session_id)
            if session.generation != request.generation:
                raise Conflict('stale_generation', 'Session generation changed.')
            self._db.execute('INSERT INTO schedules VALUES (?,?,?,?,?,?,?,?,?,?)', (
                request.id, request.session_id, request.generation, request.input,
                instant(request.next_due_at), request.interval_seconds,
                canonical(list(request.tools)), payload, 'active', None,
            ))
            return self._schedule(request.id)
        return await self._call(lambda: self._transaction(create))

    async def get_schedule(self, schedule_id):
        return await self._call(lambda: self._schedule(schedule_id))

    async def schedules(self):
        def read():
            rows = self._db.execute('SELECT id FROM schedules ORDER BY rowid').fetchall()
            return [self._schedule(row['id']) for row in rows]
        return await self._call(read)

    async def schedule_occurrences(self, schedule_id):
        def read():
            self._schedule(schedule_id)
            rows = self._db.execute(
                'SELECT * FROM schedule_occurrences WHERE schedule_id=? ORDER BY nominal_due_at',
                (schedule_id,)).fetchall()
            return [ScheduleOccurrence(schedule_id=row['schedule_id'],
                        nominal_due_at=datetime.fromisoformat(row['nominal_due_at']), run_id=row['run_id'])
                    for row in rows]
        return await self._call(read)

    async def pause_schedule(self, schedule_id):
        def pause():
            schedule = self._schedule(schedule_id)
            if schedule.status == 'completed':
                raise Conflict('schedule_completed', 'A completed schedule cannot be paused.')
            if schedule.status == 'active':
                self._db.execute("UPDATE schedules SET status='paused',pause_reason='operator' WHERE id=?",
                                 (schedule_id,))
            return self._schedule(schedule_id)
        return await self._call(lambda: self._transaction(pause))

    async def retarget_schedule(self, schedule_id, expected_generation, generation):
        if (type(expected_generation) is not int or expected_generation < 0
                or type(generation) is not int or generation < 0):
            raise InvalidRequest()
        def retarget():
            schedule = self._schedule(schedule_id)
            if schedule.status == 'completed':
                raise Conflict('schedule_completed', 'A completed schedule cannot be retargeted.')
            if schedule.pause_reason == 'uncertain_effect' or self._db.execute(
                    "SELECT 1 FROM schedule_occurrences o JOIN runs r ON r.id=o.run_id "
                    "WHERE o.schedule_id=? AND r.status='uncertain'", (schedule_id,)).fetchone():
                raise Conflict('uncertain_effect', 'Uncertain scheduled work requires operator reconciliation.')
            if schedule.generation != expected_generation:
                raise Conflict('stale_generation', 'Schedule generation changed.')
            session = self._session(schedule.session_id)
            if session.generation != generation:
                raise Conflict('stale_generation', 'Session generation changed.')
            self._db.execute("UPDATE schedules SET generation=?,status='active',pause_reason=NULL WHERE id=?",
                             (generation, schedule_id))
            return self._schedule(schedule_id)
        return await self._call(lambda: self._transaction(retarget))

    async def tick_schedules(self, current: datetime):
        try:
            current = schedule_instant(current)
        except (ValueError, TypeError, AttributeError):
            raise InvalidRequest() from None
        current_text = instant(current)
        def tick():
            created = []
            rows = self._db.execute("SELECT id FROM schedules WHERE status='active' ORDER BY next_due_at,rowid").fetchall()
            for row in rows:
                schedule = self._schedule(row['id'])
                if self._db.execute(
                        "SELECT 1 FROM schedule_occurrences o JOIN runs r ON r.id=o.run_id "
                        "WHERE o.schedule_id=? AND r.status='uncertain'", (schedule.id,)).fetchone():
                    self._db.execute("UPDATE schedules SET status='paused',pause_reason='uncertain_effect' WHERE id=?",
                                     (schedule.id,))
                    continue
                session = self._session(schedule.session_id)
                if session.generation != schedule.generation:
                    self._db.execute("UPDATE schedules SET status='paused',pause_reason='stale_generation' WHERE id=?",
                                     (schedule.id,))
                    continue
                if instant(schedule.next_due_at) > current_text:
                    continue
                nominal = schedule.next_due_at
                nominal_text = instant(nominal)
                digest = hashlib.sha256(canonical([schedule.id, nominal_text]).encode()).hexdigest()
                request_id = f'schedule:{digest}'
                while self._db.execute(
                        'SELECT 1 FROM runs WHERE session_id=? AND generation=? AND request_id=?',
                        (schedule.session_id, schedule.generation, request_id)).fetchone():
                    request_id = f'schedule:{uuid4().hex}'
                request = RunRequest(session_id=schedule.session_id, generation=schedule.generation,
                                     request_id=request_id, text=schedule.input,
                                     tools=schedule.tools)
                try:
                    run = self._submit(request, (schedule.id, nominal_text))
                except Conflict as exc:
                    if exc.code == 'session_busy':
                        continue
                    raise
                self._db.execute('INSERT INTO schedule_occurrences VALUES (?,?,?)',
                                 (schedule.id, nominal_text, run.id))
                created.append(run.id)
                if schedule.interval_seconds is None:
                    self._db.execute("UPDATE schedules SET status='completed' WHERE id=?", (schedule.id,))
                    continue
                interval = timedelta(seconds=schedule.interval_seconds)
                steps = (current - nominal) // interval + 1
                try:
                    following = nominal + steps * interval
                except OverflowError:
                    following = None
                if following is None or following.year > 9998:
                    self._db.execute("UPDATE schedules SET status='completed' WHERE id=?", (schedule.id,))
                else:
                    self._db.execute('UPDATE schedules SET next_due_at=? WHERE id=?',
                                     (instant(following), schedule.id))
            return created
        return await self._call(lambda: self._transaction(tick))

    async def get_run(self, run_id):
        return await self._call(lambda: self._run(run_id))

    async def next_run(self):
        def claim():
            row = self._db.execute("SELECT id FROM runs WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
            if row is None:
                return None
            self._db.execute("UPDATE runs SET status='running',active_since=? WHERE id=?", (now(), row['id']))
            self._event(row['id'], 'run.started', {})
            return self._run(row['id'])
        return await self._call(lambda: self._transaction(claim))

    async def append(self, run_id, kind, data):
        if not kind or kind in {'run.queued', 'run.started', 'run.finished'}:
            raise InvalidRequest()
        def append_event():
            if self._run(run_id).status != 'running':
                raise Conflict('run_not_running', 'Events require a running run.')
            return self._event(run_id, kind, data)
        return await self._call(lambda: self._transaction(append_event))

    async def events(self, run_id, after=0, limit=100):
        if type(after) is not int or after < 0:
            raise Conflict('invalid_cursor', 'Event cursor must be a committed sequence or zero.')
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise InvalidRequest()
        def read():
            self._run(run_id)
            latest = self._db.execute('SELECT coalesce(max(seq),0) FROM events WHERE run_id=?', (run_id,)).fetchone()[0]
            if after > latest:
                raise Conflict('invalid_cursor', 'Event cursor is ahead of this run.')
            rows = self._db.execute('SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?', (run_id, after, limit)).fetchall()
            return [RunEvent(run_id=r['run_id'], seq=r['seq'], kind=r['kind'], at=r['at'], data=json.loads(r['payload_json'])) for r in rows]
        return await self._call(read)

    async def history(self, session_id, generation):
        if type(generation) is not int or generation < 0:
            raise InvalidRequest()
        def read():
            self._session(session_id)
            rows = self._db.execute("""SELECT m.role,m.content FROM messages m JOIN runs r ON r.id=m.run_id
                WHERE r.session_id=? AND r.generation=? AND r.status='succeeded'
                ORDER BY r.rowid, CASE m.role WHEN 'user' THEN 0 ELSE 1 END""", (session_id, generation)).fetchall()
            return [Message(**dict(row)) for row in rows]
        return await self._call(read)

    def _finish(self, run_id, status, output, error, verification='not_requested'):
        run = self._run(run_id)
        if run.status in TERMINAL:
            return run
        if run.status == 'queued' and status != 'cancelled':
            raise Conflict('invalid_transition', 'Only cancellation can finish queued work.')
        if status == 'succeeded' and (output is None or error is not None):
            raise InvalidRequest()
        if status != 'succeeded':
            output = None
        self._stop_clock(run_id)
        self._db.execute('UPDATE runs SET status=?,output=?,error_json=?,verification=? WHERE id=?',
                         (status, output, canonical(error.model_dump()) if error else None, verification, run_id))
        if status == 'uncertain':
            self._db.execute("UPDATE schedules SET status='paused',pause_reason='uncertain_effect' "
                             "WHERE status!='completed' AND id IN "
                             "(SELECT schedule_id FROM schedule_occurrences WHERE run_id=?)", (run_id,))
        self._db.execute("UPDATE approvals SET status='cancelled' WHERE status='pending' AND invocation_id IN (SELECT id FROM invocations WHERE run_id=?)", (run_id,))
        if status == 'succeeded':
            self._db.execute("INSERT INTO messages(run_id,role,content) VALUES (?,'assistant',?)", (run_id, output))
        self._event(run_id, 'run.finished', {'status': status, 'verification': verification})
        return self._run(run_id)

    async def finish(self, run_id, status: RunStatus, output=None, error=None, verification='not_requested'):
        if status not in TERMINAL:
            raise InvalidRequest()
        return await self._call(lambda: self._transaction(lambda: self._finish(run_id, status, output, error, verification)))

    async def recover(self):
        def recover():
            rows = self._db.execute("SELECT id FROM runs WHERE status='running' ORDER BY rowid").fetchall()
            recovered = []
            for row in rows:
                invocation_rows = self._db.execute(
                    'SELECT status,receipt_json FROM invocations WHERE run_id=? ORDER BY rowid', (row['id'],)
                ).fetchall()
                unknown = any(invocation['status'] == 'uncertain' for invocation in invocation_rows)
                status = 'uncertain' if unknown else 'interrupted'
                verifications = {
                    ToolReceipt.model_validate_json(invocation['receipt_json']).evidence.get('verification')
                    for invocation in invocation_rows if invocation['receipt_json']
                }
                verification = 'failed' if 'failed' in verifications else 'passed' if 'passed' in verifications else 'not_requested'
                recovered.append(self._finish(row['id'], status, None,
                    Failure(code=status, message='Owner stopped before the response completed.'), verification))
            return recovered
        return await self._call(lambda: self._transaction(recover))

    @staticmethod
    def _elapsed(row):
        elapsed = row['elapsed_s']
        if row['active_since']:
            elapsed += max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(row['active_since'])).total_seconds())
        return elapsed

    def _stop_clock(self, run_id):
        row = self._db.execute('SELECT elapsed_s,active_since FROM runs WHERE id=?', (run_id,)).fetchone()
        self._db.execute('UPDATE runs SET elapsed_s=?,active_since=NULL WHERE id=?', (self._elapsed(row), run_id))

    async def elapsed(self, run_id):
        return (await self.get_run(run_id)).elapsed_s

    def _artifacts(self, run_id):
        rows = self._db.execute('SELECT a.path,a.sha256,a.size_bytes FROM artifacts a JOIN invocations i ON a.invocation_id=i.id WHERE i.run_id=? ORDER BY i.rowid,a.path', (run_id,)).fetchall()
        return tuple(Artifact(**dict(row)) for row in rows)

    async def installation_id(self):
        return await self._call(lambda: self._db.execute('SELECT id FROM installation').fetchone()[0])

    async def save_checkpoint(self, run_id, checkpoint: Checkpoint):
        def save():
            run = self._run(run_id)
            if run.status != 'running':
                raise Conflict('run_not_running', 'Checkpoint requires active execution.')
            value = checkpoint.model_copy(update={'elapsed_s': run.elapsed_s})
            self._db.execute('UPDATE runs SET checkpoint_json=? WHERE id=?', (value.model_dump_json(), run_id))
            return value
        return await self._call(lambda: self._transaction(save))

    async def load_checkpoint(self, run_id):
        def read():
            self._run(run_id)
            value = self._db.execute('SELECT checkpoint_json FROM runs WHERE id=?', (run_id,)).fetchone()[0]
            return Checkpoint.model_validate_json(value) if value else None
        return await self._call(read)

    async def history_groups(self, session_id, generation):
        def read():
            self._session(session_id)
            rows = self._db.execute("SELECT id,checkpoint_json FROM runs WHERE session_id=? AND generation=? AND status='succeeded' ORDER BY rowid", (session_id, generation)).fetchall()
            result = []
            for row in rows:
                if row['checkpoint_json']:
                    cp = Checkpoint.model_validate_json(row['checkpoint_json'])
                    result.append(cp.messages[cp.history_length:])
                else:
                    messages = self._db.execute("SELECT role,content FROM messages WHERE run_id=? ORDER BY CASE role WHEN 'user' THEN 0 ELSE 1 END", (row['id'],)).fetchall()
                    result.append([Message(**dict(m)) for m in messages])
            return result
        return await self._call(read)

    async def grant(self, workspace_id, capability):
        if capability not in {'write', 'execute'} or not workspace_id:
            raise InvalidRequest()
        def save():
            self._db.execute('INSERT OR IGNORE INTO grants VALUES (?,?,?)', (workspace_id, capability, now()))
            return {'workspace_id': workspace_id, 'capability': capability}
        return await self._call(lambda: self._transaction(save))

    async def grants(self, workspace_id):
        return await self._call(lambda: {r[0] for r in self._db.execute('SELECT capability FROM grants WHERE workspace_id=?', (workspace_id,)).fetchall()})

    def _invocation(self, invocation_id):
        row = self._db.execute('SELECT * FROM invocations WHERE id=?', (invocation_id,)).fetchone()
        if row is None:
            raise NotFound()
        return Invocation(id=row['id'], run_id=row['run_id'], call=ToolCall.model_validate_json(row['call_json']),
            arguments_sha256=row['arguments_sha256'], policy_sha256=row['policy_sha256'], workspace_id=row['workspace_id'],
            capability=row['capability'], status=row['status'], approved=bool(row['approved']), container_id=row['container_id'],
            receipt=ToolReceipt.model_validate_json(row['receipt_json']) if row['receipt_json'] else None)

    async def get_invocation(self, invocation_id):
        return await self._call(lambda: self._invocation(invocation_id))

    async def invocations(self, run_id=None):
        def read():
            if run_id is not None:
                self._run(run_id)
            query = 'SELECT id FROM invocations' + (' WHERE run_id=?' if run_id is not None else '') + ' ORDER BY rowid'
            return [self._invocation(row[0]) for row in self._db.execute(query, (run_id,) if run_id is not None else ()).fetchall()]
        return await self._call(read)

    async def prepare_invocation(self, run_id, call, policy_sha256, workspace_id, capability):
        def prepare():
            run = self._run(run_id)
            call_json = canonical(call.model_dump())
            arguments_sha256 = hashlib.sha256(canonical(call.arguments).encode()).hexdigest()
            existing = self._db.execute('SELECT id,call_json,policy_sha256,workspace_id FROM invocations WHERE run_id=? AND call_id=?', (run_id, call.id)).fetchone()
            if existing:
                if (existing['call_json'], existing['policy_sha256'], existing['workspace_id']) != (call_json, policy_sha256, workspace_id):
                    raise Conflict('invocation_changed', 'Call arguments, schema, policy or workspace changed; prior authority is invalid.')
                return self._invocation(existing['id'])
            if run.status != 'running':
                raise Conflict('run_not_running', 'Invocations require active execution.')
            invocation_id = uuid4().hex
            self._db.execute('INSERT INTO invocations(id,run_id,call_id,call_json,arguments_sha256,policy_sha256,workspace_id,capability,status) VALUES (?,?,?,?,?,?,?,?,?)',
                (invocation_id, run_id, call.id, call_json, arguments_sha256, policy_sha256, workspace_id, capability, 'prepared'))
            self._event(run_id, 'tool.requested', {'invocation_id': invocation_id, 'call': call.model_dump(),
                'arguments_sha256': arguments_sha256, 'policy_sha256': policy_sha256, 'workspace_id': workspace_id, 'intent': capability})
            return self._invocation(invocation_id)
        return await self._call(lambda: self._transaction(prepare))

    def _approval(self, approval_id):
        row = self._db.execute('SELECT * FROM approvals WHERE id=?', (approval_id,)).fetchone()
        if row is None:
            raise NotFound()
        inv = self._invocation(row['invocation_id'])
        return Approval(id=row['id'], invocation_id=inv.id, run_id=inv.run_id, call=inv.call,
            arguments_sha256=inv.arguments_sha256, policy_sha256=inv.policy_sha256, workspace_id=inv.workspace_id,
            expires_at=row['expires_at'], status=row['status'])

    async def get_approval(self, approval_id):
        return await self._call(lambda: self._approval(approval_id))

    async def require_approval(self, invocation_id):
        def pause():
            inv = self._invocation(invocation_id)
            existing = self._db.execute('SELECT id FROM approvals WHERE invocation_id=?', (inv.id,)).fetchone()
            if existing:
                return self._approval(existing[0])
            row = self._db.execute('SELECT status,checkpoint_json FROM runs WHERE id=?', (inv.run_id,)).fetchone()
            if row['status'] != 'running' or not row['checkpoint_json']:
                raise Conflict('checkpoint_required', 'Persist the pending call before approval.')
            approval_id = uuid4().hex
            expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
            self._db.execute('INSERT INTO approvals VALUES (?,?,?,?)', (approval_id, inv.id, expires, 'pending'))
            self._db.execute("UPDATE invocations SET status='waiting_approval' WHERE id=?", (inv.id,))
            self._stop_clock(inv.run_id)
            self._db.execute("UPDATE runs SET status='waiting_approval' WHERE id=?", (inv.run_id,))
            approval = self._approval(approval_id)
            self._event(inv.run_id, 'approval.required', approval.model_dump())
            return approval
        return await self._call(lambda: self._transaction(pause))

    async def approvals(self):
        await self.expire_approvals()
        return await self._call(lambda: [self._approval(row[0]) for row in self._db.execute("SELECT id FROM approvals WHERE status='pending' ORDER BY rowid").fetchall()])

    async def expire_approvals(self):
        def expire():
            rows = self._db.execute("SELECT a.id,i.run_id FROM approvals a JOIN invocations i ON i.id=a.invocation_id WHERE a.status='pending' AND a.expires_at<=?", (now(),)).fetchall()
            for row in rows:
                self._db.execute("UPDATE approvals SET status='expired' WHERE id=?", (row['id'],))
                self._finish(row['run_id'], 'failed', None, Failure(code='approval_expired', message='Operator approval expired after 24 hours.'))
            return len(rows)
        return await self._call(lambda: self._transaction(expire))

    async def decide_approval(self, approval_id, approved, arguments_sha256, policy_sha256):
        await self.expire_approvals()
        def decide():
            approval = self._approval(approval_id)
            run = self._run(approval.run_id)
            if approval.status != 'pending' or run.status != 'waiting_approval':
                raise Conflict('approval_not_pending', 'This approval is no longer pending.')
            if (approval.arguments_sha256, approval.policy_sha256) != (arguments_sha256, policy_sha256):
                raise Conflict('approval_changed', 'Decision must bind the exact arguments and policy.')
            self._db.execute('UPDATE approvals SET status=? WHERE id=?', ('approved' if approved else 'denied', approval_id))
            self._event(run.id, 'approval.decided', {'approval_id': approval_id, 'approved': approved})
            if approved:
                self._db.execute("UPDATE invocations SET approved=1,status='prepared' WHERE id=?", (approval.invocation_id,))
                self._db.execute("UPDATE runs SET status='queued' WHERE id=?", (run.id,))
                self._event(run.id, 'run.queued', {'resumed_approval': approval_id})
                return self._run(run.id)
            return self._finish(run.id, 'failed', None, Failure(code='approval_denied', message='Operator denied this invocation.'))
        return await self._call(lambda: self._transaction(decide))

    async def mark_invocation_running(self, invocation_id):
        def start():
            inv = self._invocation(invocation_id)
            if inv.status != 'prepared' or self._run(inv.run_id).status != 'running':
                raise Conflict('invocation_not_prepared', 'An invocation can be dispatched only once.')
            self._db.execute("UPDATE invocations SET status='running' WHERE id=?", (inv.id,))
        return await self._call(lambda: self._transaction(start))

    async def bind_container(self, invocation_id, container_id):
        def bind():
            inv = self._invocation(invocation_id)
            if inv.container_id is not None and inv.container_id != container_id:
                raise Conflict('container_changed', 'Invocation already owns another container.')
            self._db.execute('UPDATE invocations SET container_id=? WHERE id=?', (container_id, inv.id))
        return await self._call(lambda: self._transaction(bind))

    async def complete_invocation(self, receipt: ToolReceipt):
        def complete():
            inv = self._invocation(receipt.invocation_id)
            if inv.receipt:
                if inv.receipt != receipt:
                    raise Conflict('receipt_exists', 'A conclusive invocation receipt cannot be replaced.')
                return inv.receipt
            self._db.execute('UPDATE invocations SET status=?,receipt_json=? WHERE id=?', (receipt.status, receipt.model_dump_json(), inv.id))
            for artifact in receipt.artifacts:
                self._db.execute('INSERT INTO artifacts VALUES (?,?,?,?)', (inv.id, artifact.path, artifact.sha256, artifact.size_bytes))
            self._event(inv.run_id, 'tool.finished', receipt.model_dump(mode='json'))
            return receipt
        return await self._call(lambda: self._transaction(complete))
