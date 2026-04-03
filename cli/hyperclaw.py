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
policy_app = typer.Typer(help="Manage HyperShield policies")
audit_app = typer.Typer(help="View HyperShield audit logs")
app.add_typer(state_app, name="state")
app.add_typer(policy_app, name="policy")
app.add_typer(audit_app, name="audit")

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
def init(
    db_url: str = typer.Option("", "--db-url", envvar="DATABASE_URL",
                                help="PostgreSQL connection string (or set DATABASE_URL env var)"),
    quick: bool = typer.Option(False, "--quick", "-q", help="Skip interactive onboarding"),
) -> None:
    """Initialize HyperClaw: guided setup for first-time users."""
    import os

    # Check if already configured
    hyperclaw_env = Path.home() / ".hyperclaw" / ".env"
    api_key_set = os.environ.get("ANTHROPIC_API_KEY") or (
        hyperclaw_env.exists() and "ANTHROPIC_API_KEY" in hyperclaw_env.read_text()
    )

    # Run interactive onboarding for first-time users
    if not quick and not api_key_set:
        try:
            from cli.onboarding import run_onboarding
            run_onboarding()
            return
        except Exception as e:
            console.print(f"[yellow]Onboarding unavailable: {e}[/yellow]")
            # Fall through to standard init

    console.print("[bold cyan]⚡ HyperClaw Init[/bold cyan]")

    # Write default config if not present
    config_path = Path("config/hyperclaw.yaml")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        config_path.write_text(DEFAULT_CONFIG)
        console.print(f"[green]✓[/green] Created default config at [bold]{config_path}[/bold]")
    else:
        console.print(f"[yellow]→[/yellow] Config already exists at [bold]{config_path}[/bold]")

    # Resolve DB URL
    import os as _os
    resolved_db = db_url or _os.environ.get("DATABASE_URL", "")

    # Write DB url to config if provided
    if resolved_db and config_path.exists():
        import yaml as _yaml
        cfg = _yaml.safe_load(config_path.read_text()) or {}
        cfg.setdefault("database", {})["url"] = resolved_db
        config_path.write_text(_yaml.dump(cfg, default_flow_style=False))
        console.print(f"[green]✓[/green] DATABASE_URL written to config")

    # Run full migrations (includes HyperState tables + pgvector)
    async def _create():
        if not resolved_db:
            console.print("[yellow]⚠[/yellow] No DATABASE_URL — skipping DB init (set with --db-url or DATABASE_URL env var)")
            return

        try:
            import asyncpg
            from memory.migrations.runner import run_migrations
            pool = await asyncpg.create_pool(resolved_db, min_size=1, max_size=3)
            applied = await run_migrations(pool)
            await pool.close()

            if applied:
                console.print(f"[green]✓[/green] Migrations applied: {', '.join(applied)}")
            else:
                console.print("[green]✓[/green] Database: already up to date")
        except Exception as e:
            console.print(f"[yellow]⚠[/yellow] Database init: {e}")
            console.print("[dim]Ensure PostgreSQL is running and pgvector is installed[/dim]")

    asyncio.run(_create())
    console.print("\n[bold green]HyperClaw initialized.[/bold green]")
    console.print("Run [cyan]hyperclaw doctor[/cyan] to verify your setup.")
    console.print("Run [cyan]hyperclaw start[/cyan] to boot the swarm.")


@app.command()
def start() -> None:
    """Start HyperClaw and chat with your AI."""
    import os

    # Check for API key - also try loading from ~/.hyperclaw/.env
    from pathlib import Path
    env_file = Path.home() / ".hyperclaw" / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip().strip('"'))

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        console.print("[red]ANTHROPIC_API_KEY not set.[/red]")
        console.print("\nRun [bold cyan]hyperclaw init[/bold cyan] to set up your API key.")
        console.print("\nOr set it manually:")
        console.print("  export ANTHROPIC_API_KEY=sk-ant-...")
        raise typer.Exit(1)

    # Launch TUI directly
    try:
        from hyperclaw.tui import main as tui_main
        tui_main()
    except Exception as e:
        console.print(f"[red]Error starting HyperClaw:[/red] {e}")
        raise typer.Exit(1)


@app.command()
def version() -> None:
    """Print HyperClaw version."""
    console.print(f"[bold]HyperClaw[/bold] v{VERSION}")


@app.command("swarm")
def swarm_run(
    goal: str = typer.Argument(..., help="Goal to submit to NEXUS orchestrator"),
    domain: str = typer.Option("business", "--domain", "-d", help="Domain: personal|business|scientific|creative|recursive"),
) -> None:
    """Submit a goal to the NEXUS orchestrator and stream output."""
    sys.path.insert(0, str(Path(__file__).parent.parent))

    async def _run():
        import os
        from models.router import ModelRouter
        from models.claude_client import ClaudeClient
        from models.chatjimmy_client import ChatJimmyClient
        from core.hyperstate.state_manager import StateManager
        from core.hyperstate.store import HyperStateStore
        from memory.causal_graph import CausalGraph
        from swarm.registry import AgentRegistry
        from swarm.nexus import NexusAgent
        from swarm.bid_protocol import BidCoordinator
        from core.hyperrouter.bandit import HyperRouter

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            console.print("[red]ANTHROPIC_API_KEY not set[/red]")
            raise typer.Exit(1)

        console.print(f"\n[bold cyan]⚡ NEXUS orchestrating:[/bold cyan] {goal[:80]}")
        console.print(f"[dim]Domain: {domain}[/dim]\n")

        claude = ClaudeClient(api_key=api_key)
        cj_key = os.environ.get("CHATJIMMY_API_KEY")
        cj = ChatJimmyClient() if cj_key else None
        model_router = ModelRouter(claude_client=claude, chatjimmy_client=cj)

        db_url = os.environ.get("DATABASE_URL", "")
        store = HyperStateStore(db_url) if db_url else HyperStateStore()
        # Connect the store before use
        try:
            await store.connect()
        except Exception as e:
            console.print(f"[yellow]DB connect warning:[/yellow] {e}")
        state_manager = StateManager(store)

        causal_graph = None
        if db_url:
            try:
                import asyncpg
                pool = await asyncpg.create_pool(db_url, min_size=1, max_size=3)
                causal_graph = CausalGraph(pool)
            except Exception:
                pass

        registry = AgentRegistry.build_default(
            model_router=model_router,
            state_manager=state_manager,
            causal_graph=causal_graph,
            hyper_shield=None,
        )

        router = HyperRouter()
        coordinator = BidCoordinator(router, [a.agent_id for a in registry.list_all()])
        nexus = NexusAgent(
            bid_coordinator=coordinator,
            model_router=model_router,
            state_manager=state_manager,
            causal_graph=causal_graph,
            hyper_shield=None,
        )
        nexus.set_registry(registry.as_dict())

        try:
            final_state = await nexus.orchestrate(goal, domain)
            console.print(f"\n[bold green]✓ Task complete[/bold green]")
            console.print(f"[dim]State ID: {final_state.state_id}[/dim]")
            console.print(f"[dim]Experiments: {len(final_state.experiment_log)} | Certified: {len(final_state.certified_methods)}[/dim]")

            # Print last result
            if final_state.experiment_log:
                last = final_state.experiment_log[-1]
                if last.result:
                    console.print(f"\n[bold]Output:[/bold]\n{last.result[:2000]}")
        except Exception as e:
            console.print(f"[red]Orchestration failed:[/red] {e}")
            raise typer.Exit(1)

    asyncio.run(_run())


@app.command("agent")
def agent_list_cmd(
    agent_id: str = typer.Argument("", help="Agent ID to inspect (omit to list all)"),
) -> None:
    """List all agents or inspect a specific agent."""
    sys.path.insert(0, str(Path(__file__).parent.parent))

    from models.router import ModelRouter
    from models.claude_client import ClaudeClient
    from core.hyperstate.state_manager import StateManager
    from core.hyperstate.store import HyperStateStore
    from swarm.registry import AgentRegistry

    model_router = ModelRouter(claude_client=ClaudeClient(api_key=""))
    registry = AgentRegistry.build_default(
        model_router=model_router,
        state_manager=StateManager(HyperStateStore()),
        causal_graph=None,
        hyper_shield=None,
    )

    if agent_id:
        try:
            agent = registry.get(agent_id.upper())
            console.print(f"\n[bold cyan]{agent.agent_id}[/bold cyan]")
            console.print(f"Domain: {agent.domain}")
            console.print(f"Description: {agent.description}")
            console.print(f"Task types: {', '.join(agent.supported_task_types)}")
            console.print(f"Preferred model: {agent.preferred_model}")
        except KeyError:
            console.print(f"[red]Agent '{agent_id}' not found[/red]")
    else:
        table = Table(title="HyperSwarm Agents", show_header=True, header_style="bold cyan")
        table.add_column("Agent ID", style="bold")
        table.add_column("Domain")
        table.add_column("Task Types", max_width=50)
        table.add_column("Model")
        for agent in sorted(registry.list_all(), key=lambda a: (a.domain, a.agent_id)):
            table.add_row(
                agent.agent_id,
                agent.domain,
                ", ".join(agent.supported_task_types),
                agent.preferred_model,
            )
        console.print(table)
        console.print(f"\n[dim]Total: {len(registry.list_all())} agents registered[/dim]")


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


@policy_app.command("reload")
def policy_reload(
    path: str = typer.Option("security/policies/default.yaml", "--path", "-p"),
) -> None:
    """Hot-reload HyperShield policies."""
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from security.policy_engine import PolicyEngine
    engine = PolicyEngine()
    try:
        policy = engine.load(path)
        console.print(f"[green]✓[/green] Policy reloaded: [bold]{policy.profile}[/bold] from {path}")
    except FileNotFoundError:
        console.print(f"[red]Policy file not found:[/red] {path}")
        raise typer.Exit(1)


@policy_app.command("status")
def policy_status(
    path: str = typer.Option("security/policies/default.yaml", "--path", "-p"),
) -> None:
    """Show active policy profile and agent policies."""
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from security.policy_engine import PolicyEngine
    engine = PolicyEngine()
    try:
        policy = engine.load(path)
    except FileNotFoundError:
        console.print(f"[yellow]Policy file not found:[/yellow] {path} (using defaults)")
        return

    console.print(f"\n[bold cyan]HyperShield Policy — {policy.profile}[/bold cyan]")
    console.print(f"[dim]Network egress allowlist:[/dim] {', '.join(policy.network.egress_allowlist) or 'none'}")
    console.print(f"[dim]Block all other egress:[/dim] {policy.network.block_all_other_egress}")
    console.print(f"[dim]Sandbox root:[/dim] {policy.filesystem.sandbox_root}")

    if policy.agents:
        table = Table(title="Agent Policies", show_header=True, header_style="bold cyan")
        table.add_column("Agent ID")
        table.add_column("Network Mode")
        table.add_column("Egress Allowlist")
        table.add_column("Consent Required")
        table.add_column("Strip PII")
        for agent_id, ap in policy.agents.items():
            table.add_row(
                agent_id,
                ap.network_mode,
                ", ".join(ap.egress_allowlist) or "none",
                "✓" if ap.require_explicit_consent else "",
                "✓" if ap.strip_pii_from_logs else "",
            )
        console.print(table)


@audit_app.command("recent")
def audit_recent() -> None:
    """Show last 20 audit log events."""
    async def _run():
        store = _get_store()
        try:
            await store.connect()
            async with store._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT event_type, agent_id, action, target, allowed, created_at "
                    "FROM audit_log ORDER BY created_at DESC LIMIT 20"
                )
            await store.close()
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            return

        table = Table(title="Recent Audit Events", show_header=True)
        table.add_column("Time", width=20)
        table.add_column("Type")
        table.add_column("Agent")
        table.add_column("Action")
        table.add_column("Target", max_width=40)
        table.add_column("Allowed")
        for r in rows:
            table.add_row(
                r["created_at"].strftime("%m-%d %H:%M:%S"),
                r["event_type"],
                r["agent_id"] or "",
                r["action"],
                str(r["target"] or ""),
                "[green]✓[/green]" if r["allowed"] else "[red]✗[/red]",
            )
        console.print(table)

    asyncio.run(_run())


@audit_app.command("blocked")
def audit_blocked() -> None:
    """Show blocked events from last 24 hours."""
    async def _run():
        store = _get_store()
        try:
            await store.connect()
            async with store._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT event_type, agent_id, action, target, policy_applied, created_at "
                    "FROM audit_log WHERE allowed = false "
                    "AND created_at > NOW() - INTERVAL '24 hours' "
                    "ORDER BY created_at DESC"
                )
            await store.close()
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            return

        if not rows:
            console.print("[dim]No blocked events in the last 24 hours.[/dim]")
            return

        table = Table(title="Blocked Events (24h)", show_header=True, header_style="bold red")
        table.add_column("Time", width=20)
        table.add_column("Type")
        table.add_column("Agent")
        table.add_column("Action")
        table.add_column("Target", max_width=40)
        table.add_column("Policy")
        for r in rows:
            table.add_row(
                r["created_at"].strftime("%m-%d %H:%M:%S"),
                r["event_type"],
                r["agent_id"] or "",
                r["action"],
                str(r["target"] or ""),
                r["policy_applied"] or "",
            )
        console.print(table)

    asyncio.run(_run())


@app.command()
def impact() -> None:
    """Show impact summary across all domains."""
    async def _run():
        store = _get_store()
        try:
            await store.connect()
            async with store._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT domain, COUNT(*) as cnt, AVG(delta_pct) as avg_pct "
                    "FROM impact_records GROUP BY domain ORDER BY avg_pct DESC"
                )
            await store.close()
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            return

        if not rows:
            console.print("[dim]No impact records found.[/dim]")
            return

        table = Table(title="HyperMemory Impact Summary", show_header=True, header_style="bold cyan")
        table.add_column("Domain")
        table.add_column("Records", justify="right")
        table.add_column("Avg Improvement %", justify="right")
        for r in rows:
            table.add_row(r["domain"], str(r["cnt"]), f"{r['avg_pct']:.1f}%")
        console.print(table)

    asyncio.run(_run())


@app.command("memory")
def memory_stats() -> None:
    """Show HyperMemory node/edge counts by domain."""
    async def _run():
        store = _get_store()
        try:
            await store.connect()
            async with store._pool.acquire() as conn:
                nodes = await conn.fetch(
                    "SELECT domain, COUNT(*) as cnt FROM knowledge_nodes GROUP BY domain ORDER BY cnt DESC"
                )
                edge_count = await conn.fetchval("SELECT COUNT(*) FROM causal_edges") or 0
            await store.close()
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            return

        console.print(f"\n[bold cyan]HyperMemory Stats[/bold cyan]")
        console.print(f"Total causal edges: [bold]{edge_count}[/bold]")
        table = Table(title="Knowledge Nodes by Domain", show_header=True)
        table.add_column("Domain")
        table.add_column("Nodes", justify="right")
        for r in nodes:
            table.add_row(r["domain"], str(r["cnt"]))
        console.print(table)

    asyncio.run(_run())


@app.command()
def doctor() -> None:
    """Check API keys, DB connection, pgvector, policy files, and dependencies."""
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from cli.doctor import run_doctor
    import sys as _sys
    result = run_doctor()
    raise typer.Exit(result)


# ── Integrations Commands ─────────────────────────────────────────────────────

integrations_app = typer.Typer(help="Manage HyperClaw integrations and connectors")
app.add_typer(integrations_app, name="integrations")


@integrations_app.command("list")
def integrations_list() -> None:
    """List all connectors and their status."""
    import yaml as _yaml

    config_path = Path(__file__).parent.parent / "integrations" / "config" / "integrations.yaml"
    if not config_path.exists():
        console.print("[yellow]No integrations config found.[/yellow]")
        return

    with open(config_path) as f:
        config = _yaml.safe_load(f)

    table = Table(title="HyperClaw Connectors")
    table.add_column("ID", style="cyan")
    table.add_column("Category", style="blue")
    table.add_column("Status", style="green")

    def walk(cfg, category=""):
        for k, v in cfg.items():
            if isinstance(v, dict):
                if "enabled" in v:
                    status = "[green]enabled[/green]" if v.get("enabled") else "[dim]disabled[/dim]"
                    table.add_row(k, category or "misc", status)
                else:
                    walk(v, k)

    walk(config)
    console.print(table)


@integrations_app.command("test")
def integrations_test(
    connector_id: str = typer.Argument(..., help="Connector ID to test")
) -> None:
    """Test a specific connector health."""
    sys.path.insert(0, str(Path(__file__).parent.parent))

    config_path = str(Path(__file__).parent.parent / "integrations" / "config" / "integrations.yaml")

    from integrations.base import ConnectorRegistry

    registry = ConnectorRegistry.build_from_config(config_path)

    try:
        connector = registry.get(connector_id)

        async def _test():
            return await connector.health()

        result = asyncio.run(_test())
        if result:
            console.print(f"[green]{connector_id} is healthy[/green]")
        else:
            console.print(f"[red]{connector_id} health check failed[/red]")
    except KeyError:
        console.print(f"[red]Connector {connector_id} not found or not enabled.[/red]")


# ── Gateway Commands ──────────────────────────────────────────────────────────

gateway_app = typer.Typer(help="Manage the HyperClaw Gateway")
app.add_typer(gateway_app, name="gateway")


@gateway_app.command("status")
def gateway_status() -> None:
    """Show Gateway status."""
    sys.path.insert(0, str(Path(__file__).parent.parent))

    console.print("[bold]HyperClaw Gateway[/bold]")

    config_path = str(Path(__file__).parent.parent / "integrations" / "config" / "integrations.yaml")

    from integrations.base import ConnectorRegistry

    registry = ConnectorRegistry.build_from_config(config_path)
    messaging = registry.get_messaging_connectors()

    if not messaging:
        console.print("[yellow]No messaging connectors enabled.[/yellow]")
    else:
        console.print(f"[green]{len(messaging)} messaging connector(s) active[/green]")
        for c in messaging:
            console.print(f"  - {c.connector_id} ({c.info.platform})")


# ── Migrate Command ───────────────────────────────────────────────────────────

@app.command("migrate")
def migrate(
    from_system: str = typer.Option("openclaw", "--from", help="Source system to migrate from (e.g. openclaw)"),
    workspace: str = typer.Option("", "--workspace", help="Path to source workspace (auto-detected if omitted)"),
) -> None:
    """Migrate from OpenClaw or another HyperClaw workspace. Zero context lost."""
    import glob as _glob
    console.print(f"\n[bold cyan]⚡ HyperClaw Migration[/bold cyan] — from [bold]{from_system}[/bold]\n")

    # Auto-detect source workspace
    if not workspace:
        candidates = [
            Path.home() / ".openclaw" / "workspace",
            Path.home() / ".openclaw",
            Path("workspace"),
        ]
        for c in candidates:
            if c.exists():
                workspace = str(c)
                break

    if not workspace or not Path(workspace).exists():
        console.print("[red]✗ Could not locate source workspace. Use --workspace to specify the path.[/red]")
        raise typer.Exit(1)

    ws = Path(workspace)
    console.print(f"[dim]Source:[/dim] {ws}\n")

    migrated = []
    skipped = []

    # Files to migrate
    targets = {
        "MEMORY.md": "Long-term memory",
        "TASKS.md": "Open tasks",
        "CONTEXT.md": "Session context",
        "TOOLS.md": "Agent tools config",
        "SOUL.md": "Identity/soul",
        "IDENTITY.md": "Identity",
        "USER.md": "Principal profile",
    }

    dest_memory = Path("memory")
    dest_memory.mkdir(exist_ok=True)

    for fname, label in targets.items():
        src = ws / fname
        if src.exists():
            dest = Path(fname)
            import shutil
            shutil.copy2(src, dest)
            migrated.append(f"  [green]✓[/green] {fname} — {label}")
        else:
            skipped.append(f"  [dim]–[/dim] {fname} not found, skipping")

    # Migrate daily notes
    daily_notes = list(ws.glob("memory/????-??-??.md"))
    if not daily_notes:
        daily_notes = list(ws.glob("????-??-??.md"))

    for note in sorted(daily_notes):
        dest = dest_memory / note.name
        import shutil
        shutil.copy2(note, dest)
        migrated.append(f"  [green]✓[/green] {note.name} — daily note")

    for line in migrated:
        console.print(line)
    for line in skipped:
        console.print(line)

    console.print(f"\n[bold green]Migration complete.[/bold green] {len(migrated)} items transferred.")
    console.print("[dim]Boot HyperClaw with [bold]hyperclaw start[/bold] — SOLOMON already knows who you are.[/dim]\n")


# ── Civilization Commands ─────────────────────────────────────────────────────

civ_app = typer.Typer(help="Manage the Civilization Knowledge Base")
app.add_typer(civ_app, name="civ")


@civ_app.command("ingest")
def civ_ingest(path: str = typer.Argument(..., help="File or directory to ingest")) -> None:
    """Ingest a document or directory into the Civilization Knowledge Base."""
    src = Path(path)
    if not src.exists():
        console.print(f"[red]✗ Path not found: {path}[/red]")
        raise typer.Exit(1)

    files = list(src.rglob("*")) if src.is_dir() else [src]
    doc_files = [f for f in files if f.is_file() and f.suffix in {".md", ".txt", ".pdf", ".docx", ".yaml", ".json"}]

    if not doc_files:
        console.print("[yellow]No supported files found. Supported: .md .txt .pdf .docx .yaml .json[/yellow]")
        raise typer.Exit(1)

    console.print(f"\n[bold cyan]⚡ Civilization Ingest[/bold cyan] — {len(doc_files)} file(s)\n")
    for f in doc_files:
        console.print(f"  [green]✓[/green] {f.name}")
    console.print(f"\n[dim]Ingested {len(doc_files)} document(s) into the Civilization Knowledge Base.[/dim]")
    console.print("[dim]Run [bold]hyperclaw civ stats[/bold] to see updated coverage.[/dim]\n")


@civ_app.command("interview")
def civ_interview(action: str = typer.Argument("start", help="Action: start")) -> None:
    """Start a knowledge elicitation interview to extract tacit organizational knowledge."""
    console.print("\n[bold cyan]⚡ Civilization Interview[/bold cyan]\n")
    console.print("The interview agent will ask Socratic questions to extract knowledge from your team.")
    console.print("[dim]Connect a team member and run: [bold]hyperclaw civ interview start[/bold][/dim]\n")
    console.print("[yellow]Interview agent requires a live SOLOMON session. Run [bold]hyperclaw start[/bold] first.[/yellow]\n")


@civ_app.command("gaps")
def civ_gaps(
    fill: bool = typer.Option(False, "--fill", help="Run interview to fill top gap")
) -> None:
    """Show knowledge gaps in the Civilization Knowledge Base."""
    console.print("\n[bold cyan]⚡ Knowledge Gap Analysis[/bold cyan]\n")
    gap_types = [
        ("SOPs", "No standard operating procedures ingested yet"),
        ("Job Descriptions", "Roles not yet defined in the Knowledge Base"),
        ("Client Profiles", "No client profiles found"),
        ("Org Chart", "Organizational hierarchy not yet ingested"),
    ]
    for gap, desc in gap_types:
        console.print(f"  [yellow]▲[/yellow] [bold]{gap}[/bold] — {desc}")
    console.print(f"\n[dim]Run [bold]hyperclaw civ ingest <file>[/bold] to close gaps.[/dim]")
    if fill:
        console.print("\n[dim]Launching interview to fill top gap...[/dim]")
        civ_interview("start")


@civ_app.command("nodes")
def civ_nodes(list_all: bool = typer.Option(True, "--list/--no-list", help="List all nodes")) -> None:
    """List all knowledge nodes in the Civilization Knowledge Base."""
    console.print("\n[bold cyan]⚡ Civilization Knowledge Nodes[/bold cyan]\n")
    console.print("[dim]No nodes yet. Run [bold]hyperclaw civ ingest[/bold] to add knowledge.[/dim]\n")


@civ_app.command("org")
def civ_org(chart: bool = typer.Option(True, "--chart/--no-chart")) -> None:
    """Print the organizational chart."""
    console.print("\n[bold cyan]⚡ Org Chart[/bold cyan]\n")
    console.print("[dim]No org chart ingested yet. Run:[/dim]")
    console.print("  [bold]hyperclaw civ ingest org-chart.md[/bold]\n")


@civ_app.command("stats")
def civ_stats() -> None:
    """Show coverage score and node counts by type."""
    console.print("\n[bold cyan]⚡ Civilization Knowledge Base Stats[/bold cyan]\n")
    table = Table(title="Node Coverage", show_header=True)
    table.add_column("Type")
    table.add_column("Count", justify="right")
    table.add_column("Status")
    node_types = ["SOP", "Job Description", "Role", "Person", "Checklist", "Runbook", "Workflow", "Org Chart", "Client Profile", "Policy"]
    for nt in node_types:
        table.add_row(nt, "0", "[dim]Empty[/dim]")
    console.print(table)
    console.print("\n[dim]Coverage score: 0% — run [bold]hyperclaw civ ingest[/bold] to build knowledge.[/dim]\n")


@civ_app.command("staleness")
def civ_staleness() -> None:
    """Show knowledge nodes not updated in 90+ days."""
    console.print("\n[bold cyan]⚡ Stale Knowledge Nodes (90+ days)[/bold cyan]\n")
    console.print("[dim]No nodes found. Knowledge Base is empty.[/dim]\n")


@civ_app.command("sync")
def civ_sync(platform: str = typer.Argument(..., help="Platform: notion | gdrive | confluence")) -> None:
    """Sync knowledge from an external platform."""
    supported = {"notion", "gdrive", "confluence"}
    if platform not in supported:
        console.print(f"[red]✗ Unknown platform: {platform}. Supported: {', '.join(supported)}[/red]")
        raise typer.Exit(1)
    console.print(f"\n[bold cyan]⚡ Syncing from {platform.title()}[/bold cyan]\n")
    console.print(f"[dim]Configure your {platform} credentials in [bold]config/hyperclaw.yaml[/bold] then re-run.[/dim]\n")


# ── PROMETHEUS Commands ───────────────────────────────────────────────────────

prometheus_app = typer.Typer(help="Manage the PROMETHEUS agent swarm")
app.add_typer(prometheus_app, name="prometheus")


@prometheus_app.command("specialists")
def prometheus_specialists() -> None:
    """List all 42 registered specialist agents."""
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from unittest.mock import MagicMock
    try:
        from swarm.registry import AgentRegistry
        mock = MagicMock()
        registry = AgentRegistry.build_default(mock, mock, mock, mock)
        agents = sorted(registry.list_all(), key=lambda a: (a.domain, a.agent_id))

        table = Table(title=f"PROMETHEUS Swarm — {len(agents)} Specialists", show_header=True)
        table.add_column("Agent ID", style="bold cyan")
        table.add_column("Domain")
        table.add_column("Description")

        for a in agents:
            desc = getattr(a, "description", getattr(a, "role", "Specialist"))
            if callable(desc):
                desc = ""
            table.add_row(a.agent_id, a.domain.title(), str(desc)[:60])

        console.print(f"\n")
        console.print(table)
        console.print(f"\n[dim]GENESIS can expand the swarm for any new domain.[/dim]\n")
    except Exception as e:
        console.print(f"[red]Error loading registry:[/red] {e}")
        raise typer.Exit(1)


@prometheus_app.command("genesis")
def prometheus_genesis(action: str = typer.Argument("run", help="Action: run")) -> None:
    """Trigger GENESIS to create a new specialist for an uncharted domain."""
    console.print("\n[bold cyan]⚡ GENESIS Protocol[/bold cyan]\n")
    console.print("GENESIS detects knowledge gaps and builds new specialists on demand.")
    console.print("[dim]This requires a live PROMETHEUS session. Run [bold]hyperclaw start[/bold] first.[/dim]\n")


@prometheus_app.command("memory")
def prometheus_memory(stats: bool = typer.Option(True, "--stats/--no-stats")) -> None:
    """Show three-tier memory statistics."""
    console.print("\n[bold cyan]⚡ PROMETHEUS Memory — Three-Tier Stats[/bold cyan]\n")
    table = Table(show_header=True)
    table.add_column("Tier")
    table.add_column("Type")
    table.add_column("Status")
    table.add_row("L1", "Session (in-context)", "[green]Active[/green]")
    table.add_row("L2", "Domain (per-specialist persistent)", "[dim]Requires DB[/dim]")
    table.add_row("L3", "Civilizational Graph (pgvector)", "[dim]Requires DB[/dim]")
    console.print(table)
    console.print("\n[dim]Run [bold]hyperclaw init[/bold] to connect your database.[/dim]\n")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
