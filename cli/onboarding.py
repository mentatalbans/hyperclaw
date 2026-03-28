"""
HyperClaw Onboarding — Warm, conversational first-run experience.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich import print as rprint

console = Console()

BANNER = """
[bold cyan]
 ██╗  ██╗██╗   ██╗██████╗ ███████╗██████╗  ██████╗██╗      █████╗ ██╗    ██╗
 ██║  ██║╚██╗ ██╔╝██╔══██╗██╔════╝██╔══██╗██╔════╝██║     ██╔══██╗██║    ██║
 ███████║ ╚████╔╝ ██████╔╝█████╗  ██████╔╝██║     ██║     ███████║██║ █╗ ██║
 ██╔══██║  ╚██╔╝  ██╔═══╝ ██╔══╝  ██╔══██╗██║     ██║     ██╔══██║██║███╗██║
 ██║  ██║   ██║   ██║     ███████╗██║  ██║╚██████╗███████╗██║  ██║╚███╔███╔╝
 ╚═╝  ╚═╝   ╚═╝   ╚═╝     ╚══════╝╚═╝  ╚═╝ ╚═════╝╚══════╝╚═╝  ╚═╝ ╚══╝╚══╝
[/bold cyan]
"""


def slow_print(text: str, delay: float = 0.03):
    """Print text character by character for a warm feel."""
    for char in text:
        console.print(char, end="", highlight=False)
        time.sleep(delay)
    console.print()


def warm_greeting():
    """First greeting - friendly and inviting."""
    console.print(BANNER)
    time.sleep(0.5)

    console.print()
    slow_print("[bold white]Hey there.[/bold white]", 0.05)
    time.sleep(0.3)
    slow_print("[dim]I'm NEXUS.[/dim]", 0.04)
    time.sleep(0.5)

    console.print()
    rprint("[white]I'm going to be your AI assistant. Not just for one thing — for [bold]everything[/bold].[/white]")
    time.sleep(0.3)
    rprint("[white]Work, life, projects, research, communication — whatever you need.[/white]")
    console.print()
    time.sleep(0.5)


def get_name():
    """Ask for user's name."""
    name = Prompt.ask("[cyan]What should I call you?[/cyan]")
    console.print()
    rprint(f"[white]Nice to meet you, [bold]{name}[/bold].[/white]")
    time.sleep(0.3)
    return name


def explain_system(name: str):
    """Explain what HyperClaw does."""
    console.print()
    rprint(f"[white]Here's the deal, {name}.[/white]")
    time.sleep(0.3)
    console.print()

    rprint("[white]I coordinate [bold cyan]50+ specialized AI agents[/bold cyan].[/white]")
    time.sleep(0.2)
    rprint("[white]Finance, health, scheduling, research, creative work, coding...[/white]")
    time.sleep(0.2)
    rprint("[white]Each one is an expert in their domain.[/white]")
    console.print()
    time.sleep(0.5)

    rprint("[white]When you ask me something, I figure out which agents to involve,[/white]")
    time.sleep(0.2)
    rprint("[white]coordinate their work, and give you one clear answer.[/white]")
    console.print()
    time.sleep(0.5)

    rprint("[dim]Think of me as your command center.[/dim]")
    console.print()


def setup_api_key():
    """Guide through API key setup."""
    time.sleep(0.3)
    rprint("[white]First thing I need is an [bold]Anthropic API key[/bold].[/white]")
    rprint("[dim]This powers the AI brain behind everything.[/dim]")
    console.print()

    rprint("[white]If you don't have one:[/white]")
    rprint("[cyan]  1. Go to [link=https://console.anthropic.com]console.anthropic.com[/link][/cyan]")
    rprint("[cyan]  2. Create an account (or sign in)[/cyan]")
    rprint("[cyan]  3. Go to API Keys and create a new one[/cyan]")
    console.print()

    has_key = Confirm.ask("[cyan]Do you have your API key ready?[/cyan]")

    if not has_key:
        rprint("\n[white]No problem. Come back when you have it.[/white]")
        rprint("[dim]Run [bold]hyperclaw init[/bold] again when ready.[/dim]")
        return None

    console.print()
    api_key = Prompt.ask("[cyan]Paste your API key[/cyan]", password=True)

    if not api_key.startswith("sk-ant-"):
        rprint("[yellow]That doesn't look quite right. API keys start with 'sk-ant-'.[/yellow]")
        rprint("[dim]Double-check and try again.[/dim]")
        return None

    return api_key


def setup_database():
    """Guide through database setup."""
    console.print()
    rprint("[white]Next: [bold]memory[/bold].[/white]")
    time.sleep(0.2)
    rprint("[white]I can remember everything across sessions if you give me a database.[/white]")
    console.print()

    rprint("[white]Easiest option: [bold]Supabase[/bold] (free tier works great)[/white]")
    rprint("[cyan]  1. Go to [link=https://supabase.com]supabase.com[/link][/cyan]")
    rprint("[cyan]  2. Create a project[/cyan]")
    rprint("[cyan]  3. Copy the connection string from Settings > Database[/cyan]")
    console.print()

    setup_db = Confirm.ask("[cyan]Want to set up a database now?[/cyan]", default=False)

    if not setup_db:
        rprint("\n[white]That's fine. You can add it later.[/white]")
        rprint("[dim]I'll work without long-term memory for now.[/dim]")
        return None

    db_url = Prompt.ask("[cyan]Paste your database URL[/cyan]")
    return db_url


def setup_integrations():
    """Show available integrations."""
    console.print()
    rprint("[white]One more thing — [bold]integrations[/bold].[/white]")
    time.sleep(0.2)
    rprint("[white]I can connect to all your tools:[/white]")
    console.print()

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="cyan")
    table.add_column(style="cyan")
    table.add_column(style="cyan")

    table.add_row("Telegram", "Slack", "Discord")
    table.add_row("Gmail", "Calendar", "Notion")
    table.add_row("Salesforce", "HubSpot", "Jira")
    table.add_row("GitHub", "Stripe", "...and more")

    console.print(table)
    console.print()

    rprint("[white]You can set these up anytime in the [bold]dashboard[/bold] or [bold]config[/bold].[/white]")
    rprint("[dim]Run [bold]hyperclaw integrations list[/bold] to see all options.[/dim]")


def write_config(api_key: str | None, db_url: str | None):
    """Write configuration files."""
    config_dir = Path("config")
    config_dir.mkdir(exist_ok=True)

    env_path = Path(".env")
    env_content = []

    if api_key:
        env_content.append(f"ANTHROPIC_API_KEY={api_key}")

    if db_url:
        env_content.append(f"DATABASE_URL={db_url}")

    env_content.extend([
        "",
        "# Add your integrations below:",
        "# TELEGRAM_BOT_TOKEN=",
        "# SLACK_BOT_TOKEN=",
        "# GITHUB_TOKEN=",
    ])

    if env_content:
        env_path.write_text("\n".join(env_content))
        rprint(f"\n[green]Created [bold].env[/bold] with your settings[/green]")


def final_message(name: str, api_key: str | None):
    """Wrap up the onboarding."""
    console.print()
    console.print(Panel.fit(
        f"[bold white]You're all set, {name}.[/bold white]",
        border_style="green"
    ))
    console.print()

    if api_key:
        rprint("[white]Here's what you can do next:[/white]")
        console.print()
        rprint("[cyan]  hyperclaw start[/cyan]      — Boot me up")
        rprint("[cyan]  hyperclaw swarm \"...\"[/cyan] — Ask me anything")
        rprint("[cyan]  hyperclaw doctor[/cyan]     — Check system health")
        console.print()

        rprint("[white]Or open the [bold]dashboard[/bold]:[/white]")
        rprint("[cyan]  Start the server and go to [link=http://localhost:8000]http://localhost:8000[/link][/cyan]")
    else:
        rprint("[white]Once you have your API key:[/white]")
        rprint("[cyan]  hyperclaw init[/cyan]       — Run setup again")

    console.print()
    rprint(f"[dim]Looking forward to working with you, {name}.[/dim]")
    console.print()


def run_onboarding():
    """Main onboarding flow."""
    console.clear()

    warm_greeting()

    name = get_name()
    explain_system(name)

    api_key = setup_api_key()
    db_url = None

    if api_key:
        db_url = setup_database()
        setup_integrations()

    write_config(api_key, db_url)
    final_message(name, api_key)


if __name__ == "__main__":
    run_onboarding()
