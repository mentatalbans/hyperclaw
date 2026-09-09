"""Authenticated loopback HTTP adapter; lifespan owns the only runtime."""
from contextlib import asynccontextmanager
import hmac
import json
import logging
import os
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from hyperclaw.config import Settings, initialize_root, read_token
from hyperclaw.contracts import (
    Conflict, Generation, InvalidRequest, NotFound, RootInUse, RunRequest,
    RuntimeErrorBase, StorageFailure, Value,
)
from hyperclaw.ollama import Ollama
from hyperclaw.runtime import Runtime
from hyperclaw.store import Store


class ResetRequest(Value):
    generation: Generation


def create_app(settings: Settings) -> FastAPI:
    instance = uuid4().hex
    url = f'http://127.0.0.1:{settings.port}'
    metadata_path = settings.root / 'daemon.json'

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
                    if json.loads(metadata_path.read_text()).get('instance_id') == instance:
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
        if request.headers.get('host') != f'127.0.0.1:{settings.port}':
            return JSONResponse({'error': {'code': 'invalid_host', 'message': 'Invalid local host.'}}, status_code=400)
        if request.headers.get('origin') not in (None, url):
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

    @app.get('/v1/runs/{run_id}')
    async def get_run(run_id: str):
        return await app.state.runtime.get_run(run_id)

    @app.post('/v1/runs/{run_id}/cancel')
    async def cancel(run_id: str):
        return await app.state.runtime.cancel(run_id)

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
