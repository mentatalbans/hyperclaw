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

from hyperclaw.cli import main
main()
