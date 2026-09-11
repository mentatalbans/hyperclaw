"""Test-only observation of real Store calls, followed by Task 2's transport."""
import json
from pathlib import Path
import runpy
import sys
import threading
import time

from hyperclaw.store import Store

root = Path(sys.argv[sys.argv.index('--root') + 1])
lock = threading.Lock()
output = (root / 'measurement.jsonl').open('a', buffering=1)


def record(value):
    with lock:
        output.write(json.dumps(value) + '\n')


original_transaction = Store._transaction


def transaction(self, operation):
    started = time.monotonic()
    try:
        return original_transaction(self, operation)
    finally:
        record({'kind': 'transaction', 'at': started,
                'seconds': time.monotonic() - started})


Store._transaction = transaction
original_tick = Store.tick_schedules
previous_end = None


async def tick(self, current):
    global previous_end
    started = time.monotonic()
    result = await original_tick(self, current)
    ended = time.monotonic()
    counts = await self._call(lambda: dict(self._db.execute(
        'SELECT status,count(*) FROM runs GROUP BY status').fetchall()))
    active, due = await self._call(lambda: self._db.execute(
        "SELECT count(*),coalesce(sum(next_due_at<=?),0) FROM schedules WHERE status='active'",
        (current.isoformat(),)).fetchone())
    record({'kind': 'tick', 'at': started, 'seconds': ended - started,
            'interval_lateness_seconds': max(0, started - previous_end - .1) if previous_end else None,
            'counts': counts, 'active': active, 'due': due, 'created': len(result)})
    previous_end = ended
    return result


Store.tick_schedules = tick
runpy.run_path(str(Path(__file__).with_name('telegram_daemon.py')), run_name='__main__')
