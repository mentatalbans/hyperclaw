"""One durable chat worker; observers never own model execution."""
import asyncio
import hashlib
import logging

from hyperclaw.contracts import (
    CONTEXT_BYTES, Failure, Message, ProviderFailure, StorageFailure, TERMINAL,
    canonical, message_bytes,
)

log = logging.getLogger(__name__)


class Runtime:
    def __init__(self, store, ollama, settings):
        self.store, self.ollama, self.settings = store, ollama, settings
        self._wake = asyncio.Event()
        self._changed = asyncio.Event()
        self._control = asyncio.Lock()
        self._worker = None
        self._active = None
        self._active_task = None
        self._completion = None
        self._cancel_status = None
        self._closing = False
        self._close_task = None
        self._started = False
        self.healthy = True

    async def start(self):
        if self._started:
            return
        try:
            await self.store.recover()
            self._started = True
            self._worker = asyncio.create_task(self._work(), name='hyperclaw-worker')
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

    async def reset_session(self, session_id, generation):
        self._accepting()
        return await self.store.reset_session(session_id, generation)

    async def submit(self, request):
        self._accepting()
        try:
            return await self.store.submit(request)
        finally:
            # Store settles an accepted transaction even if its caller disconnects.
            self._wake.set()
            self._changed.set()

    async def get_run(self, run_id):
        return await self.store.get_run(run_id)

    async def cancel(self, run_id):
        async with self._control:
            run = await self.store.get_run(run_id)
            if run.status in TERMINAL:
                return run
            if run.status == 'queued':
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
        try:
            if self._worker:
                await self._worker
        finally:
            try:
                await self.ollama.aclose()
            finally:
                await self.store.close()
                self._changed.set()

    async def _work(self):
        try:
            while not self._closing:
                self._wake.clear()
                async with self._control:
                    if self._closing:
                        return
                    run = await self.store.next_run()
                    if run:
                        self._active = run
                        self._cancel_status = None
                        self._completion = asyncio.Event()
                        self._active_task = asyncio.create_task(self._execute(run))
                if run is None:
                    await self._wake.wait()
                    continue
                self._changed.set()
                try:
                    await self._active_task
                except asyncio.CancelledError:
                    # Cancellation can arrive before the task's first instruction.
                    await self.store.finish(run.id, self._cancel_status or 'interrupted')
                    self._changed.set()
                finally:
                    self._completion.set()
                    self._active = None
                    self._active_task = None
        except Exception:
            # Storage failures are not model failures, and cannot establish success.
            self.healthy = False
            self._changed.set()
            log.error('Runtime worker stopped; storage or internal execution is unavailable.')

    async def _append(self, run_id, kind, data):
        await self.store.append(run_id, kind, data)
        self._changed.set()

    async def _context(self, run):
        history = await self.store.history(run.request.session_id, run.request.generation)
        current = Message(role='user', content=run.request.text)
        retained = []
        for index in range(len(history) - 2, -1, -2):
            group = history[index:index + 2]
            if len(message_bytes(group + retained + [current])) <= CONTEXT_BYTES:
                retained = group + retained
        messages = retained + [current]
        encoded = message_bytes(messages)
        await self._append(run.id, 'run.context', {
            'sha256': hashlib.sha256(encoded).hexdigest(), 'retained_turns': len(retained) // 2,
            'serialized_bytes': len(encoded), 'model': self.settings.model,
            'config_sha256': hashlib.sha256(canonical(self.settings.model_dump(mode='json')).encode()).hexdigest(),
        })
        return messages

    async def _execute(self, run):
        parts = []
        buffer = ''
        buffer_kind = None
        buffer_bytes = 0
        flush_at = None
        stream = None
        pending = None
        status, error = 'succeeded', None
        finished = False

        async def flush():
            nonlocal buffer, buffer_bytes, flush_at
            if buffer:
                chunk = buffer
                buffer, buffer_bytes, flush_at = '', 0, None
                await self._append(run.id, 'model.' + buffer_kind, {'text': chunk})

        async def consume(event):
            nonlocal buffer, buffer_kind, buffer_bytes, flush_at, finished
            if event.kind in {'text', 'thinking'}:
                if buffer_kind != event.kind:
                    await flush()
                    buffer_kind = event.kind
                text = event.data['text']
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
            elif event.kind == 'finish':
                await flush()
                finished = True

        try:
            async with asyncio.timeout(self.settings.run_timeout_s):
                messages = await self._context(run)
                stream = self.ollama.stream(messages)
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
        except asyncio.CancelledError:
            status = self._cancel_status or 'interrupted'
            if status == 'interrupted':
                error = Failure(code='interrupted', message='Owner stopped before the response completed.')
        except TimeoutError:
            status = 'failed'
            error = Failure(code='run_timeout', message='Run exceeded its execution deadline.')
        except ProviderFailure as exc:
            status = 'failed'
            error = Failure(code=exc.code, message=exc.message)
        finally:
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            if stream is not None:
                await stream.aclose()
        await flush()
        await self.store.finish(run.id, status, output=''.join(parts) if status == 'succeeded' else None, error=error)
        self._changed.set()

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
