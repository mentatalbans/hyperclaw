"""Test-only timer/failure controls; all database work stays on the Store owner."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

from hyperclaw.contracts import StorageFailure
from hyperclaw.store import Store

root = Path(sys.argv[sys.argv.index('--root') + 1])
stage = (root / 'test-maintenance-stage').read_text().strip()

original_approval = Store.require_approval


async def require_approval(self, *args, **kwargs):
    result = await original_approval(self, *args, **kwargs)
    if stage == 'expiry':
        due = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()
        await self._call(lambda: self._transaction(lambda: self._db.execute(
            'UPDATE approvals SET expires_at=? WHERE id=?', (due, result.id))))
    return result


Store.require_approval = require_approval


def fail_after_trigger(original):
    async def controlled(self, *args, **kwargs):
        if (root / 'test-failure-trigger').exists():
            (root / 'test-failure-observed').write_text(stage)
            raise StorageFailure()
        return await original(self, *args, **kwargs)
    return controlled


if stage == 'worker':
    Store.next_run = fail_after_trigger(Store.next_run)
elif stage == 'maintenance' and hasattr(Store, 'tick_schedules'):
    Store.tick_schedules = fail_after_trigger(Store.tick_schedules)

from hyperclaw.cli import main

main()
