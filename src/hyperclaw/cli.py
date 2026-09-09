"""One explicit CLI. Commands never load legacy configuration."""
import base64
from enum import Enum
import json
from pathlib import Path
import sqlite3
import sys

import httpx
import typer

from hyperclaw.config import initialize_root, load_settings
from hyperclaw.contracts import ImageAttachment, RuntimeErrorBase

app = typer.Typer(no_args_is_help=True)


@app.callback()
def options(ctx: typer.Context, root: Path | None = typer.Option(None),
            model: str | None = typer.Option(None), ollama_url: str | None = typer.Option(None)):
    ctx.obj = load_settings(root=root, overrides={k: v for k, v in {
        'model': model, 'ollama_url': ollama_url}.items() if v is not None})


@app.command('init')
def initialize(ctx: typer.Context):
    initialize_root(ctx.obj)
    typer.echo(f'Initialized runtime root: {ctx.obj.root}')


@app.command()
def doctor(ctx: typer.Context, probe: bool = False):
    settings = ctx.obj
    typer.echo(json.dumps(settings.model_dump(mode='json'), indent=2))
    with sqlite3.connect(':memory:') as db:
        try:
            db.execute('CREATE VIRTUAL TABLE probe USING fts5(text)')
            fts = True
        except sqlite3.OperationalError:
            fts = False
    typer.echo(f'Python {sys.version.split()[0]}; SQLite {sqlite3.sqlite_version}; FTS5: {fts}')
    if probe:
        try:
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=min(10, settings.request_timeout_s)) as client:
                response = client.get(settings.ollama_url + '/api/tags')
                response.raise_for_status()
                models = response.json()['models']
                if not any(m.get('name') == settings.model or m.get('model') == settings.model for m in models):
                    raise ValueError('model unavailable')
            typer.echo('Configured model is listed. Chat/thinking capabilities require live tests.')
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            typer.echo('Configured Ollama endpoint/model is unavailable. No model was downloaded.', err=True)
            raise typer.Exit(1) from None


def main():
    try:
        app()
    except RuntimeErrorBase as exc:
        typer.echo(f'{exc.code}: {exc.message}', err=True)
        raise SystemExit(1) from None


@app.command()
def serve(ctx: typer.Context, port: int | None = typer.Option(None, min=0, max=65535)):
    import socket
    import uvicorn
    from hyperclaw.api import create_app

    settings = ctx.obj
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(('127.0.0.1', settings.port if port is None else port))
        except OSError:
            typer.echo('Cannot bind the selected loopback port.', err=True)
            raise typer.Exit(1) from None
        settings = settings.model_copy(update={'port': listener.getsockname()[1]})
        server = uvicorn.Server(uvicorn.Config(create_app(settings), access_log=False, timeout_graceful_shutdown=2))
        server.run(sockets=[listener])
        if not server.started:
            raise typer.Exit(1)


from contextlib import contextmanager
from urllib.parse import urlsplit, quote
from uuid import uuid4

from hyperclaw.config import read_token
from hyperclaw.contracts import InvalidRequest


MAX_IMAGE_BYTES = 6 * 1024 * 1024


class ToolName(str, Enum):
    workspace_read = 'workspace_read'
    workspace_list = 'workspace_list'
    workspace_search = 'workspace_search'
    workspace_write = 'workspace_write'
    command = 'command'


class Capability(str, Enum):
    write = 'write'
    execute = 'execute'


@contextmanager
def daemon_client(settings):
    client = None
    try:
        metadata = json.loads((settings.root / 'daemon.json').read_text())
        url = metadata['url']
        parsed = urlsplit(url)
        if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port
                or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise ValueError('Invalid daemon endpoint')
        client = httpx.Client(base_url=url, trust_env=False, follow_redirects=False, timeout=10)
        health = client.get('/healthz')
        if health.status_code != 200 or health.json() != {'status': 'ok'}:
            raise ValueError('Daemon unavailable')
        client.headers['Authorization'] = 'Bearer ' + read_token(settings.root)
    except (OSError, ValueError, KeyError, TypeError, httpx.HTTPError):
        if client is not None:
            client.close()
        raise InvalidRequest('daemon_unavailable', f'No reachable daemon. Run: hyperclaw --root {settings.root} serve') from None
    try:
        yield client
    except httpx.HTTPError:
        raise InvalidRequest('daemon_connection', 'Daemon connection failed. Inspect the run before resubmitting.') from None
    finally:
        client.close()


def response_json(response):
    if response.is_error:
        try:
            error = response.json()['error']
            raise InvalidRequest(error['code'], error['message'])
        except (ValueError, KeyError, TypeError):
            raise InvalidRequest('http_error', f'Daemon returned HTTP {response.status_code}.') from None
    return response.json()


def run_path(run_id):
    return '/v1/runs/' + quote(run_id, safe='')


def image_attachment(path: Path) -> dict:
    try:
        with path.open('rb') as source:
            raw = source.read(MAX_IMAGE_BYTES + 1)
    except OSError:
        raise InvalidRequest('invalid_image', f'Cannot read image: {path}') from None
    if len(raw) > MAX_IMAGE_BYTES:
        raise InvalidRequest('invalid_image', 'Image exceeds the bounded local file limit.')
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        media_type = 'image/png'
    elif raw.startswith(b'\xff\xd8\xff'):
        media_type = 'image/jpeg'
    elif raw.startswith((b'GIF87a', b'GIF89a')):
        media_type = 'image/gif'
    elif raw.startswith(b'RIFF') and raw[8:12] == b'WEBP':
        media_type = 'image/webp'
    else:
        raise InvalidRequest('invalid_image', 'Image must contain PNG, JPEG, GIF, or WebP bytes.')
    try:
        attachment = ImageAttachment(media_type=media_type, data=base64.b64encode(raw).decode('ascii'))
    except ValueError:
        raise InvalidRequest('invalid_image', 'Image bytes are invalid or exceed the attachment limit.') from None
    return attachment.model_dump(mode='json')


def read_events(client, run_id, after=0):
    with client.stream('GET', run_path(run_id) + '/events', params={'after': after}, timeout=None) as response:
        if response.is_error:
            response.read()
            response_json(response)
        data = []
        for line in response.iter_lines():
            if not line:
                if data:
                    yield json.loads('\n'.join(data))
                data = []
            elif line.startswith('data:'):
                data.append(line[5:].lstrip())
        if data:
            raise InvalidRequest('incomplete_events', 'Event connection ended inside a frame. Reconnect using the last sequence.')


@app.command()
def chat(ctx: typer.Context, text: str, session: str | None = None, detach: bool = False,
         request_id: str | None = None, retry_of: str | None = None,
         image: list[Path] = typer.Option([], '--image'),
         context_bytes: int = typer.Option(65_536, min=65_536, max=8 * 1024 * 1024),
         tool: list[ToolName] = typer.Option([], '--tool'),
         no_tools: bool = typer.Option(False, '--no-tools')):
    if no_tools and tool:
        raise InvalidRequest('tool_selection', 'Choose explicit --tool values or --no-tools, not both.')
    if len(image) > 4:
        raise InvalidRequest('invalid_image', 'At most four images may be attached.')
    attachments = [image_attachment(path) for path in image]
    selected_tools = [] if no_tools else [name.value for name in tool] if tool else None
    with daemon_client(ctx.obj) as client:
        conversation = response_json(client.get('/v1/sessions/' + quote(session, safe=''))) if session else response_json(client.post('/v1/sessions', json={}))
        body = {
            'session_id': conversation['id'], 'generation': conversation['generation'],
            'request_id': request_id or uuid4().hex, 'text': text, 'retry_of': retry_of,
            'context_bytes': context_bytes,
        }
        if attachments:
            body['images'] = attachments
        if selected_tools is not None:
            body['tools'] = selected_tools
        run = response_json(client.post('/v1/runs', json=body))
        run_id = run['id']
        if detach:
            typer.echo(run_id)
            return
        typer.echo(f"Session {conversation['id']}; run {run_id}", err=True)
        try:
            for event in read_events(client, run_id):
                if event['kind'] == 'model.text':
                    typer.echo(event['data']['text'], nl=False)
                elif event['kind'] == 'approval.required':
                    typer.echo('Approval required for this exact pending action:', err=True)
                    typer.echo(json.dumps(event['data'], indent=2), err=True)
                    typer.echo(f'Detached. Run {run_id} is waiting; review it with approval list.', err=True)
                    return
                elif event['kind'] == 'run.finished':
                    typer.echo()
                    if event['data']['status'] != 'succeeded':
                        result = response_json(client.get(run_path(run_id)))
                        failure = result['error']
                        typer.echo(f"Run {run_id}: {result['status']}" + (f" ({failure['code']}: {failure['message']})" if failure else ''), err=True)
                        raise typer.Exit(1)
        except KeyboardInterrupt:
            typer.echo(f'\nDetached. Run {run_id} continues; use run inspect or run cancel.', err=True)


run_app = typer.Typer(no_args_is_help=True)
session_app = typer.Typer(no_args_is_help=True)
approval_app = typer.Typer(no_args_is_help=True)
app.add_typer(run_app, name='run')
app.add_typer(session_app, name='session')
app.add_typer(approval_app, name='approval')


@run_app.command('inspect')
def inspect_run(ctx: typer.Context, run_id: str):
    with daemon_client(ctx.obj) as client:
        typer.echo(json.dumps(response_json(client.get(run_path(run_id))), indent=2))


@run_app.command('cancel')
def cancel_run(ctx: typer.Context, run_id: str):
    with daemon_client(ctx.obj) as client:
        typer.echo(json.dumps(response_json(client.post(run_path(run_id) + '/cancel')), indent=2))


@run_app.command('events')
def run_events(ctx: typer.Context, run_id: str, after: int = typer.Option(0, min=0)):
    with daemon_client(ctx.obj) as client:
        try:
            for event in read_events(client, run_id, after):
                typer.echo(json.dumps(event))
        except KeyboardInterrupt:
            typer.echo(f'Detached from run {run_id}.', err=True)


@run_app.command('receipts')
def run_receipts(ctx: typer.Context, run_id: str):
    with daemon_client(ctx.obj) as client:
        typer.echo(json.dumps(response_json(client.get(run_path(run_id) + '/receipts')), indent=2))


@approval_app.command('list')
def list_approvals(ctx: typer.Context):
    with daemon_client(ctx.obj) as client:
        typer.echo(json.dumps(response_json(client.get('/v1/approvals')), indent=2))


def decide_approval(ctx: typer.Context, approval_id: str, approved: bool,
                    arguments_sha256: str, policy_sha256: str):
    path = '/v1/approvals/' + quote(approval_id, safe='') + '/decision'
    with daemon_client(ctx.obj) as client:
        result = response_json(client.post(path, json={
            'approved': approved,
            'arguments_sha256': arguments_sha256,
            'policy_sha256': policy_sha256,
        }))
        typer.echo(json.dumps(result, indent=2))


@approval_app.command('approve')
def approve(ctx: typer.Context, approval_id: str,
            arguments_sha256: str = typer.Option(..., '--arguments-sha256'),
            policy_sha256: str = typer.Option(..., '--policy-sha256')):
    decide_approval(ctx, approval_id, True, arguments_sha256, policy_sha256)


@approval_app.command('deny')
def deny(ctx: typer.Context, approval_id: str,
         arguments_sha256: str = typer.Option(..., '--arguments-sha256'),
         policy_sha256: str = typer.Option(..., '--policy-sha256')):
    decide_approval(ctx, approval_id, False, arguments_sha256, policy_sha256)


@app.command()
def grant(ctx: typer.Context, capability: Capability,
          workspace_id: str = typer.Option(..., '--workspace-id')):
    with daemon_client(ctx.obj) as client:
        result = response_json(client.post('/v1/grants', json={
            'workspace_id': workspace_id,
            'capability': capability.value,
        }))
        typer.echo(json.dumps(result, indent=2))


@app.command()
def workspace(ctx: typer.Context):
    with daemon_client(ctx.obj) as client:
        typer.echo(json.dumps(response_json(client.get('/v1/workspace')), indent=2))


@session_app.command('reset')
def reset_session(ctx: typer.Context, session_id: str):
    with daemon_client(ctx.obj) as client:
        path = '/v1/sessions/' + quote(session_id, safe='')
        session = response_json(client.get(path))
        typer.echo(json.dumps(response_json(client.post(path + '/reset', json={'generation': session['generation']})), indent=2))
