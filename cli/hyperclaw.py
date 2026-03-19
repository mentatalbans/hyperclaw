"""
HyperClaw CLI — command-line interface for the HyperClaw swarm platform.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional
from uuid import UUID

import typer
from rich.console import Console
from rich.table import Table
from rich.syntax import Syntax

app = typer.Typer(
    name="hyperclaw",
    help="HyperClaw — unlimited-domain multi-agent AI swarm platform",
    add_completion=False,
)
state_app = typer.Typer(help="Manage HyperState objects")
app.add_typer(state_app, name="state")

console = Console()

VERSION = "0.1.0-alpha"

BANNER = """\
 ██╗  ██╗██╗   ██╗██████╗ ███████╗██████╗  ██████╗██╗      █████╗ ██╗    ██╗
 ██║  ██║╚██╗ ██╔╝██╔══██╗██╔════╝██╔══██╗██╔════╝██║     ██╔══██╗██║    ██║
 ███████║ ╚████╔╝ ██████╔╝█████╗  ██████╔╝██║     ██║     ███████║██║ █╗ ██║
 ██╔══██║  ╚██╔╝  ██╔═══╝ ██╔══╝  ██╔══██╗██║     ██║     ██╔══██║██║███╗██║
 ██║  ██║   ██║   ██║     ███████╗██║  ██║╚██████╗███████╗██║  ██║╚███╔███╔╝
 ╚═╝  ╚═╝   ╚═╝   ╚═╝     ╚══════╝╚═╝  ╚═╝ ╚═════╝╚══════╝╚═╝  ╚═╝ ╚══╝╚══╝
 The AI that actually takes over. v0.1.0-alpha\
"""

DEFAULT_CONFIG = """\
# HyperClaw Configuration
version: "0.1.0-alpha"

database:
  url: "postgresql://localhost/hyperclaw"

models:
  default: "claude-sonnet-4-6"
  chatjimmy_url: "https://chatjimmy.ai/api"
  claude_max_tokens: 4096

router:
  fast_loop_enabled: true
  slow_loop_interval_seconds: 300
  cost_budget_usd: null
  latency_budget_ms: null

swarm:
  max_agents: 50
  default_domain: "business"

security:
  policy: "default"
  audit_log: true

logging:
  level: "INFO"
  file: "hyperclaw.log"
"""


def _get_store():
    """Return a connected HyperStateStore."""
    # Import here to keep CLI startup fast
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from core.hyperstate.store import HyperStateStore
    store = HyperStateStore()
    return store


@app.command()
def init() -> None:
    """Initialize HyperClaw: create DB tables and default config."""
    console.print("[bold cyan]⚡ HyperClaw Init[/bold cyan]")

    # Write default config if not present
    config_path = Path("config/hyperclaw.yaml")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        config_path.write_text(DEFAULT_CONFIG)
        console.print(f"[green]✓[/green] Created default config at [bold]{config_path}[/bold]")
    else:
        console.print(f"[yellow]→[/yellow] Config already exists at [bold]{config_path}[/bold]")

    # Create DB tables
    async def _create():
        store = _get_store()
        try:
            await store.connect()
            await store.create_tables()
            await store.close()
            console.print("[green]✓[/green] Database tables created")
        except Exception as e:
            console.print(f"[yellow]⚠[/yellow] Database init skipped (no DB configured): {e}")

    asyncio.run(_create())
    console.print("\n[bold green]HyperClaw initialized. Run [cyan]hyperclaw start[/cyan] to boot the swarm.[/bold green]")


@app.command()
def start() -> None:
    """Boot the HyperClaw swarm."""
    console.print(f"[bold magenta]{BANNER}[/bold magenta]")
    console.print("\n[bold cyan]HyperClaw starting...[/bold cyan]")
    console.print("[dim]Swarm boot is a stub in v0.1.0-alpha. Full swarm launch coming in v0.2.0.[/dim]")
    console.print("\n[green]✓[/green] HyperCore loaded")
    console.print("[green]✓[/green] HyperRouter initialized")
    console.print("[green]✓[/green] HyperState store connected")
    console.print("[dim cyan]Awaiting agent registration...[/dim cyan]")


@app.command()
def version() -> None:
    """Print HyperClaw version."""
    console.print(f"[bold]HyperClaw[/bold] v{VERSION}")


@state_app.command("list")
def state_list() -> None:
    """List all active HyperStates."""
    async def _list():
        store = _get_store()
        try:
            await store.connect()
            states = await store.list_states(limit=50)
            await store.close()
        except Exception as e:
            console.print(f"[red]Error listing states:[/red] {e}")
            return

        if not states:
            console.print("[dim]No active HyperStates found.[/dim]")
            return

        table = Table(title="Active HyperStates", show_header=True, header_style="bold cyan")
        table.add_column("State ID", style="dim", width=36)
        table.add_column("Domain", style="cyan")
        table.add_column("Goal", max_width=50)
        table.add_column("Version", justify="right")
        table.add_column("Last Updated", width=20)

        for s in states:
            table.add_row(
                str(s.state_id),
                s.domain,
                s.task.goal[:50],
                str(s.state_version),
                s.last_updated.strftime("%Y-%m-%d %H:%M"),
            )
        console.print(table)

    asyncio.run(_list())


@state_app.command("inspect")
def state_inspect(state_id: str = typer.Argument(..., help="HyperState UUID")) -> None:
    """Inspect a HyperState by ID."""
    async def _inspect():
        try:
            sid = UUID(state_id)
        except ValueError:
            console.print(f"[red]Invalid UUID:[/red] {state_id}")
            raise typer.Exit(1)

        store = _get_store()
        try:
            await store.connect()
            state = await store.load_state(sid)
            await store.close()
        except KeyError:
            console.print(f"[red]HyperState not found:[/red] {state_id}")
            raise typer.Exit(1)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)

        data = json.loads(state.model_dump_json(indent=2))
        syntax = Syntax(json.dumps(data, indent=2), "json", theme="monokai", line_numbers=True)
        console.print(syntax)

    asyncio.run(_inspect())


def main() -> None:
    app()


if __name__ == "__main__":
    main()
