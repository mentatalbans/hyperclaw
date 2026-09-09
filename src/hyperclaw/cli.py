"""One explicit CLI. Commands never load legacy configuration."""
import json
from pathlib import Path
import sqlite3
import sys

import httpx
import typer

from hyperclaw.config import initialize_root, load_settings
from hyperclaw.contracts import RuntimeErrorBase

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
         request_id: str | None = None, retry_of: str | None = None):
    with daemon_client(ctx.obj) as client:
        conversation = response_json(client.get('/v1/sessions/' + quote(session, safe=''))) if session else response_json(client.post('/v1/sessions', json={}))
        run = response_json(client.post('/v1/runs', json={
            'session_id': conversation['id'], 'generation': conversation['generation'],
            'request_id': request_id or uuid4().hex, 'text': text, 'retry_of': retry_of,
        }))
        run_id = run['id']
        if detach:
            typer.echo(run_id)
            return
        typer.echo(f"Session {conversation['id']}; run {run_id}", err=True)
        try:
            for event in read_events(client, run_id):
                if event['kind'] == 'model.text':
                    typer.echo(event['data']['text'], nl=False)
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
app.add_typer(run_app, name='run')
app.add_typer(session_app, name='session')


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


@session_app.command('reset')
def reset_session(ctx: typer.Context, session_id: str):
    with daemon_client(ctx.obj) as client:
        path = '/v1/sessions/' + quote(session_id, safe='')
        session = response_json(client.get(path))
        typer.echo(json.dumps(response_json(client.post(path + '/reset', json={'generation': session['generation']})), indent=2))
