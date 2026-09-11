"""Explicit Docker acceptance through the public Runtime and durable Store path."""
import asyncio
from pathlib import Path

import pytest
from hyperclaw.config import Settings
from hyperclaw.contracts import Checkpoint, Message, ModelEvent, RunRequest, ToolCall, TERMINAL
from hyperclaw.execution import Executor
from hyperclaw.runtime import Runtime
from hyperclaw.store import Store

pytestmark = pytest.mark.docker


class CommandModel:
    def __init__(self, argv):
        self.argv = argv
        self.calls = 0

    async def aclose(self):
        pass

    async def stream(self, messages, system='', tools=None):
        self.calls += 1
        if self.calls == 1:
            call = {'id':'command-one','name':'command','arguments':{'argv':self.argv}}
            yield ModelEvent(kind='tool_call',data=call)
            yield ModelEvent(kind='finish',data={'stop_reason':'tool_use','content':[{'type':'tool_use','id':call['id'],'name':'command','input':call['arguments']}]})
        else:
            yield ModelEvent(kind='text',data={'text':'done'})
            yield ModelEvent(kind='finish',data={'stop_reason':'end_turn','content':[{'type':'text','text':'done'}]})


async def ready(path):
    async with asyncio.timeout(20):
        while not path.exists():
            await asyncio.sleep(.02)


async def terminal(runtime, run_id):
    async with asyncio.timeout(20):
        while True:
            run = await runtime.get_run(run_id)
            if run.status in TERMINAL:
                return run
            await asyncio.sleep(.02)


SCRIPT = """import os,time
from pathlib import Path
pid=os.fork()
if pid==0:
    time.sleep(3);Path('/workspace/child-late').write_text('bad');os._exit(0)
Path('/workspace/ready').write_text('yes')
time.sleep(3);Path('/workspace/parent-late').write_text('bad')
os.waitpid(pid,0)
"""


async def test_runtime_cancel_stops_container_children_before_terminal_receipt(tmp_path):
    settings = Settings(root=tmp_path,run_timeout_s=30.0)
    model = CommandModel(['python','-c',SCRIPT])
    runtime = Runtime(await Store.open(tmp_path),model,settings)
    await runtime.start()
    try:
        await runtime.grant(runtime.executor.workspace.identity,'execute')
        await runtime.grant(runtime.executor.workspace.identity,'write')
        session = await runtime.create_session()
        run = await runtime.submit(RunRequest(session_id=session.id,generation=0,request_id='cancel',text='test'))
        await ready(tmp_path/'workspace/ready')
        result = await runtime.cancel(run.id)
        assert result.status == 'cancelled', result
        receipts = await runtime.receipts(run.id)
        assert len(receipts) == 1 and receipts[0].evidence['terminated']
        await asyncio.sleep(3.1)
        assert not (tmp_path/'workspace/child-late').exists()
        assert not (tmp_path/'workspace/parent-late').exists()
        assert await runtime.executor.backend.owned() == []
        assert model.calls == 1
    finally:
        await runtime.close()


async def test_startup_reconciles_running_container_before_queued_model_work(tmp_path):
    settings = Settings(root=tmp_path,run_timeout_s=30.0)
    store = await Store.open(tmp_path)
    executor = await Executor.open(store,settings)
    session = await store.create_session()
    run = await store.submit(RunRequest(session_id=session.id,generation=0,request_id='abrupt',text='test'))
    await store.next_run()
    call = ToolCall(id='command-one',name='command',arguments={'argv':['python','-c',SCRIPT]})
    await store.save_checkpoint(run.id,Checkpoint(messages=[Message(role='user',content='test')],pending_calls=[call],workspace_id=executor.workspace.identity))
    await store.grant(executor.workspace.identity,'execute')
    await store.grant(executor.workspace.identity,'write')
    decision = (await executor.policy()).check(call,run.request.tools)
    inv = await store.prepare_invocation(run.id,call,decision.sha256,executor.workspace.identity,'execute')
    await store.mark_invocation_running(inv.id)
    cid = await executor.backend.create(inv.id,call.arguments['argv'],writable=True)
    # Simulate process death in the create/ID-commit gap: labels must recover it.
    await executor.backend.start(cid)
    await ready(tmp_path/'workspace/ready')
    other = await store.create_session()
    queued = await store.submit(RunRequest(session_id=other.id,generation=0,request_id='queued',text='test',tools=()))
    await executor.close()
    await store.close()
    class ObservingModel(CommandModel):
        async def stream(self,messages,system='',tools=None):
            assert await runtime.executor.backend.owned() == []
            yield ModelEvent(kind='text',data={'text':'queued after reconciliation'})
            yield ModelEvent(kind='finish',data={'stop_reason':'end_turn'})
    runtime = Runtime(await Store.open(tmp_path),ObservingModel([]),settings)
    try:
        await runtime.start()
        assert (await runtime.get_run(run.id)).status == 'interrupted'
        assert (await terminal(runtime,queued.id)).status == 'succeeded'
        assert (await runtime.receipts(run.id))[0].status == 'interrupted'
        await asyncio.sleep(3.1)
        assert not (tmp_path/'workspace/child-late').exists()
        assert not (tmp_path/'workspace/parent-late').exists()
    finally:
        await runtime.close()
