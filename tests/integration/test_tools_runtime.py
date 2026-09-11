"""The single worker pauses and resumes exact calls while other sessions progress."""
import asyncio

import pytest
from hyperclaw.config import Settings
from hyperclaw.contracts import ModelEvent, RunRequest, TERMINAL
from hyperclaw.runtime import Runtime
from hyperclaw.store import Store


class ToolModel:
    def __init__(self, *, repeated=False):
        self.requests = []
        self.repeated = repeated

    async def aclose(self):
        pass

    async def stream(self, messages, system='', tools=None):
        self.requests.append([m.model_dump() for m in messages])
        if isinstance(messages[-1].content, str) or self.repeated:
            call = {'id':f'call-{len(self.requests)}', 'name':'workspace_write', 'arguments':{'path':'answer.txt','content':'checkpointed'}}
            block = {'type':'tool_use','id':call['id'],'name':call['name'],'input':call['arguments']}
            yield ModelEvent(kind='tool_call',data=call)
            yield ModelEvent(kind='finish',data={'stop_reason':'tool_use','content':[block]})
        else:
            yield ModelEvent(kind='text',data={'text':'File checked.'})
            yield ModelEvent(kind='finish',data={'stop_reason':'end_turn','content':[{'type':'text','text':'File checked.'}]})


async def open_runtime(root, model, **settings):
    runtime = Runtime(await Store.open(root), model, Settings(root=root, **settings))
    await runtime.start()
    return runtime


async def submit(runtime, text='write'):
    session = await runtime.create_session()
    return await runtime.submit(RunRequest(session_id=session.id,generation=0,request_id='r',text=text))


async def status(runtime, run, wanted):
    async with asyncio.timeout(5):
        while True:
            result = await runtime.get_run(run.id)
            if result.status in wanted:
                return result
            await asyncio.sleep(.01)


async def test_approval_restart_resumes_exact_arguments_and_grouped_results(tmp_path):
    first = ToolModel()
    runtime = await open_runtime(tmp_path, first)
    run = await submit(runtime)
    try:
        await status(runtime, run, {'waiting_approval'})
        assert not (tmp_path/'workspace/answer.txt').exists()
        assert len(first.requests) == 1
        await runtime.close()
        second = ToolModel()
        runtime = await open_runtime(tmp_path, second)
        approval = (await runtime.approvals())[0]
        await runtime.decide_approval(approval.id, True, approval.arguments_sha256, approval.policy_sha256)
        result = await status(runtime, run, TERMINAL)
        assert result.status == 'succeeded', result
        assert result.verification == 'passed'
        assert (tmp_path/'workspace/answer.txt').read_text() == 'checkpointed'
        assert len(second.requests) == 1
        assert second.requests[0][-2]['content'][0]['type'] == 'tool_use'
        assert second.requests[0][-1]['content'][0]['tool_use_id'] == 'call-1'
        assert len(await runtime.receipts(run.id)) == 1
        groups = await runtime.store.history_groups(run.request.session_id,0)
        assert len(groups) == 1
        assert [m.role for m in groups[0]] == ['user','assistant','user','assistant']
        assert groups[0][1].content[0]['id'] == groups[0][2].content[0]['tool_use_id']
    finally:
        await runtime.close()


async def test_waiting_approval_releases_worker_and_cancel_does_not_execute(tmp_path):
    runtime = await open_runtime(tmp_path, ToolModel())
    try:
        one = await submit(runtime)
        await status(runtime, one, {'waiting_approval'})
        two = await submit(runtime)
        await status(runtime, two, {'waiting_approval'})
        assert len(await runtime.approvals()) == 2
        assert (await runtime.cancel(one.id)).status == 'cancelled'
        assert not (tmp_path/'workspace/answer.txt').exists()
    finally:
        await runtime.close()


async def test_repeated_call_bound_stops_further_mutations(tmp_path):
    runtime = await open_runtime(tmp_path, ToolModel(repeated=True))
    try:
        await runtime.grant(runtime.executor.workspace.identity, 'write')
        run = await submit(runtime)
        result = await status(runtime, run, TERMINAL)
        assert result.status == 'failed'
        assert result.error.code == 'repeated_tool_call'
        assert len(await runtime.receipts(run.id)) == 3
    finally:
        await runtime.close()


class ReadLoop(ToolModel):
    async def stream(self, messages, system='', tools=None):
        self.requests.append(messages)
        call = {'id':f'read-{len(self.requests)}','name':'workspace_search','arguments':{'query':str(len(self.requests))}}
        yield ModelEvent(kind='tool_call',data=call)
        yield ModelEvent(kind='finish',data={'stop_reason':'tool_use','content':[{'type':'tool_use','id':call['id'],'name':call['name'],'input':call['arguments']}]})


async def test_twelve_round_limit_is_applied_to_actual_model_requests(tmp_path):
    model = ReadLoop()
    runtime = await open_runtime(tmp_path, model)
    try:
        run = await submit(runtime)
        result = await status(runtime, run, TERMINAL)
        assert result.status == 'failed' and result.error.code == 'tool_round_limit'
        assert len(model.requests) == 12
        assert len(await runtime.receipts(run.id)) == 12
    finally:
        await runtime.close()


async def test_restart_does_not_reset_consumed_active_budget(tmp_path):
    runtime = await open_runtime(tmp_path, ToolModel(), run_timeout_s=1.0)
    run = await submit(runtime)
    try:
        await status(runtime, run, {'waiting_approval'})
        approval = (await runtime.approvals())[0]
        await runtime.store._call(lambda: runtime.store._db.execute('UPDATE runs SET elapsed_s=2 WHERE id=?', (run.id,)))
        await runtime.close()
        model = ToolModel()
        runtime = await open_runtime(tmp_path, model, run_timeout_s=1.0)
        await runtime.decide_approval(approval.id, True, approval.arguments_sha256, approval.policy_sha256)
        result = await status(runtime, run, TERMINAL)
        assert result.status == 'failed' and result.error.code == 'run_timeout'
        assert not model.requests
        assert not (tmp_path/'workspace/answer.txt').exists()
    finally:
        await runtime.close()
