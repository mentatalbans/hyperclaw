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
