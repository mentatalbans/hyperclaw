"""Test-only barriers at MCP ownership and receipt durability boundaries."""
import asyncio
import builtins
import json
import os
from pathlib import Path
import sys

from hyperclaw.execution.docker import DockerBackend
from hyperclaw.store import Store

root = Path(sys.argv[sys.argv.index('--root') + 1])
fault = json.loads((root / 'mcp-test-fault.json').read_text())
stage = fault['stage']

async def barrier():
    (root / 'mcp-test-barrier').write_text(stage)
    await asyncio.Event().wait()

create = DockerBackend.create_docs
async def create_docs(self, *args, **kwargs):
    cid = await create(self, *args, **kwargs)
    if stage == 'after_create': await barrier()
    return cid
DockerBackend.create_docs = create_docs

attach = DockerBackend.attach_start
async def attach_start(self, cid):
    process = await attach(self, cid)
    if stage == 'after_start':
        for _ in range(100):
            if (await self.inspect(cid))['state'] == 'running': break
            await asyncio.sleep(.01)
        await barrier()
    return process
DockerBackend.attach_start = attach_start

complete = Store.complete_invocation
async def complete_invocation(self, receipt):
    result = await complete(self, receipt)
    if stage == 'after_receipt' and receipt.evidence.get('server') == 'docs': await barrier()
    return result
Store.complete_invocation = complete_invocation

if stage == 'missing_sdk':
    original = builtins.__import__
    def blocked(name, *args, **kwargs):
        if name == 'mcp' or name.startswith('mcp.') or name == 'mcp_types':
            raise ImportError('Test-only missing optional SDK')
        return original(name, *args, **kwargs)
    builtins.__import__ = blocked

from hyperclaw.cli import main
main()
