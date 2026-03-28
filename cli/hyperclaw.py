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
    # Run interactive onboarding for first-time users
    if not quick and not Path(".env").exists():
        try:
            from cli.onboarding import run_onboarding
            run_onboarding()
            return
        except Exception:
            pass  # Fall through to standard init

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
def start(
    no_tui: bool = typer.Option(False, "--no-tui", help="Skip TUI, run headless"),
    no_growth: bool = typer.Option(False, "--no-growth", help="Skip Recursive Growth Engine"),
) -> None:
    """Boot the HyperClaw swarm."""
    console.print(f"[bold magenta]{BANNER}[/bold magenta]")
    console.print("\n[bold cyan]HyperClaw starting...[/bold cyan]\n")

    sys.path.insert(0, str(Path(__file__).parent.parent))

    async def _boot():
        import os

        # 1. Run migrations
        db_url = os.environ.get("DATABASE_URL", "")
        if db_url:
            try:
                import asyncpg
                from memory.migrations.runner import run_migrations
                pool = await asyncpg.create_pool(db_url, min_size=1, max_size=5)
                applied = await run_migrations(pool)
                if applied:
                    console.print(f"[green]✓[/green] DB migrations: {len(applied)} applied")
                else:
                    console.print("[green]✓[/green] DB migrations: up to date")
            except Exception as e:
                console.print(f"[yellow]⚠[/yellow] DB migrations skipped: {e}")
                pool = None
        else:
            console.print("[yellow]⚠[/yellow] DATABASE_URL not set — HyperMemory disabled")
            pool = None

        # 2. Initialize HyperShield
        try:
            from security.hypershield import HyperShield
            shield = HyperShield("security/policies/default.yaml", pool) if pool else None
            if shield:
                await shield.initialize()
                console.print("[green]✓[/green] HyperShield initialized")
            else:
                console.print("[yellow]⚠[/yellow] HyperShield running without DB audit log")
                from security.policy_engine import PolicyEngine
                from security.audit_logger import AuditLogger
        except Exception as e:
            console.print(f"[yellow]⚠[/yellow] HyperShield: {e}")
            shield = None

        # 3. Build AgentRegistry
        try:
            from models.router import ModelRouter
            from models.claude_client import ClaudeClient
            from models.chatjimmy_client import ChatJimmyClient
            from core.hyperstate.state_manager import StateManager
            from core.hyperstate.store import HyperStateStore
            from memory.causal_graph import CausalGraph
            from swarm.registry import AgentRegistry

            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            claude = ClaudeClient(api_key=api_key) if api_key else None
            cj = ChatJimmyClient() if os.environ.get("CHATJIMMY_API_KEY") else None
            model_router = ModelRouter(claude_client=claude or ClaudeClient(api_key=""), chatjimmy_client=cj)

            store = HyperStateStore(db_url) if db_url else HyperStateStore()
            state_manager = StateManager(store)
            causal_graph = CausalGraph(pool) if pool else None

            registry = AgentRegistry.build_default(
                model_router=model_router,
                state_manager=state_manager,
                causal_graph=causal_graph,
                hyper_shield=shield,
            )
            count = len(registry.list_all())
            console.print(f"[green]✓[/green] AgentRegistry: {count} agents registered")

            # Print agent table
            table = Table(show_header=True, header_style="bold cyan")
            table.add_column("Agent", style="bold")
            table.add_column("Domain")
            table.add_column("Model")
            for agent in sorted(registry.list_all(), key=lambda a: (a.domain, a.agent_id)):
                table.add_row(agent.agent_id, agent.domain, agent.preferred_model)
            console.print(table)

        except Exception as e:
            console.print(f"[red]✗[/red] AgentRegistry failed: {e}")
            registry = None

        # 4. Start Recursive Growth Engine
        if not no_growth and registry:
            try:
                from swarm.agents.recursive.scout import ScoutAgent
                from swarm.agents.recursive.alchemist import AlchemistAgent
                from swarm.agents.recursive.calibrator import CalibratorAgent
                from recursive.discovery_loop import RecursiveGrowthEngine

                scout = registry.get("SCOUT")
                alchemist = registry.get("ALCHEMIST")
                calibrator = registry.get("CALIBRATOR")

                engine = RecursiveGrowthEngine(scout, alchemist, calibrator, causal_graph)
                console.print("[green]✓[/green] Recursive Growth Engine: active (6h interval)")
            except Exception as e:
                console.print(f"[yellow]⚠[/yellow] Recursive Growth Engine: {e}")

        console.print(f"\n[bold green]✓ HyperClaw is running.[/bold green]")
        console.print("[dim]Control Center: http://localhost:3000 (coming in v0.2.0)[/dim]")
        console.print("[dim]Press Ctrl+C to stop[/dim]\n")

    asyncio.run(_boot())

    # 5. Launch TUI
    if not no_tui:
        try:
            from ui.tui.app import HyperClawTUI
            app_tui = HyperClawTUI()
            app_tui.run()
        except Exception as e:
            console.print(f"[yellow]⚠[/yellow] TUI unavailable: {e}")
            console.print("[dim]Run with --no-tui to skip the terminal UI[/dim]")


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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
