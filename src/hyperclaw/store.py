"""Single-owner SQLite storage. Every state transition and its events commit together."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
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
    RunEvent, RunRequest, RunStatus, Session, StorageFailure, TERMINAL,
    UnsupportedSchema, canonical,
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


def now():
    return datetime.now(timezone.utc).isoformat()


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
            if destructive:
                backup_path = self.root / f'backup-v{index}.sqlite3'
                # A failed migration's original backup must never be overwritten.
                fd = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                os.close(fd)
                with sqlite3.connect(backup_path) as backup:
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
                   status=row['status'], output=row['output'],
                   error=Failure.model_validate_json(row['error_json']) if row['error_json'] else None)

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
            if self._db.execute("SELECT 1 FROM runs WHERE session_id=? AND status IN ('queued','running')", (session_id,)).fetchone():
                raise Conflict('session_busy', 'Session has an active run.')
            self._db.execute('UPDATE sessions SET generation=generation+1 WHERE id=?', (session_id,))
            return self._session(session_id)
        return await self._call(lambda: self._transaction(reset))

    async def submit(self, request: RunRequest):
        try:
            request = RunRequest.model_validate(request.model_dump())
        except (ValidationError, ValueError, AttributeError):
            raise InvalidRequest() from None
        def accept():
            session = self._session(request.session_id)
            if session.generation != request.generation:
                raise Conflict('stale_generation', 'Session generation changed.')
            payload = canonical(request.model_dump())
            previous = self._db.execute('SELECT id,payload_json FROM runs WHERE session_id=? AND generation=? AND request_id=?',
                                        (session.id, session.generation, request.request_id)).fetchone()
            if previous:
                if previous['payload_json'] != payload:
                    raise Conflict('request_conflict', 'Request ID was used with a different payload.')
                return self._run(previous['id'])
            if request.retry_of:
                source = self._run(request.retry_of)
                if (source.request.session_id != session.id or source.request.generation != session.generation
                        or source.status not in {'failed', 'cancelled', 'interrupted'}):
                    raise Conflict('invalid_retry', 'Retry source must be a failed, cancelled or interrupted run in this generation.')
            if self._db.execute("SELECT 1 FROM runs WHERE session_id=? AND generation=? AND status IN ('queued','running')", (session.id, session.generation)).fetchone():
                raise Conflict('session_busy', 'Session has an active run.')
            run_id = uuid4().hex
            self._db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)',
                             (run_id, session.id, session.generation, request.request_id, payload, 'queued', None, None, now()))
            self._db.execute("INSERT INTO messages(run_id,role,content) VALUES (?,'user',?)", (run_id, request.text))
            self._event(run_id, 'run.queued', {})
            return self._run(run_id)
        return await self._call(lambda: self._transaction(accept))

    async def get_run(self, run_id):
        return await self._call(lambda: self._run(run_id))

    async def next_run(self):
        def claim():
            row = self._db.execute("SELECT id FROM runs WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
            if row is None:
                return None
            self._db.execute("UPDATE runs SET status='running' WHERE id=?", (row['id'],))
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

    def _finish(self, run_id, status, output, error):
        run = self._run(run_id)
        if run.status in TERMINAL:
            return run
        if run.status == 'queued' and status != 'cancelled':
            raise Conflict('invalid_transition', 'Only cancellation can finish queued work.')
        if status == 'succeeded' and (output is None or error is not None):
            raise InvalidRequest()
        if status != 'succeeded':
            output = None
        self._db.execute('UPDATE runs SET status=?,output=?,error_json=? WHERE id=?',
                         (status, output, canonical(error.model_dump()) if error else None, run_id))
        if status == 'succeeded':
            self._db.execute("INSERT INTO messages(run_id,role,content) VALUES (?,'assistant',?)", (run_id, output))
        self._event(run_id, 'run.finished', {'status': status, 'verification': 'not_requested'})
        return self._run(run_id)

    async def finish(self, run_id, status: RunStatus, output=None, error=None):
        if status not in TERMINAL:
            raise InvalidRequest()
        return await self._call(lambda: self._transaction(lambda: self._finish(run_id, status, output, error)))

    async def recover(self):
        def recover():
            rows = self._db.execute("SELECT id FROM runs WHERE status='running' ORDER BY rowid").fetchall()
            return [self._finish(row['id'], 'interrupted', None, Failure(code='interrupted', message='Owner stopped before the response completed.')) for row in rows]
        return await self._call(lambda: self._transaction(recover))
