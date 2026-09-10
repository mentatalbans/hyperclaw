"""Authenticated loopback HTTP adapter; lifespan owns the only runtime."""
from contextlib import asynccontextmanager
import hmac
from importlib.resources import files
import json
import logging
import os
from uuid import uuid4
from typing import Annotated, Literal
from pydantic import Field

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse

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


class McpAdmission(Value):
    expected_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class SkillAdmission(Value):
    content_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


def create_app(settings: Settings) -> FastAPI:
    instance = uuid4().hex
    url = f'http://127.0.0.1:{settings.port}'
    metadata_path = settings.root / 'daemon.json'
    hosts = {f'127.0.0.1:{settings.port}'}
    origins = {url}
    public_web = {'/': ('index.html', 'text/html; charset=utf-8'),
                  '/web/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                  '/web/style.css': ('style.css', 'text/css; charset=utf-8')}
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
        is_public_web = request.method in {'GET', 'HEAD'} and request.url.path in public_web
        if request.url.path != '/healthz' and not is_public_web:
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
        if any(error['type'] == 'mcp_schedule_unsupported' for error in exc.errors()):
            return JSONResponse({'error': {'code': 'mcp_schedule_unsupported',
                'message': 'MCP tools require a directly submitted run; scheduled admission binding is unavailable.'}}, status_code=422)
        return JSONResponse({'error': {'code': 'invalid_request', 'message': 'Invalid request fields.'}}, status_code=422)

    @app.get('/healthz')
    async def health():
        if not app.state.runtime.healthy:
            return JSONResponse({'status': 'unavailable'}, status_code=503)
        return {'status': 'ok'}

    @app.api_route('/', methods=['GET', 'HEAD'])
    @app.api_route('/web/app.js', methods=['GET', 'HEAD'])
    @app.api_route('/web/style.css', methods=['GET', 'HEAD'])
    async def web_asset(request: Request):
        name, media_type = public_web[request.url.path]
        content = files('hyperclaw').joinpath('web', name).read_bytes()
        return Response(content=content, media_type=media_type, headers={
            'Cache-Control': 'no-store',
            'Content-Security-Policy': (
                "default-src 'none'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                "form-action 'self'"
            ),
            'Referrer-Policy': 'no-referrer',
            'X-Content-Type-Options': 'nosniff',
        })

    @app.post('/v1/sessions')
    async def create_session():
        return await app.state.runtime.create_session()

    @app.get('/v1/sessions')
    async def sessions(
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        before: str | None = None,
    ):
        return await app.state.runtime.sessions(limit=limit, before=before)

    @app.get('/v1/sessions/{session_id}')
    async def get_session(session_id: str):
        return await app.state.runtime.get_session(session_id)

    @app.get('/v1/sessions/{session_id}/runs')
    async def session_runs(
        session_id: str,
        generation: Annotated[int | None, Query(ge=0)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        before: str | None = None,
    ):
        return await app.state.runtime.session_runs(
            session_id, generation=generation, limit=limit, before=before,
        )

    @app.post('/v1/sessions/{session_id}/reset')
    async def reset_session(session_id: str, body: ResetRequest):
        return await app.state.runtime.reset_session(session_id, body.generation)

    @app.post('/v1/runs', status_code=202)
    async def submit(body: RunRequest):
        return await app.state.runtime.submit(body)

    @app.get('/v1/mcp')
    async def inspect_mcp():
        result = await app.state.runtime.executor.mcp.inspect_admission()
        admission = await app.state.runtime.store.mcp_admission()
        return result | {'admission': admission}

    @app.post('/v1/mcp/admit')
    async def admit_mcp(body: McpAdmission):
        return await app.state.runtime.executor.mcp.admit(body.expected_sha256)

    @app.delete('/v1/mcp/admission')
    async def revoke_mcp():
        return await app.state.runtime.executor.mcp.revoke()

    @app.get('/v1/skills')
    async def skills():
        return await app.state.runtime.list_skills()

    @app.get('/v1/skills/{name}')
    async def preview_skill(name: str):
        return await app.state.runtime.preview_skill(name)

    @app.post('/v1/skills/{name}/admit')
    async def admit_skill(name: str, body: SkillAdmission):
        return await app.state.runtime.admit_skill(name, body.content_hash)

    @app.delete('/v1/skills/{name}/admission')
    async def revoke_skill(name: str):
        return await app.state.runtime.revoke_skill(name)

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
