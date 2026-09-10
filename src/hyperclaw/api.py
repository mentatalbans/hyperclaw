"""Authenticated loopback HTTP adapter; lifespan owns the only runtime."""
from contextlib import asynccontextmanager
import hmac
import json
import logging
import os
from uuid import uuid4
from typing import Literal
from pydantic import Field

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from hyperclaw.config import Settings, initialize_root, read_token
from hyperclaw.contracts import (
    Conflict, Generation, InvalidRequest, NotFound, RootInUse, RunRequest,
    RuntimeErrorBase, ScheduleRequest, ScheduleRetarget, StorageFailure, Value,
)
from hyperclaw.ollama import Ollama
from hyperclaw.runtime import Runtime
from hyperclaw.store import Store


class ResetRequest(Value):
    generation: Generation


class ApprovalDecision(Value):
    approved: bool = Field(strict=True)
    arguments_sha256: str
    policy_sha256: str


class GrantRequest(Value):
    workspace_id: str
    capability: Literal['write', 'execute']


def create_app(settings: Settings) -> FastAPI:
    instance = uuid4().hex
    url = f'http://127.0.0.1:{settings.port}'
    metadata_path = settings.root / 'daemon.json'
    hosts = {f'127.0.0.1:{settings.port}'}
    origins = {url}
    if settings.port == 80:
        hosts.add('127.0.0.1')
        origins.add('http://127.0.0.1')

    @asynccontextmanager
    async def lifespan(app):
        store = model = runtime = None
        published = False
        try:
            initialize_root(settings)
            token = read_token(settings.root)
            store = await Store.open(settings.root)
            model = Ollama(settings)
            runtime = Runtime(store, model, settings)
            await runtime.start()
            app.state.runtime = runtime
            app.state.token = token
            temporary = settings.root / f'daemon-{instance}.tmp'
            try:
                temporary.write_text(json.dumps({'url': url, 'instance_id': instance, 'pid': os.getpid()}) + '\n')
                temporary.replace(metadata_path)
            finally:
                temporary.unlink(missing_ok=True)
            published = True
            yield
        except RuntimeErrorBase as exc:
            logging.getLogger(__name__).error('%s: %s', exc.code, exc.message)
            raise
        finally:
            # Remove only our metadata, while the root lock is still held.
            if published:
                try:
                    metadata = json.loads(metadata_path.read_text())
                    if isinstance(metadata, dict) and metadata.get('instance_id') == instance:
                        metadata_path.unlink()
                except (OSError, ValueError):
                    pass
            if runtime is not None:
                await runtime.close()
            else:
                if model is not None:
                    await model.aclose()
                if store is not None:
                    await store.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def boundary(request, call_next):
        if request.headers.get('host') not in hosts:
            return JSONResponse({'error': {'code': 'invalid_host', 'message': 'Invalid local host.'}}, status_code=400)
        if request.headers.get('origin') is not None and request.headers['origin'] not in origins:
            return JSONResponse({'error': {'code': 'invalid_origin', 'message': 'Cross-origin requests are disabled.'}}, status_code=403)
        if request.url.path != '/healthz':
            supplied = request.headers.get('authorization', '')
            expected = 'Bearer ' + app.state.token
            if not hmac.compare_digest(supplied.encode(), expected.encode()):
                return JSONResponse({'error': {'code': 'unauthorized', 'message': 'Operator bearer token required.'}}, status_code=401)
        return await call_next(request)

    @app.exception_handler(RuntimeErrorBase)
    async def domain_error(request, exc):
        status = 404 if isinstance(exc, NotFound) else 409 if isinstance(exc, (Conflict, RootInUse)) else 422 if isinstance(exc, InvalidRequest) else 503
        return JSONResponse({'error': {'code': exc.code, 'message': exc.message}}, status_code=status)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        return JSONResponse({'error': {'code': 'invalid_request', 'message': 'Invalid request fields.'}}, status_code=422)

    @app.get('/healthz')
    async def health():
        if not app.state.runtime.healthy:
            return JSONResponse({'status': 'unavailable'}, status_code=503)
        return {'status': 'ok'}

    @app.post('/v1/sessions')
    async def create_session():
        return await app.state.runtime.create_session()

    @app.get('/v1/sessions/{session_id}')
    async def get_session(session_id: str):
        return await app.state.runtime.get_session(session_id)

    @app.post('/v1/sessions/{session_id}/reset')
    async def reset_session(session_id: str, body: ResetRequest):
        return await app.state.runtime.reset_session(session_id, body.generation)

    @app.post('/v1/runs', status_code=202)
    async def submit(body: RunRequest):
        return await app.state.runtime.submit(body)

    @app.post('/v1/schedules')
    async def create_schedule(body: ScheduleRequest):
        return await app.state.runtime.create_schedule(body)

    @app.get('/v1/schedules')
    async def schedules():
        return await app.state.runtime.schedules()

    @app.get('/v1/schedules/{schedule_id}')
    async def get_schedule(schedule_id: str):
        return await app.state.runtime.get_schedule(schedule_id)

    @app.post('/v1/schedules/{schedule_id}/pause')
    async def pause_schedule(schedule_id: str):
        return await app.state.runtime.pause_schedule(schedule_id)

    @app.post('/v1/schedules/{schedule_id}/retarget')
    async def retarget_schedule(schedule_id: str, body: ScheduleRetarget):
        return await app.state.runtime.retarget_schedule(
            schedule_id,
            body.expected_generation,
            body.generation,
        )

    @app.get('/v1/schedules/{schedule_id}/occurrences')
    async def schedule_occurrences(schedule_id: str):
        return await app.state.runtime.schedule_occurrences(schedule_id)

    @app.get('/v1/runs/{run_id}')
    async def get_run(run_id: str):
        return await app.state.runtime.get_run(run_id)

    @app.post('/v1/runs/{run_id}/cancel')
    async def cancel(run_id: str):
        return await app.state.runtime.cancel(run_id)

    @app.get('/v1/approvals')
    async def approvals():
        return await app.state.runtime.approvals()

    @app.post('/v1/approvals/{approval_id}/decision')
    async def decide_approval(approval_id: str, body: ApprovalDecision):
        return await app.state.runtime.decide_approval(approval_id, body.approved, body.arguments_sha256, body.policy_sha256)

    @app.get('/v1/workspace')
    async def workspace():
        return await app.state.runtime.workspace()

    @app.post('/v1/grants')
    async def grant(body: GrantRequest):
        return await app.state.runtime.grant(body.workspace_id, body.capability)

    @app.get('/v1/runs/{run_id}/receipts')
    async def receipts(run_id: str):
        return await app.state.runtime.receipts(run_id)

    @app.get('/v1/runs/{run_id}/events')
    async def events(run_id: str, after: str = '0'):
        if not after.isascii() or not after.isdecimal():
            raise Conflict('invalid_cursor', 'Event cursor must be a committed sequence or zero.')
        try:
            cursor = int(after)
        except ValueError:
            raise Conflict('invalid_cursor', 'Invalid event cursor.') from None
        runtime = app.state.runtime
        # All errors that promise an HTTP status occur before stream headers.
        await runtime.get_run(run_id)
        await runtime.store.events(run_id, after=cursor, limit=1)
        async def stream():
            async for event in runtime.events(run_id, after=cursor):
                yield f'id: {event.seq}\nevent: {event.kind}\ndata: {event.model_dump_json()}\n\n'
        return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache'})

    return app
