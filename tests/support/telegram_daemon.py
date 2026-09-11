"""Test-only local transport and crash gates; imports the installed production package."""
import asyncio
import importlib.util
import logging
from pathlib import Path
import sys

import httpx

from hyperclaw.runtime import Runtime
from hyperclaw.store import Store
from hyperclaw.telegram import TelegramAdapter

# Resolve copied support assets by this file, never source-tree/PYTHONPATH assumptions.
spec = importlib.util.spec_from_file_location('telegram_test_transport', Path(__file__).with_name('telegram_peer.py'))
module = importlib.util.module_from_spec(spec)
# telegram_peer imports tests.support.images; the copied tests root is explicit and test-only.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
spec.loader.exec_module(module)
root = Path(sys.argv[sys.argv.index('--root') + 1])
peer_url = (root / 'test-telegram-url').read_text().strip()
stage = (root / 'test-telegram-stage').read_text().strip()
original_init = TelegramAdapter.__init__


def adapter_init(self, runtime, settings, client=None):
    original_init(self, runtime, settings, httpx.AsyncClient(
        transport=module.LocalTransport(peer_url), trust_env=False, follow_redirects=False))


TelegramAdapter.__init__ = adapter_init
logging.getLogger('httpx').setLevel(logging.INFO)
logging.getLogger('httpx').addHandler(logging.StreamHandler())


async def gate():
    marker = root / 'test-telegram-observed'
    if not marker.exists():
        marker.write_text(stage)
        await asyncio.Event().wait()


if stage == 'reserve':
    original_reserve = Store.telegram_reserve
    async def reserve(self, *args, **kwargs):
        result = await original_reserve(self, *args, **kwargs)
        await gate()
        return result
    Store.telegram_reserve = reserve
elif stage == 'submit':
    original_submit = Runtime.submit
    async def submit(self, *args, **kwargs):
        result = await original_submit(self, *args, **kwargs)
        await gate()
        return result
    Runtime.submit = submit

# Composition gates are armed by the parent only after unrelated setup settles.
# Every persistence wrapper awaits the real operation before marking its boundary.
async def composition_gate(name):
    marker = root / ('test-composition-' + name)
    if (root / 'test-composition-arm').exists() and not marker.exists():
        marker.write_text(name)
        await asyncio.Event().wait()


if stage.startswith('composition-') or stage == 'observe-effects':
    from hyperclaw.execution.workspace import Workspace
    original_write = Workspace.write
    def observed_write(self, path, *args, **kwargs):
        result = original_write(self, path, *args, **kwargs)
        # Observe each completed real write, including dispatch before a lost receipt.
        with (root / 'test-observed-writes').open('a') as journal:
            journal.write(path + '\n')
        return result
    Workspace.write = observed_write


if stage.startswith('composition-'):
    original_submit = Runtime.submit
    async def composition_submit(self, request):
        result = await original_submit(self, request)
        if stage == 'composition-http' and request.request_id == 'mixed-http':
            await composition_gate('intake')
        if stage == 'composition-telegram-submit' and request.request_id.startswith('telegram:'):
            await composition_gate('intake')
        return result
    Runtime.submit = composition_submit

    original_tick = Store.tick_schedules
    async def composition_tick(self, current):
        result = await original_tick(self, current)
        if stage == 'composition-schedule' and result:
            await composition_gate('intake')
        return result
    Store.tick_schedules = composition_tick

    original_reconcile = Store.telegram_reconcile
    async def composition_reconcile(self, *args):
        result = await original_reconcile(self, *args)
        if stage == 'composition-telegram-bind' and result:
            await composition_gate('intake')
        return result
    Store.telegram_reconcile = composition_reconcile

    original_complete = Store.complete_invocation
    async def composition_complete(self, receipt):
        # Workspace dispatch has really finished; the receipt is not committed yet.
        if (root / 'test-composition-hold-effect').exists():
            await composition_gate('effect')
        return await original_complete(self, receipt)
    Store.complete_invocation = composition_complete

elif stage == 'shutdown':
    original_adapter_close = TelegramAdapter._close
    async def composition_close(self):
        original_transport_close = self.transport.close
        async def delayed_close():
            (root / 'test-cleanup-entered').touch()
            while not (root / 'test-cleanup-release').exists():
                await asyncio.sleep(.01)
            await original_transport_close()
            (root / 'test-cleanup-complete').touch()
        self.transport.close = delayed_close
        await original_adapter_close(self)
    TelegramAdapter._close = composition_close

from hyperclaw.cli import main
main()
