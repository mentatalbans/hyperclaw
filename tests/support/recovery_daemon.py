"""Test-only CLI launcher: stop at real durable/effect boundaries for SIGKILL."""
import asyncio
import json
from pathlib import Path
import sys
import threading

from hyperclaw.store import Store
from hyperclaw.execution.workspace import Workspace
from hyperclaw.execution.docker import DockerBackend

root = Path(sys.argv[sys.argv.index('--root') + 1])
stage = json.loads((root / 'test-fault.json').read_text())['stage']


def signal():
    (root / 'test-barrier').write_text(stage)


async def pause():
    signal()
    await asyncio.Event().wait()


original_prepare = Store.prepare_invocation
async def prepare(self, *args, **kwargs):
    if stage == 'before_invocation':
        await pause()
    value = await original_prepare(self, *args, **kwargs)
    if stage == 'after_invocation':
        await pause()
    return value
Store.prepare_invocation = prepare

original_mark = Store.mark_invocation_running
async def mark(self, *args, **kwargs):
    value = await original_mark(self, *args, **kwargs)
    if stage == 'before_effect':
        await pause()
    return value
Store.mark_invocation_running = mark

original_write = Workspace.write
def write(self, *args, **kwargs):
    value = original_write(self, *args, **kwargs)
    if stage == 'after_dispatch':
        signal()
        threading.Event().wait()
    return value
Workspace.write = write

original_start = DockerBackend.start
async def start(self, *args, **kwargs):
    value = await original_start(self, *args, **kwargs)
    if stage in {'after_dispatch', 'after_exit'}:
        marker = root / 'workspace' / ('effect' if stage == 'after_exit' else 'ready')
        expected = 'complete' if stage == 'after_exit' else 'yes'
        async with asyncio.timeout(15):
            while True:
                try:
                    if marker.read_text() == expected:
                        break
                except OSError:
                    pass
                await asyncio.sleep(.01)
        if stage == 'after_exit':
            # Observe actual exit without committing an invocation receipt.
            await self.wait(args[0], 15, 65536)
        await pause()
    return value
DockerBackend.start = start

original_complete = Store.complete_invocation
async def complete(self, *args, **kwargs):
    value = await original_complete(self, *args, **kwargs)
    if stage == 'after_receipt':
        await pause()
    return value
Store.complete_invocation = complete

from hyperclaw.cli import main
main()
