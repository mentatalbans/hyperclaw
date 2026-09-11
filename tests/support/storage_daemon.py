"""Disposable SQLite faults below Store sanitization; never exhaust the host disk."""
import json
from pathlib import Path
import sqlite3
import sys
import threading

from hyperclaw.store import Store

root = Path(sys.argv[sys.argv.index('--root') + 1])
stage = (root / 'test-storage-stage').read_text().strip()
boundary = {}


def record(**values):
    boundary.update(stage=stage, **values)
    (root / 'test-storage-observed.json').write_text(json.dumps(boundary, sort_keys=True))


class Connection(sqlite3.Connection):
    fail_commit = False

    def execute(self, sql, parameters=(), /):
        if sql == 'COMMIT' and self.fail_commit:
            self.fail_commit = False
            record(sql=sql, controlled_commit_failure=True)
            raise sqlite3.OperationalError('controlled commit I/O failure')
        try:
            result = super().execute(sql, parameters)
        except sqlite3.Error as exc:
            if getattr(exc, 'sqlite_errorcode', None) is not None and 'sqlite_errorcode' not in boundary:
                record(sql=sql, sqlite_errorcode=exc.sqlite_errorcode,
                       sqlite_errorname=exc.sqlite_errorname, sqlite_message=str(exc))
            raise
        if stage == 'migration_interrupt' and sql == 'DROP TABLE events':
            record(sql=sql, in_transaction=self.in_transaction)
            threading.Event().wait()  # Parent must SIGKILL this owned child.
        return result


original_connect = sqlite3.connect


def connect(*args, **kwargs):
    return original_connect(*args, **dict(kwargs, factory=Connection))


sqlite3.connect = connect


def full(self, after_effect):
    db = self._db
    db.execute('CREATE TABLE IF NOT EXISTS test_storage_pressure (payload BLOB)')
    count = db.execute('PRAGMA page_count').fetchone()[0]
    limit = db.execute(f'PRAGMA max_page_count={count}').fetchone()[0]
    boundary.update(page_count=count, max_page_count=limit,
                    page_size=db.execute('PRAGMA page_size').fetchone()[0],
                    observed_effect=(root / 'workspace/answer.txt').exists())
    action = 'UPDATE' if after_effect else 'INSERT'
    predicate = 'WHEN NEW.receipt_json IS NOT NULL' if after_effect else ''
    db.execute(f"""CREATE TEMP TRIGGER test_full BEFORE {action} ON invocations {predicate}
        BEGIN INSERT INTO test_storage_pressure VALUES (zeroblob(1048576)); END""")


original_prepare = Store.prepare_invocation


async def prepare(self, *args, **kwargs):
    if stage == 'full_before':
        await self._call(lambda: full(self, False))
    journal = root / 'runtime.sqlite3-journal'
    if stage == 'journal_before':
        journal.mkdir()
        boundary.update(observed_effect=False, journal_obstruction='directory')
    try:
        return await original_prepare(self, *args, **kwargs)
    finally:
        if stage == 'journal_before':
            journal.rmdir()


Store.prepare_invocation = prepare
original_complete = Store.complete_invocation


async def complete(self, receipt):
    if stage in {'full_after', 'commit_after'}:
        # The real workspace tool has returned after publication and inspection.
        artifact = root / 'workspace/answer.txt'
        assert artifact.read_text() == 'hello'
        boundary.update(observed_effect=True, effect_text=artifact.read_text(),
                        invocation_id=receipt.invocation_id)
        if stage == 'full_after':
            await self._call(lambda: full(self, True))

    return await original_complete(self, receipt)


Store.complete_invocation = complete
original_complete_sync = Store._complete_invocation


def complete_sync(self, receipt):
    result = original_complete_sync(self, receipt)
    if stage == 'commit_after':
        self._db.fail_commit = True
    return result


Store._complete_invocation = complete_sync

from hyperclaw.cli import main
main()
