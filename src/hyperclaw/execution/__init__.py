"""Execution policy in the actual effect path; Store owns every durable receipt."""
import asyncio
import json
from pathlib import Path

from hyperclaw.contracts import (
    ApprovalRequired, Artifact, Conflict, Failure, InvalidRequest, RuntimeErrorBase,
    TERMINAL, ToolReceipt,
)
from hyperclaw.execution.policy import Policy


async def settle(task):
    """Complete a started host effect and its receipt even if its caller cancels."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


class Executor:
    def __init__(self, store, settings, workspace, backend):
        self.store, self.settings, self.workspace, self.backend = store, settings, workspace, backend
        from hyperclaw.memory import Memory
        self.memory = Memory(store)
        from hyperclaw.mcp import McpTools
        self.mcp = McpTools(store, settings, backend)

    @classmethod
    async def open(cls, store, settings):
        from hyperclaw.execution.workspace import Workspace
        from hyperclaw.execution.docker import DockerBackend
        selected = Path(settings.workspace_path).expanduser() if settings.workspace_path else settings.root / 'workspace'
        if not selected.is_absolute():
            raise InvalidRequest('workspace_absolute', 'Configure an absolute workspace path.')
        if not settings.workspace_path:
            selected.mkdir(exist_ok=True, mode=0o700)
        resolved = selected.resolve()
        for protected in (settings.root.resolve(), Path.home().resolve()):
            if resolved == protected or resolved in protected.parents:
                raise InvalidRequest('unsafe_workspace', 'The workspace cannot contain the runtime root or operator home.')
        workspace = Workspace(selected)
        try:
            backend = DockerBackend(await store.installation_id(), resolved)
            return cls(store, settings, workspace, backend)
        except BaseException:
            workspace.close()
            raise

    async def policy(self, offered=()):
        from hyperclaw.contracts import MCP_TOOLS
        manifest = await self.mcp.current() if set(offered) & set(MCP_TOOLS) else None
        return Policy(self.workspace.identity, await self.store.grants(self.workspace.identity), manifest)

    async def definitions(self, offered):
        definitions = {item['name']: item for item in (await self.policy(offered)).definitions(offered)}
        for item in self.mcp.list_tools():
            if item.name in offered:
                definitions[item.name] = item.model_dump(include={'name', 'description', 'input_schema'})
        return [definitions[name] for name in offered if name in definitions]

    async def decide_approval(self, approval_id, approved, arguments_sha256, policy_sha256):
        approval = await self.store.get_approval(approval_id)
        run = await self.store.get_run(approval.run_id)
        decision = (await self.policy(run.request.tools)).check(approval.call, run.request.tools)
        if approved and (decision.sha256 != approval.policy_sha256 or self.workspace.identity != approval.workspace_id):
            raise Conflict('approval_changed', 'The effective policy or workspace changed; this invocation cannot reuse approval.')
        return await self.store.decide_approval(approval_id, approved, arguments_sha256, policy_sha256)

    async def invoke(self, run_id, call):
        run = await self.store.get_run(run_id)
        decision = (await self.policy(run.request.tools)).check(call, run.request.tools)
        inv = await self.store.prepare_invocation(run_id, call, decision.sha256, self.workspace.identity, decision.capability)
        if inv.receipt:
            return inv.receipt
        if run.status != 'running':
            raise Conflict('run_not_running', 'Tool execution requires an active run.')
        if inv.status not in {'prepared', 'waiting_approval'}:
            return await self.store.complete_invocation(ToolReceipt(invocation_id=inv.id, status='uncertain',
                evidence={'reason': 'Prior dispatch has no receipt; execution is not retried.'}))
        if decision.requires_approval and not inv.approved:
            await self.store.require_approval(inv.id)
            raise ApprovalRequired()
        # Recheck after the persistence/approval boundary, immediately before dispatch.
        current = (await self.policy(run.request.tools)).check(call, run.request.tools)
        if current.sha256 != inv.policy_sha256:
            raise Conflict('policy_changed', 'The effective policy changed before dispatch.')
        remaining = self.settings.run_timeout_s - await self.store.elapsed(run_id)
        if remaining <= 0:
            return await self.store.complete_invocation(ToolReceipt(invocation_id=inv.id, status='failed',
                evidence={'reason': 'Run execution budget exhausted.'}))
        if decision.effect == 'mcp':
            return await self.mcp.invoke(call, invocation_id=inv.id)
        if decision.effect == 'memory':
            return await settle(asyncio.create_task(self.memory.invoke(inv.id)))
        if decision.effect != 'command':
            return await settle(asyncio.create_task(self._file(inv, decision)))
        return await self._command(inv, decision, min(remaining, decision.deadline_s))

    async def _file(self, inv, decision):
        from hyperclaw.execution.workspace import WorkspaceUncertain
        await self.store.mark_invocation_running(inv.id)
        args = decision.arguments
        artifacts, evidence = (), {}
        try:
            if inv.call.name == 'workspace_read':
                output = await asyncio.to_thread(self.workspace.read, args['path'])
            elif inv.call.name == 'workspace_list':
                output = json.dumps(await asyncio.to_thread(self.workspace.list, args['path']), ensure_ascii=False)
            elif inv.call.name == 'workspace_search':
                output = json.dumps(await asyncio.to_thread(self.workspace.search, args['query'], args['path']), ensure_ascii=False)
            else:
                observed = await asyncio.to_thread(self.workspace.write, args['path'], args['content'], args['expected_sha256'])
                artifacts = (Artifact(**{k: observed[k] for k in ('path', 'sha256', 'size_bytes')}),)
                evidence = {'verification': 'passed' if observed['verified'] else 'failed', 'observed': observed}
                output = json.dumps(observed)
            status = 'failed' if evidence.get('verification') == 'failed' else 'succeeded'
        except WorkspaceUncertain as exc:
            status, output, evidence = 'uncertain', exc.message, {'code': exc.code}
        except InvalidRequest as exc:
            status, output, evidence = 'failed', exc.message, {'code': exc.code}
        except OSError:
            status, output = 'uncertain' if decision.effect == 'write' else 'failed', 'Workspace operation did not return a conclusive result.'
        return await self.store.complete_invocation(ToolReceipt(invocation_id=inv.id, status=status, output=output,
                                                              artifacts=artifacts, evidence=evidence))

    def _container_receipt(self, inv, result, *, cancelled=False):
        status = 'uncertain' if not result.terminated else 'cancelled' if cancelled else 'succeeded' if result.exit_code == 0 and result.status == 'succeeded' else 'failed'
        return ToolReceipt(invocation_id=inv.id, status=status, output=result.output,
            evidence={'container_id': inv.container_id, 'terminated': result.terminated, 'exit_code': result.exit_code,
                      'truncated': result.truncated, 'verification': 'passed' if status == 'succeeded' else 'failed'})

    async def _command(self, inv, decision, timeout_s):
        from hyperclaw.execution.docker import DockerUnavailable
        await self.store.mark_invocation_running(inv.id)
        try:
            container_id = await self.backend.create(inv.id, decision.arguments['argv'], writable=decision.writable)
            await self.store.bind_container(inv.id, container_id)
            inv = await self.store.get_invocation(inv.id)
            await self.backend.start(container_id)
            result = await self.backend.wait(container_id, timeout_s, decision.output_limit)
            receipt = self._container_receipt(inv, result)
            receipt = await self._verify_command_receipt(inv, receipt)
            await self.store.complete_invocation(receipt)
            if result.terminated:
                await self.backend.remove(container_id)
            return receipt
        except asyncio.CancelledError:
            await self.cancel(inv.run_id)
            raise
        except DockerUnavailable as exc:
            # A lost CLI response cannot establish that the container was never created/started.
            receipt = ToolReceipt(invocation_id=inv.id, status='uncertain', output=exc.message, evidence={'reason': 'Docker backend unavailable; no automatic retry.'})
            current = await self.store.get_invocation(inv.id)
            return current.receipt or await self.store.complete_invocation(receipt)
        except InvalidRequest as exc:
            # A dispatch rejection is still a durable invocation outcome. A created
            # container must be accounted for before the run can report failure.
            current = await self.store.get_invocation(inv.id)
            if current.receipt:
                return current.receipt
            evidence = {'code': exc.code}
            status = 'failed'
            if current.container_id:
                try:
                    result = await self.backend.terminate(current.container_id)
                    evidence['terminated'] = result.terminated
                    if not result.terminated:
                        status = 'uncertain'
                except DockerUnavailable:
                    status = 'uncertain'
            receipt = await self.store.complete_invocation(ToolReceipt(invocation_id=inv.id,
                status=status, output=exc.message, evidence=evidence))
            if current.container_id and evidence.get('terminated'):
                try:
                    await self.backend.remove(current.container_id)
                except DockerUnavailable:
                    pass  # Receipt is durable; startup can remove the stopped container.
            return receipt

    async def _verify_command_receipt(self, inv, receipt):
        if receipt.status != 'succeeded':
            return receipt
        checks = inv.call.arguments.get('checks', [])
        if checks and inv.workspace_id != self.workspace.identity:
            return receipt.model_copy(update={'status': 'uncertain', 'evidence': dict(receipt.evidence,
                verification='failed', reason='The original workspace is unavailable for artifact verification.')})
        artifacts, checks_ok = [], True
        for check in checks:
            try:
                observed = await asyncio.to_thread(self.workspace.inspect, check['path'], check.get('expected_sha256'))
                artifacts.append(Artifact(**{k: observed[k] for k in ('path', 'sha256', 'size_bytes')}))
                checks_ok &= observed['verified']
            except (InvalidRequest, OSError):
                checks_ok = False
        return receipt.model_copy(update={'artifacts': tuple(artifacts), 'status': 'succeeded' if checks_ok else 'failed',
            'evidence': dict(receipt.evidence, verification='passed' if checks_ok else 'failed')})

    async def cancel(self, run_id):
        await self._reconcile(run_id, cancellation=True)

    async def reconcile(self):
        return await self._reconcile(None, cancellation=False)

    async def _reconcile(self, run_id, cancellation):
        from hyperclaw.execution.docker import DockerUnavailable
        invocations = await self.store.invocations(run_id)
        commands = [i for i in invocations if i.call.name == 'command' or i.call.name.startswith('mcp_docs_')]
        receipts = []
        # Enumerate even empty state: a process may have died after create before ID commit.
        try:
            owned = await self.backend.owned() if commands else []
        except DockerUnavailable:
            owned = None
        by_invocation = {i.id: i for i in invocations}
        by_container = {c['invocation_id']: c for c in owned or []}
        for inv in invocations:
            if inv.status == 'waiting_approval' and inv.id not in by_container:
                continue
            if inv.status == 'prepared' and inv.id not in by_container:
                parent = await self.store.get_run(inv.run_id)
                if parent.status != 'running' and parent.status not in TERMINAL:
                    continue  # Queued approvals and pending decisions still own this intent.
                receipts.append(await self.store.complete_invocation(ToolReceipt(
                    invocation_id=inv.id,
                    status='cancelled' if cancellation else 'interrupted',
                    evidence={'reason': 'Dispatch did not start before the owner stopped.'},
                )))
                continue
            container = by_container.get(inv.id)
            if inv.call.name == 'command' or inv.call.name.startswith('mcp_docs_'):
                cid = container['id'] if container else inv.container_id
                if cid and owned is not None and container:
                    try:
                        if not inv.container_id:
                            await self.store.bind_container(inv.id, cid)
                            inv = await self.store.get_invocation(inv.id)
                        result = await self.backend.terminate(cid)
                        if inv.call.name.startswith('mcp_docs_'):
                            receipt = ToolReceipt(invocation_id=inv.id, status=('cancelled' if cancellation else 'interrupted') if result.terminated else 'uncertain',
                                evidence={'container_id': cid, 'terminated': result.terminated, 'reason': 'MCP result was not durably recorded; protocol logs are not receipts.'})
                        else:
                            receipt = self._container_receipt(inv, result, cancelled=cancellation)
                            receipt = await self._verify_command_receipt(inv, receipt)
                        if not cancellation and container['state'] not in {'exited', 'dead'}:
                            receipt = receipt.model_copy(update={'status': 'interrupted' if result.terminated else 'uncertain'})
                        if not inv.receipt:
                            receipts.append(await self.store.complete_invocation(receipt))
                        if result.terminated:
                            await self.backend.remove(cid)
                    except DockerUnavailable:
                        if not inv.receipt:
                            receipts.append(await self.store.complete_invocation(ToolReceipt(invocation_id=inv.id, status='uncertain', evidence={'reason':'Termination could not be established.'})))
                elif not inv.receipt:
                    receipts.append(await self.store.complete_invocation(ToolReceipt(invocation_id=inv.id, status='uncertain', evidence={'reason':'Container outcome unavailable; dispatch is never replayed.'})))
            elif not inv.receipt and inv.status == 'running':
                receipts.append(await self.store.complete_invocation(ToolReceipt(invocation_id=inv.id, status='uncertain', evidence={'reason':'Host operation has no durable receipt.'})))
        # Orphaned containers belong to this installation but have no corresponding intent.
        # Stop them before admitting work; never start or replay them.
        for container in owned or []:
            if container['invocation_id'] not in by_invocation and run_id is None:
                result = await self.backend.terminate(container['id'])
                if not result.terminated:
                    raise DockerUnavailable('Cannot establish orphan container termination.')
                await self.backend.remove(container['id'])
        return receipts

    async def close(self):
        self.workspace.close()
