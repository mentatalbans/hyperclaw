"""Test-only pauses/failure at the real memory/receipt transaction boundary."""
import asyncio
from pathlib import Path
import sys
import threading

from hyperclaw.memory import Memory
from hyperclaw.store import Store

root = Path(sys.argv[sys.argv.index('--root') + 1])
stage = (root / 'test-memory-stage').read_text()


def signal():
    (root / 'test-memory-barrier').write_text(stage)


original_complete = Store._complete_invocation


def complete(self, receipt):
    invocation = self._invocation(receipt.invocation_id)
    is_memory = invocation.call.name.startswith('memory_')
    if stage == 'fail_memory_receipt' and is_memory:
        # Created inside this transaction, so the failing trigger is rolled back too.
        self._db.execute("""CREATE TEMP TRIGGER fail_memory_event BEFORE INSERT ON events
            WHEN NEW.kind='tool.finished' BEGIN SELECT RAISE(ABORT, 'injected memory receipt failure'); END""")
    result = original_complete(self, receipt)
    if stage == 'before_memory_commit' and is_memory:
        signal()
        threading.Event().wait()
    return result


Store._complete_invocation = complete
original_invoke = Memory.invoke


async def invoke(self, invocation_id):
    receipt = await original_invoke(self, invocation_id)
    if stage == 'after_memory_commit':
        signal()
        await asyncio.Event().wait()
    return receipt


Memory.invoke = invoke

from hyperclaw.cli import main
main()
