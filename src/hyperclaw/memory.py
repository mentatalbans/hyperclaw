"""Explicit scoped memory stored on the runtime's single SQLite owner."""
from datetime import datetime
import re
from uuid import uuid4

from pydantic import ValidationError

from hyperclaw.contracts import (
    Checkpoint,
    Conflict,
    InvalidRequest,
    MemoryRecord,
    MemoryScope,
    NotFound,
    memory_instant,
    memory_query,
    memory_text,
)
from hyperclaw.store import instant, now


_WORD = re.compile(r'[^\W_]+', re.UNICODE)


class Memory:
    def __init__(self, store, *, clock=None):
        self.store = store
        self.clock = clock or (lambda: datetime.fromisoformat(now()))

    @staticmethod
    def _scope(scope):
        try:
            if not isinstance(scope, MemoryScope):
                raise ValueError
            return MemoryScope.model_validate(scope.model_dump())
        except (ValidationError, ValueError, TypeError):
            raise InvalidRequest() from None

    @staticmethod
    def _text(text):
        try:
            return memory_text(text)
        except (ValueError, TypeError):
            raise InvalidRequest() from None

    @staticmethod
    def _query(query):
        try:
            return memory_query(query)
        except (ValueError, TypeError):
            raise InvalidRequest() from None

    @staticmethod
    def _instant(value):
        try:
            return memory_instant(value)
        except (ValueError, TypeError):
            raise InvalidRequest() from None

    def _observed_at(self):
        return self._instant(self.clock())

    def _validate_new(self, scope, text, source_run_id, valid_until, observed_at):
        scope = self._scope(scope)
        text = self._text(text)
        observed_at = self._instant(observed_at)
        if valid_until is not None:
            valid_until = self._instant(valid_until)
            if valid_until <= observed_at:
                raise InvalidRequest('invalid_memory_expiry', 'Memory expiry must be after observation time.')
        if source_run_id is not None:
            if not isinstance(source_run_id, str) or not source_run_id or len(source_run_id) > 256:
                raise InvalidRequest()
        return scope, text, source_run_id, valid_until, observed_at

    def _validate_source(self, scope, source_run_id):
        if source_run_id is None:
            return
        row = self.store._db.execute(
            'SELECT session_id,checkpoint_json FROM runs WHERE id=?', (source_run_id,)).fetchone()
        if row is None:
            raise NotFound()
        checkpoint = Checkpoint.model_validate_json(row['checkpoint_json']) if row['checkpoint_json'] else None
        wrong_session = scope.session_id is not None and row['session_id'] != scope.session_id
        wrong_workspace = checkpoint is not None and checkpoint.workspace_id and checkpoint.workspace_id != scope.workspace_id
        if wrong_session or wrong_workspace:
            raise InvalidRequest('invalid_memory_source', 'Memory source does not match its scope.')

    @staticmethod
    def _record(row):
        return MemoryRecord(
            id=row['id'],
            scope=MemoryScope(workspace_id=row['workspace_id'], session_id=row['session_id']),
            text=row['text'], source_run_id=row['source_run_id'],
            observed_at=datetime.fromisoformat(row['observed_at']),
            valid_until=datetime.fromisoformat(row['valid_until']) if row['valid_until'] else None,
            supersedes=row['supersedes'], version=row['version'], status=row['status'],
        )

    def _remember(self, scope, text, source_run_id=None, *, valid_until=None, observed_at=None):
        observed_at = self._observed_at() if observed_at is None else observed_at
        scope, text, source_run_id, valid_until, observed_at = self._validate_new(
            scope, text, source_run_id, valid_until, observed_at)
        if scope.session_id is not None:
            self.store._session(scope.session_id)
        self._validate_source(scope, source_run_id)
        record_id = uuid4().hex
        self.store._db.execute(
            """INSERT INTO memory_records(
                id,workspace_id,session_id,text,source_run_id,observed_at,valid_until,
                supersedes,version,status) VALUES (?,?,?,?,?,?,?,?,1,'active')""",
            (record_id, scope.workspace_id, scope.session_id, text, source_run_id,
             instant(observed_at), instant(valid_until) if valid_until else None, None),
        )
        row = self.store._db.execute('SELECT * FROM memory_records WHERE id=?', (record_id,)).fetchone()
        return self._record(row)

    async def remember(self, scope, text, source_run_id=None, *, valid_until=None):
        observed_at = self._observed_at()
        return await self.store._call(lambda: self.store._transaction(
            lambda: self._remember(scope, text, source_run_id,
                                   valid_until=valid_until, observed_at=observed_at)))

    @staticmethod
    def _match_query(query):
        tokens = list(dict.fromkeys(_WORD.findall(query)))
        return ' OR '.join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)

    def _search(self, scope, query, limit=5, *, observed_at=None):
        scope = self._scope(scope)
        query = self._query(query)
        if type(limit) is not int or not 1 <= limit <= 5:
            raise InvalidRequest()
        observed_at = self._observed_at() if observed_at is None else self._instant(observed_at)
        if scope.session_id is not None:
            self.store._session(scope.session_id)
        match = self._match_query(query)
        if not match:
            return []
        rows = self.store._db.execute(
            """SELECT m.* FROM memory_fts
               JOIN memory_records AS m ON m.rowid=memory_fts.rowid
               WHERE memory_fts MATCH ? AND m.workspace_id=?
                 AND ((? IS NULL AND m.session_id IS NULL)
                      OR (? IS NOT NULL AND (m.session_id=? OR m.session_id IS NULL)))
                 AND m.status='active' AND (m.valid_until IS NULL OR m.valid_until>?)
               ORDER BY bm25(memory_fts),m.rowid LIMIT ?""",
            (match, scope.workspace_id, scope.session_id, scope.session_id,
             scope.session_id, instant(observed_at), limit),
        ).fetchall()
        return [self._record(row) for row in rows]

    async def search(self, scope, query, limit=5):
        observed_at = self._observed_at()
        return await self.store._call(
            lambda: self._search(scope, query, limit, observed_at=observed_at))

    def _scoped_row(self, record_id, scope):
        if not isinstance(record_id, str) or not record_id or len(record_id) > 256:
            raise InvalidRequest()
        row = self.store._db.execute(
            'SELECT * FROM memory_records WHERE id=? AND workspace_id=? AND session_id IS ?',
            (record_id, scope.workspace_id, scope.session_id),
        ).fetchone()
        if row is None:
            raise NotFound()
        return row

    def _correct(self, record_id, text, scope, *, source_run_id=None,
                 valid_until=None, observed_at=None):
        scope = self._scope(scope)
        observed_at = self._observed_at() if observed_at is None else observed_at
        scope, text, source_run_id, valid_until, observed_at = self._validate_new(
            scope, text, source_run_id, valid_until, observed_at)
        row = self._scoped_row(record_id, scope)
        if row['status'] != 'active':
            raise Conflict('stale_memory', 'Only an active memory version can be corrected.')
        self._validate_source(scope, source_run_id)
        changed = self.store._db.execute(
            "UPDATE memory_records SET status='superseded' WHERE id=? AND status='active'",
            (record_id,),
        ).rowcount
        if changed != 1:
            raise Conflict('stale_memory', 'Only an active memory version can be corrected.')
        new_id = uuid4().hex
        self.store._db.execute(
            """INSERT INTO memory_records(
                id,workspace_id,session_id,text,source_run_id,observed_at,valid_until,
                supersedes,version,status) VALUES (?,?,?,?,?,?,?,?,?,'active')""",
            (new_id, scope.workspace_id, scope.session_id, text, source_run_id,
             instant(observed_at), instant(valid_until) if valid_until else None,
             record_id, row['version'] + 1),
        )
        return self._record(self.store._db.execute(
            'SELECT * FROM memory_records WHERE id=?', (new_id,)).fetchone())

    async def correct(self, record_id, text, scope, *, source_run_id=None, valid_until=None):
        observed_at = self._observed_at()
        return await self.store._call(lambda: self.store._transaction(
            lambda: self._correct(record_id, text, scope, source_run_id=source_run_id,
                                  valid_until=valid_until, observed_at=observed_at)))

    def _forget(self, record_id, scope):
        scope = self._scope(scope)
        row = self._scoped_row(record_id, scope)
        if row['status'] == 'forgotten':
            return None
        if row['status'] != 'active':
            raise Conflict('stale_memory', 'Only an active memory version can be forgotten.')
        changed = self.store._db.execute(
            "UPDATE memory_records SET status='forgotten' WHERE id=? AND status='active'",
            (record_id,),
        ).rowcount
        if changed != 1:
            raise Conflict('stale_memory', 'Only an active memory version can be forgotten.')
        return None

    async def forget(self, record_id, scope):
        return await self.store._call(lambda: self.store._transaction(
            lambda: self._forget(record_id, scope)))
