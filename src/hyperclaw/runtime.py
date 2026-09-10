"""One durable chat worker; observers never own model execution."""
import asyncio
from datetime import datetime, timezone
import hashlib
import logging

from hyperclaw.contracts import (
    CONTEXT_BYTES, Failure, Message, ProviderFailure, StorageFailure, TERMINAL,
    canonical, message_bytes, ApprovalRequired, Checkpoint, Conflict, InvalidRequest, RuntimeErrorBase, ToolCall,
)

from hyperclaw.execution import Executor
from hyperclaw.scheduling import Scheduler
from hyperclaw.skills import Skills
from hyperclaw.skill_format import render_skill_instructions

log = logging.getLogger(__name__)
MAINTENANCE_INTERVAL_S = .1


class Runtime:
    def __init__(self, store, ollama, settings):
        self.store, self.ollama, self.settings = store, ollama, settings
        self.skills = Skills(settings.root / 'skills')
        self._wake = asyncio.Event()
        self._maintenance_wake = asyncio.Event()
        self._changed = asyncio.Event()
        self._control = asyncio.Lock()
        self._worker = None
        self._maintenance = None
        self._active = None
        self._active_task = None
        self._completion = None
        self._cancel_status = None
        self._closing = False
        self._close_task = None
        self._started = False
        self.healthy = True
        self.executor = None

    async def start(self):
        if self._started:
            return
        try:
            self.executor = await Executor.open(self.store, self.settings)
            await self.executor.reconcile()
            await self.store.expire_approvals()
            await self.store.recover()
            self._started = True
            self._worker = asyncio.create_task(self._work(), name='hyperclaw-worker')
            self._maintenance = asyncio.create_task(self._maintain(), name='hyperclaw-maintenance')
        except BaseException:
            await self.close()
            raise

    def _accepting(self):
        if not self._started or self._closing or not self.healthy:
            raise StorageFailure()

    async def create_session(self):
        self._accepting()
        return await self.store.create_session()

    async def get_session(self, session_id):
        return await self.store.get_session(session_id)

    async def sessions(self, limit=50, before=None):
        return await self.store.sessions(limit=limit, before=before)

    async def reset_session(self, session_id, generation):
        self._accepting()
        return await self.store.reset_session(session_id, generation)

    async def submit(self, request):
        self._accepting()
        try:
            documents = []
            for name in request.skills:
                document = self.skills.load(name)
                documents.append(await self.store.admitted_skill(name, document.content_hash))
            instructions = self._skill_instructions(documents)
            if len(instructions.encode('utf-8')) + len(message_bytes([request.current_message()])) > request.context_bytes:
                raise InvalidRequest('context_limit', 'Selected skill instructions exceed the context budget.')
            from hyperclaw.contracts import MCP_TOOLS
            manifest = await self.executor.mcp.current() if set(request.tools) & set(MCP_TOOLS) else None
            if manifest:
                from hyperclaw.mcp import require_sdk
                require_sdk()
            return await self.store.submit(
                request, mcp=manifest, skill_hashes={document.name: document.content_hash for document in documents}
            )
        finally:
            # Store settles an accepted transaction even if its caller disconnects.
            self._wake.set()
            self._changed.set()

    async def create_schedule(self, request):
        self._accepting()
        try:
            return await self.store.create_schedule(request)
        finally:
            self._maintenance_wake.set()

    async def schedules(self):
        return await self.store.schedules()

    async def get_schedule(self, schedule_id):
        return await self.store.get_schedule(schedule_id)

    async def schedule_occurrences(self, schedule_id):
        return await self.store.schedule_occurrences(schedule_id)

    async def pause_schedule(self, schedule_id):
        return await self.store.pause_schedule(schedule_id)

    async def retarget_schedule(self, schedule_id, expected_generation, generation):
        self._accepting()
        try:
            return await self.store.retarget_schedule(schedule_id, expected_generation, generation)
        finally:
            self._maintenance_wake.set()

    async def get_run(self, run_id):
        return await self.store.get_run(run_id)

    async def session_runs(self, session_id, generation=None, limit=50, before=None):
        return await self.store.session_runs(
            session_id, generation=generation, limit=limit, before=before,
        )

    async def cancel(self, run_id):
        async with self._control:
            run = await self.store.get_run(run_id)
            if run.status in TERMINAL:
                return run
            if run.status in {'queued', 'waiting_approval'}:
                result = await self.store.finish(run_id, 'cancelled')
                self._changed.set()
                return result
            if self._active is None or self._active.id != run_id or self._active_task is None:
                raise StorageFailure()
            task = self._active_task
            completion = self._completion
            if self._cancel_status is None:
                self._cancel_status = 'cancelled'
                task.cancel()
        await completion.wait()
        result = await self.store.get_run(run_id)
        if result.status not in TERMINAL:
            raise StorageFailure()
        return result

    async def close(self):
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
        self._close_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _close(self):
        self._closing = True
        async with self._control:
            if self._active_task and not self._active_task.done() and self._cancel_status is None:
                self._cancel_status = 'interrupted'
                self._active_task.cancel()
            self._wake.set()
            self._maintenance_wake.set()
        try:
            tasks = [task for task in (self._worker, self._maintenance) if task is not None]
            if tasks:
                await asyncio.gather(*tasks)
        finally:
            try:
                await self.ollama.aclose()
            finally:
                if self.executor is not None:
                    await self.executor.close()
                await self.store.close()
                self._changed.set()

    async def _work(self):
        try:
            while not self._closing and self.healthy:
                self._wake.clear()
                async with self._control:
                    if self._closing or not self.healthy:
                        return
                    run = await self.store.next_run()
                    if run:
                        self._active = run
                        self._cancel_status = None
                        self._completion = asyncio.Event()
                        self._active_task = asyncio.create_task(self._execute(run))
                if run is None:
                    try:
                        await asyncio.wait_for(self._wake.wait(), 1)
                    except TimeoutError:
                        pass
                    continue
                self._changed.set()
                try:
                    await self._active_task
                except asyncio.CancelledError:
                    # Cancellation can arrive before the task's first instruction.
                    await self.executor.cancel(run.id)
                    receipts = await self.receipts(run.id)
                    terminal = 'uncertain' if any(r.status == 'uncertain' for r in receipts) else self._cancel_status or 'interrupted'
                    await self.store.finish(run.id, terminal)
                    self._changed.set()
                finally:
                    self._completion.set()
                    self._active = None
                    self._active_task = None
        except Exception:
            # Storage failures are not model failures, and cannot establish success.
            self.healthy = False
            self._maintenance_wake.set()
            self._changed.set()
            log.error('Runtime worker stopped; storage or internal execution is unavailable.')

    async def _maintain(self):
        scheduler = Scheduler(self.store)
        try:
            while not self._closing and self.healthy:
                self._maintenance_wake.clear()
                expired = await self.store.expire_approvals()
                if self._closing or not self.healthy:
                    return
                run_ids = await scheduler.tick(datetime.now(timezone.utc))
                if expired or run_ids:
                    self._changed.set()
                if run_ids:
                    self._wake.set()
                try:
                    await asyncio.wait_for(self._maintenance_wake.wait(), MAINTENANCE_INTERVAL_S)
                except TimeoutError:
                    pass
        except Exception:
            self.healthy = False
            self._wake.set()
            self._changed.set()
            log.error('Runtime maintenance stopped; schedule and approval maintenance is unavailable.')

    async def _append(self, run_id, kind, data):
        await self.store.append(run_id, kind, data)
        self._changed.set()

    async def approvals(self):
        return await self.store.approvals()

    async def decide_approval(self, approval_id, approved, arguments_sha256, policy_sha256):
        self._accepting()
        async with self._control:
            result = await self.executor.decide_approval(approval_id, approved, arguments_sha256, policy_sha256)
            self._wake.set()
            self._changed.set()
            return result

    async def grant(self, workspace_id, capability):
        self._accepting()
        if workspace_id != self.executor.workspace.identity:
            raise Conflict('workspace_changed', 'Grant must select the current workspace identity.')
        return await self.store.grant(workspace_id, capability)

    async def receipts(self, run_id):
        return [i.receipt for i in await self.store.invocations(run_id) if i.receipt is not None]

    async def workspace(self):
        return {'id': self.executor.workspace.identity, 'path': str(self.executor.workspace.path),
                'grants': sorted(await self.store.grants(self.executor.workspace.identity))}

    async def list_skills(self):
        admissions = {document.name: document.content_hash
                      for document in await self.store.skill_admissions()}
        result = []
        for name in self.skills.names():
            document = self.skills.load(name)
            result.append(document.model_dump() | {
                'admitted': admissions.get(name) == document.content_hash,
            })
        return result

    async def preview_skill(self, name):
        return self.skills.load(name)

    async def admit_skill(self, name, content_hash):
        self._accepting()
        document = self.skills.load(name)
        if document.content_hash != content_hash:
            raise Conflict('skill_changed', 'Skill content changed since it was inspected.')
        return await self.store.admit_skill(document)

    async def revoke_skill(self, name):
        self._accepting()
        return await self.store.revoke_skill(name)

    @staticmethod
    def _skill_instructions(documents):
        try:
            return render_skill_instructions(documents)
        except ValueError:
            raise InvalidRequest('context_limit', 'Selected skill instructions exceed the supported snapshot bound.') from None

    async def _selected_documents(self, run):
        if set(run.request.skills) != set(run.skill_hashes):
            raise Conflict('skill_selection_changed', 'Run skill provenance is incomplete.')
        documents = []
        for name in run.request.skills:
            loaded = self.skills.load(name)
            if loaded.content_hash != run.skill_hashes[name]:
                raise Conflict('skill_changed', 'Skill content changed after this run was submitted.')
            documents.append(await self.store.admitted_skill(name, run.skill_hashes[name]))
        return documents

    async def _verify_skills(self, run, checkpoint):
        if checkpoint.mcp != run.mcp:
            raise Conflict('mcp_changed', 'Checkpoint MCP provenance changed.')
        if run.mcp:
            current = await self.executor.mcp.current()
            if current['sha256'] != run.mcp['sha256'] or current['admission_id'] != run.mcp['admission_id']:
                raise Conflict('mcp_changed', 'MCP admission changed after submission.')
        if checkpoint.skill_hashes != run.skill_hashes:
            raise Conflict('skill_selection_changed', 'Checkpoint skill provenance changed.')
        for name, content_hash in checkpoint.skill_hashes.items():
            await self.store.admitted_skill(name, content_hash)

    async def _context(self, run):
        documents = await self._selected_documents(run)
        instructions = self._skill_instructions(documents)
        instruction_bytes = instructions.encode('utf-8')
        history = await self.store.history_groups(run.request.session_id, run.request.generation)
        current = run.request.current_message()
        retained, count = [], 0
        for group in reversed(history):
            if len(instruction_bytes) + len(message_bytes(group + retained + [current])) <= run.request.context_bytes:
                retained = group + retained
                count += 1
        messages = retained + [current]
        encoded = message_bytes(messages)
        if len(instruction_bytes) + len(encoded) > run.request.context_bytes:
            raise InvalidRequest('context_limit', 'Selected skill instructions exceed the context budget.')
        combined = instruction_bytes + encoded
        provenance = {
            'sha256': hashlib.sha256(combined).hexdigest(), 'retained_turns': count,
            'serialized_bytes': len(combined), 'model': self.settings.model,
            'config_sha256': hashlib.sha256(canonical(self.settings.model_dump(mode='json')).encode()).hexdigest(),
            'tool_schema_sha256': hashlib.sha256(canonical(await self.executor.definitions(run.request.tools)).encode()).hexdigest(),
            'workspace_id': self.executor.workspace.identity,
        }
        if run.mcp:
            provenance['mcp'] = run.mcp
        if run.skill_hashes:
            provenance['skill_hashes'] = run.skill_hashes
        await self._append(run.id, 'run.context', provenance)
        return Checkpoint(messages=messages, history_length=len(retained), workspace_id=self.executor.workspace.identity,
                          skill_instructions=instructions, skill_hashes=run.skill_hashes, mcp=run.mcp,
                          context_sha256=hashlib.sha256(combined).hexdigest(), context_size=len(combined))

    async def _execute(self, run):
        status, error, output, verification = 'succeeded', None, '', 'not_requested'
        try:
            checkpoint = await self.store.load_checkpoint(run.id) or await self._context(run)
            if checkpoint.workspace_id != self.executor.workspace.identity:
                raise Conflict('workspace_changed', 'Selected workspace changed since this run was checkpointed.')
            remaining = self.settings.run_timeout_s - await self.store.elapsed(run.id)
            if remaining <= 0:
                raise TimeoutError
            async with asyncio.timeout(remaining):
                while True:
                    await self.store.save_checkpoint(run.id, checkpoint)
                    await self._verify_skills(run, checkpoint)
                    if checkpoint.pending_calls:
                        while checkpoint.pending_calls:
                            await self._verify_skills(run, checkpoint)
                            call = checkpoint.pending_calls[0]
                            receipt = await self.executor.invoke(run.id, call)
                            self._changed.set()
                            if receipt.status != 'succeeded':
                                status = receipt.status
                                verification = receipt.evidence.get('verification', 'not_requested')
                                error = Failure(code='tool_' + status, message=receipt.output or 'Tool execution did not complete successfully.')
                                break
                            result = {'type': 'tool_result', 'tool_use_id': call.id, 'content': receipt.model_dump_json()}
                            messages = list(checkpoint.messages)
                            if messages[-1].role == 'user' and isinstance(messages[-1].content, list) and all(b.get('type') == 'tool_result' for b in messages[-1].content):
                                messages[-1] = Message(role='user', content=messages[-1].content + [result])
                            else:
                                messages.append(Message(role='user', content=[result]))
                            checkpoint = checkpoint.model_copy(update={'messages': messages, 'pending_calls': checkpoint.pending_calls[1:]})
                            await self.store.save_checkpoint(run.id, checkpoint)
                        if status != 'succeeded':
                            break
                    if checkpoint.round_count >= 12:
                        raise InvalidRequest('tool_round_limit', 'Run reached the 12-round model limit.')
                    await self._verify_skills(run, checkpoint)
                    if (len(checkpoint.skill_instructions.encode('utf-8'))
                            + len(message_bytes(checkpoint.messages)) > run.request.context_bytes):
                        raise InvalidRequest('context_limit', 'Tool conversation exceeds the selected context budget.')
                    calls, content, text = await self._model_round(
                        run, checkpoint.messages, await self.executor.definitions(run.request.tools),
                        checkpoint.skill_instructions,
                    )
                    counts = dict(checkpoint.call_counts)
                    for call in calls:
                        key = hashlib.sha256(canonical({'name': call.name, 'arguments': call.arguments}).encode()).hexdigest()
                        counts[key] = counts.get(key, 0) + 1
                        if counts[key] > 3:
                            raise InvalidRequest('repeated_tool_call', 'An identical tool request exceeded three invocations.')
                    checkpoint = checkpoint.model_copy(update={'messages': checkpoint.messages + [Message(role='assistant', content=content)],
                        'pending_calls': calls, 'call_counts': counts, 'round_count': checkpoint.round_count + 1})
                    await self.store.save_checkpoint(run.id, checkpoint)
                    if not calls:
                        output = text
                        break
        except ApprovalRequired:
            self._changed.set()
            return
        except asyncio.CancelledError:
            await self.executor.cancel(run.id)
            status = self._cancel_status or 'interrupted'
            if status == 'interrupted':
                error = Failure(code='interrupted', message='Owner stopped before the response completed.')
        except TimeoutError:
            await self.executor.cancel(run.id)
            status, error = 'failed', Failure(code='run_timeout', message='Run exceeded its active execution deadline.')
        except StorageFailure:
            raise
        except RuntimeErrorBase as exc:
            status, error = 'failed', Failure(code=exc.code, message=exc.message)
        receipts = await self.receipts(run.id)
        if any(r.status == 'uncertain' for r in receipts):
            status = 'uncertain'
        if any(r.evidence.get('verification') == 'failed' for r in receipts):
            verification = 'failed'
        elif any(r.evidence.get('verification') == 'passed' for r in receipts):
            verification = 'passed'
        await self.store.finish(run.id, status, output=output if status == 'succeeded' else None, error=error, verification=verification)
        self._changed.set()

    async def _model_round(self, run, messages, definitions, system=''):
        parts = []
        buffer = ''
        buffer_kind = None
        buffer_bytes = 0
        flush_at = None
        stream = None
        pending = None
        finished = False
        calls, content = [], []
        response_bytes = 0

        async def flush():
            nonlocal buffer, buffer_bytes, flush_at
            if buffer:
                chunk = buffer
                buffer, buffer_bytes, flush_at = '', 0, None
                await self._append(run.id, 'model.' + buffer_kind, {'text': chunk})

        async def consume(event):
            nonlocal buffer, buffer_kind, buffer_bytes, flush_at, finished, content, response_bytes
            if event.kind in {'text', 'thinking'}:
                if buffer_kind != event.kind:
                    await flush()
                    buffer_kind = event.kind
                text = event.data['text']
                response_bytes += len(text.encode())
                if response_bytes > 1024 * 1024:
                    raise ProviderFailure('response_limit', 'Model response exceeds 1 MiB.')
                if event.kind == 'text':
                    parts.append(text)
                # Bound committed chunks by UTF-8 bytes, preserving code points.
                for char in text:
                    width = len(char.encode('utf-8'))
                    if buffer_bytes + width > 4096:
                        await flush()
                    if not buffer:
                        flush_at = asyncio.get_running_loop().time() + 0.05
                    buffer += char
                    buffer_bytes += width
                    if buffer_bytes == 4096:
                        await flush()
            elif event.kind == 'usage':
                await flush()
                await self._append(run.id, 'model.usage', event.data)
            elif event.kind == 'tool_call':
                calls.append(ToolCall.model_validate(event.data))
                if len(calls) > 16 or len({c.id for c in calls}) != len(calls):
                    raise ProviderFailure('invalid_tool_calls', 'Invalid or excessive tool calls.')
            elif event.kind == 'finish':
                await flush()
                content = event.data.get('content') or [{'type': 'text', 'text': ''.join(parts)}]
                finished = True

        try:
            stream = self.ollama.stream(messages, system=system, tools=definitions)
            while True:
                if pending is None:
                    pending = asyncio.create_task(anext(stream))
                delay = None if flush_at is None else max(0, flush_at - asyncio.get_running_loop().time())
                ready, _ = await asyncio.wait({pending}, timeout=delay)
                if not ready:
                    await flush()
                    continue
                completed, pending = pending, None
                try:
                    event = completed.result()
                except StopAsyncIteration:
                    break
                await consume(event)
            if not finished:
                raise ProviderFailure('incomplete_stream', 'Model response did not complete.')
        finally:
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            if stream is not None:
                await stream.aclose()
            await flush()
        return calls, content, ''.join(parts)

    async def events(self, run_id, after=0):
        # Durable rows are authoritative. Notifications only shorten bounded polling.
        while True:
            self._changed.clear()
            rows = await self.store.events(run_id, after=after)
            if rows:
                for event in rows:
                    after = event.seq
                    yield event
                    if event.kind == 'run.finished':
                        return
                continue
            run = await self.store.get_run(run_id)
            if run.status in TERMINAL:
                # A terminal commit may have raced with the first page query.
                rows = await self.store.events(run_id, after=after)
                if rows:
                    for event in rows:
                        after = event.seq
                        yield event
                    continue
                return
            if not self.healthy or self._closing:
                raise StorageFailure()
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=0.1)
            except TimeoutError:
                pass
