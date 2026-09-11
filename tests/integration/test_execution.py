"""Public Executor effects use real Store and real descriptor-scoped files."""
import asyncio
from pathlib import Path

import pytest

from hyperclaw.config import Settings
from hyperclaw.contracts import ApprovalRequired, Checkpoint, Conflict, Message, RunRequest, ToolCall
from hyperclaw.execution import Executor
from hyperclaw.store import Store


async def setup(tmp_path):
    store = await Store.open(tmp_path)
    executor = await Executor.open(store, Settings(root=tmp_path))
    session = await store.create_session()
    run = await store.submit(RunRequest(session_id=session.id, generation=0, request_id='r', text='write'))
    await store.next_run()
    call = ToolCall(id='w', name='workspace_write', arguments={'path':'answer.txt', 'content':'hello'})
    await store.save_checkpoint(run.id, Checkpoint(messages=[Message(role='user', content='write')], pending_calls=[call], workspace_id=executor.workspace.identity))
    return store, executor, run, call


async def test_write_approval_exact_resume_receipt_and_scoped_grant(tmp_path):
    store, executor, run, call = await setup(tmp_path)
    try:
        with pytest.raises(ApprovalRequired):
            await executor.invoke(run.id, call)
        assert not (tmp_path/'workspace/answer.txt').exists()
        approval = (await store.approvals())[0]
        await executor.decide_approval(approval.id, True, approval.arguments_sha256, approval.policy_sha256)
        await store.next_run()
        first = await executor.invoke(run.id, call)
        assert first.status == 'succeeded'
        assert first.artifacts[0].sha256 == '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824'
        (tmp_path/'workspace/answer.txt').write_text('operator edit')
        assert await executor.invoke(run.id, call) == first
        assert (tmp_path/'workspace/answer.txt').read_text() == 'operator edit'
        await store.grant(executor.workspace.identity, 'write')
        second = await executor.invoke(run.id, call.model_copy(update={'id':'w2'}))
        assert second.status == 'succeeded'
        assert await store.approvals() == []
    finally:
        await executor.close()
        await store.close()


async def test_changed_policy_invalidates_pending_approval(tmp_path):
    store, executor, run, call = await setup(tmp_path)
    try:
        with pytest.raises(ApprovalRequired):
            await executor.invoke(run.id, call)
        approval = (await store.approvals())[0]
        await store.grant(executor.workspace.identity, 'execute')
        with pytest.raises(Conflict):
            await executor.decide_approval(approval.id, True, approval.arguments_sha256, approval.policy_sha256)
        assert not (tmp_path/'workspace/answer.txt').exists()
    finally:
        await executor.close()
        await store.close()


async def test_wrong_expected_hash_is_observed_failure_and_escapes_fail(tmp_path):
    store, executor, run, call = await setup(tmp_path)
    try:
        await store.grant(executor.workspace.identity, 'write')
        bad = call.model_copy(update={'arguments':dict(call.arguments, expected_sha256='0'*64)})
        receipt = await executor.invoke(run.id, bad)
        assert receipt.status == 'failed'
        assert receipt.evidence['verification'] == 'failed'
        assert receipt.artifacts[0].sha256 != '0'*64
        for i, path in enumerate(('../outside', '/etc/passwd')):
            receipt = await executor.invoke(run.id, ToolCall(id=f'r{i}', name='workspace_read', arguments={'path':path}))
            assert receipt.status == 'failed'
    finally:
        await executor.close()
        await store.close()


async def test_backend_loss_records_uncertain_and_never_reexecutes(tmp_path):
    from hyperclaw.execution.docker import DockerUnavailable
    store, executor, run, _ = await setup(tmp_path)
    class LostBackend:
        starts = 0
        async def create(self, invocation_id, argv, writable):
            return 'a'*64
        async def start(self, container_id):
            self.starts += 1
        async def wait(self, *args):
            raise DockerUnavailable()
        async def owned(self):
            raise DockerUnavailable()
    backend = LostBackend()
    executor.backend = backend
    try:
        await store.grant(executor.workspace.identity, 'execute')
        call = ToolCall(id='command',name='command',arguments={'argv':['true']})
        receipt = await executor.invoke(run.id,call)
        assert receipt.status == 'uncertain'
        assert await executor.invoke(run.id,call) == receipt
        await executor.reconcile()
        assert backend.starts == 1
        await store.recover()
        assert (await store.get_run(run.id)).status == 'uncertain'
    finally:
        await executor.close()
        await store.close()


async def test_reconcile_preserves_approved_queued_invocation(tmp_path):
    store, executor, run, call = await setup(tmp_path)
    try:
        with pytest.raises(ApprovalRequired):
            await executor.invoke(run.id, call)
        approval = (await store.approvals())[0]
        resumed = await executor.decide_approval(
            approval.id, True, approval.arguments_sha256, approval.policy_sha256
        )
        assert resumed.status == 'queued'

        assert await executor.reconcile() == []
        invocation = (await store.invocations(run.id))[0]
        assert invocation.status == 'prepared'
        assert invocation.approved
        assert invocation.receipt is None

        await store.next_run()
        receipt = await executor.invoke(run.id, call)
        assert receipt.status == 'succeeded'
        assert (tmp_path / 'workspace/answer.txt').read_text() == 'hello'
    finally:
        await executor.close()
        await store.close()


async def test_reconcile_settles_prepared_invocation_for_terminal_parent(tmp_path):
    store, executor, run, call = await setup(tmp_path)
    try:
        decision = (await executor.policy()).check(call, run.request.tools)
        invocation = await store.prepare_invocation(
            run.id, call, decision.sha256, executor.workspace.identity, decision.capability
        )
        await store.finish(run.id, 'interrupted')

        receipts = await executor.reconcile()
        assert len(receipts) == 1
        assert receipts[0].invocation_id == invocation.id
        assert receipts[0].status == 'interrupted'
        assert (await store.get_run(run.id)).status == 'interrupted'
    finally:
        await executor.close()
        await store.close()


async def test_host_file_cancellation_settles_effect_and_receipt(tmp_path, monkeypatch):
    import threading
    store, executor, run, call = await setup(tmp_path)
    entered, release = threading.Event(), threading.Event()
    write = executor.workspace.write
    def gated(*args):
        entered.set()
        assert release.wait(3)
        return write(*args)
    monkeypatch.setattr(executor.workspace,'write',gated)
    try:
        await store.grant(executor.workspace.identity,'write')
        task = asyncio.create_task(executor.invoke(run.id,call))
        assert await asyncio.to_thread(entered.wait,3)
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        inv = (await store.invocations(run.id))[0]
        assert inv.receipt.status == 'succeeded'
        assert (tmp_path/'workspace/answer.txt').read_text() == 'hello'
    finally:
        release.set()
        await executor.close()
        await store.close()


async def test_reconciled_exit_zero_still_verifies_requested_artifact_hash(tmp_path):
    from hyperclaw.execution.docker import DockerResult
    store, executor, run, _ = await setup(tmp_path)
    (tmp_path/'workspace/answer.txt').write_text('hello')
    call = ToolCall(id='c',name='command',arguments={'argv':['true'],'checks':[{'path':'answer.txt','expected_sha256':'0'*64}]})
    decision = (await executor.policy()).check(call,run.request.tools)
    inv = await store.prepare_invocation(run.id,call,decision.sha256,executor.workspace.identity,'execute')
    await store.mark_invocation_running(inv.id)
    await store.bind_container(inv.id,'a'*64)
    class ExitedBackend:
        async def owned(self):
            return [{'id':'a'*64,'invocation_id':inv.id,'state':'exited'}]
        async def terminate(self,container_id):
            return DockerResult('succeeded',exit_code=0,terminated=True)
        async def remove(self,container_id):
            pass
    executor.backend = ExitedBackend()
    try:
        receipts = await executor.reconcile()
        assert len(receipts)==1
        assert receipts[0].status == 'failed'
        assert receipts[0].evidence['verification'] == 'failed'
        assert receipts[0].artifacts[0].sha256 == '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824'
    finally:
        await executor.close()
        await store.close()


async def test_public_file_tools_reject_symlink_fifo_and_device_paths(tmp_path):
    import os
    store, executor, run, _ = await setup(tmp_path)
    outside = tmp_path/'outside'
    outside.write_text('private sentinel')
    (tmp_path/'workspace/link').symlink_to(outside)
    os.mkfifo(tmp_path/'workspace/pipe')
    (tmp_path/'workspace/device').symlink_to('/dev/null')
    try:
        await store.grant(executor.workspace.identity,'write')
        for index,path in enumerate(('link','pipe','device')):
            for kind,args in [('workspace_read',{'path':path}),('workspace_write',{'path':path,'content':'bad'})]:
                receipt = await executor.invoke(run.id,ToolCall(id=f'{index}-{kind}',name=kind,arguments=args))
                assert receipt.status == 'failed'
        assert outside.read_text() == 'private sentinel'
        assert (tmp_path/'workspace/link').is_symlink()
    finally:
        await executor.close()
        await store.close()


async def test_replaced_workspace_fails_durably_without_unreceipted_dispatch(tmp_path):
    store, executor, run, _ = await setup(tmp_path)
    (tmp_path/'workspace').rename(tmp_path/'original')
    (tmp_path/'workspace').mkdir()
    try:
        await store.grant(executor.workspace.identity,'execute')
        receipt = await executor.invoke(run.id,ToolCall(id='cmd',name='command',arguments={'argv':['true']}))
        assert receipt.status == 'failed'
        assert (await store.invocations(run.id))[0].receipt == receipt
    finally:
        await executor.close()
        await store.close()


async def test_start_rejection_terminates_created_container_and_records_receipt(tmp_path):
    from hyperclaw.contracts import InvalidRequest
    from hyperclaw.execution.docker import DockerResult
    store, executor, run, _ = await setup(tmp_path)
    class RejectStart:
        alive = False
        removed = False
        async def create(self,*args,**kwargs):
            self.alive = True
            return 'a'*64
        async def start(self,cid):
            raise InvalidRequest('workspace_changed','Workspace changed')
        async def terminate(self,cid):
            self.alive = False
            return DockerResult('failed',terminated=True)
        async def remove(self,cid):
            assert (await store.invocations(run.id))[0].receipt is not None
            self.removed = True
    backend = RejectStart()
    executor.backend = backend
    try:
        await store.grant(executor.workspace.identity,'execute')
        receipt = await executor.invoke(run.id,ToolCall(id='cmd',name='command',arguments={'argv':['true']}))
        assert receipt.status == 'failed'
        assert receipt.evidence['terminated']
        assert not backend.alive and backend.removed
    finally:
        await executor.close()
        await store.close()
