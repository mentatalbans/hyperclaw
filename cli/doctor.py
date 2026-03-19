"""
hyperclaw doctor — system health check.
Verifies API keys, DB connection, pgvector, policy files, and dependencies.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
from pathlib import Path
from typing import Callable

from rich.console import Console
from rich.table import Table
from rich import print as rprint

console = Console()

CHECK_MARK = "[bold green]✓[/bold green]"
CROSS_MARK = "[bold red]✗[/bold red]"
WARN_MARK  = "[bold yellow]⚠[/bold yellow]"


def _check(label: str, ok: bool, detail: str = "", warn: bool = False) -> dict:
    status = CHECK_MARK if ok else (WARN_MARK if warn else CROSS_MARK)
    return {"label": label, "ok": ok, "warn": warn, "detail": detail, "status": status}


async def _check_db(db_url: str) -> dict:
    try:
        import asyncpg
        conn = await asyncpg.connect(db_url, timeout=5)
        await conn.close()
        return _check("PostgreSQL connection", True, db_url.split("@")[-1] if "@" in db_url else db_url)
    except Exception as e:
        return _check("PostgreSQL connection", False, str(e)[:80])


async def _check_pgvector(db_url: str) -> dict:
    try:
        import asyncpg
        conn = await asyncpg.connect(db_url, timeout=5)
        row = await conn.fetchval(
            "SELECT COUNT(*) FROM pg_extension WHERE extname = 'vector'"
        )
        await conn.close()
        if row:
            return _check("pgvector extension", True, "installed")
        else:
            return _check("pgvector extension", False,
                         "Not installed — run: CREATE EXTENSION vector; in your DB", warn=True)
    except Exception as e:
        return _check("pgvector extension", False, str(e)[:80])


async def _check_migrations(db_url: str) -> dict:
    try:
        import asyncpg
        conn = await asyncpg.connect(db_url, timeout=5)
        exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name='_migrations')"
        )
        if exists:
            applied = await conn.fetch("SELECT filename FROM _migrations ORDER BY filename")
            names = [r["filename"] for r in applied]
            await conn.close()
            return _check("DB migrations", True, f"{len(names)} applied: {', '.join(names)}")
        else:
            await conn.close()
            return _check("DB migrations", False,
                         "Not initialized — run: hyperclaw init", warn=True)
    except Exception as e:
        return _check("DB migrations", False, str(e)[:80])


def run_doctor() -> int:
    """
    Run all health checks and print results.
    Returns 0 if all critical checks pass, 1 if any fail.
    """
    console.print("\n[bold cyan]⚡ HyperClaw Doctor[/bold cyan]\n")

    checks: list[dict] = []
    db_url = os.environ.get("DATABASE_URL", "")

    # ── Python version ────────────────────────────────────────────────────────
    py = sys.version_info
    checks.append(_check(
        "Python version",
        py >= (3, 11),
        f"{py.major}.{py.minor}.{py.micro}",
        warn=(py >= (3, 11)),
    ))

    # ── Required packages ─────────────────────────────────────────────────────
    required_packages = {
        "pydantic": "pydantic",
        "asyncpg": "asyncpg",
        "anthropic": "anthropic",
        "httpx": "httpx",
        "typer": "typer",
        "rich": "rich",
        "yaml": "yaml (pyyaml)",
        "dotenv": "dotenv (python-dotenv)",
    }
    for mod, label in required_packages.items():
        try:
            importlib.import_module(mod)
            checks.append(_check(f"Package: {label}", True))
        except ImportError:
            checks.append(_check(f"Package: {label}", False, f"pip install {label.split()[0]}"))

    # ── Environment variables ─────────────────────────────────────────────────
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    checks.append(_check(
        "ANTHROPIC_API_KEY",
        bool(anthropic_key),
        f"sk-ant-...{anthropic_key[-6:]}" if anthropic_key else "Not set — export ANTHROPIC_API_KEY=...",
    ))

    db_url_env = os.environ.get("DATABASE_URL", "")
    checks.append(_check(
        "DATABASE_URL",
        bool(db_url_env),
        f"...{db_url_env[-30:]}" if db_url_env else "Not set — using config/hyperclaw.yaml fallback",
        warn=not bool(db_url_env),
    ))

    chatjimmy_key = os.environ.get("CHATJIMMY_API_KEY", "")
    checks.append(_check(
        "CHATJIMMY_API_KEY",
        bool(chatjimmy_key),
        "Set" if chatjimmy_key else "Not set — ChatJimmy will be unavailable (optional)",
        warn=not bool(chatjimmy_key),
    ))

    # ── Config files ──────────────────────────────────────────────────────────
    config_files = [
        "config/hyperclaw.yaml",
        "config/agents.yaml",
        "config/models.yaml",
        "security/policies/default.yaml",
    ]
    for cf in config_files:
        checks.append(_check(f"Config: {cf}", Path(cf).exists(),
                            "Found" if Path(cf).exists() else f"Missing — run: hyperclaw init"))

    # ── DB checks (async) ─────────────────────────────────────────────────────
    if db_url_env:
        loop = asyncio.new_event_loop()
        try:
            checks.append(loop.run_until_complete(_check_db(db_url_env)))
            checks.append(loop.run_until_complete(_check_pgvector(db_url_env)))
            checks.append(loop.run_until_complete(_check_migrations(db_url_env)))
        finally:
            loop.close()
    else:
        checks.append(_check("PostgreSQL connection", False,
                            "Skipped — DATABASE_URL not set", warn=True))
        checks.append(_check("pgvector extension", False,
                            "Skipped — DATABASE_URL not set", warn=True))
        checks.append(_check("DB migrations", False,
                            "Skipped — DATABASE_URL not set", warn=True))

    # ── Print results ─────────────────────────────────────────────────────────
    table = Table(show_header=True, header_style="bold cyan", expand=True)
    table.add_column("Check", style="bold")
    table.add_column("Status", justify="center", width=4)
    table.add_column("Detail", style="dim")

    for c in checks:
        table.add_row(c["label"], c["status"], c["detail"])

    console.print(table)

    failed = [c for c in checks if not c["ok"] and not c.get("warn")]
    warned = [c for c in checks if not c["ok"] and c.get("warn")]
    passed = [c for c in checks if c["ok"]]

    console.print(
        f"\n[green]{len(passed)} passed[/green]  "
        f"[yellow]{len(warned)} warnings[/yellow]  "
        f"[red]{len(failed)} failed[/red]"
    )

    if failed:
        console.print("\n[red]Fix the failed checks above before running hyperclaw start.[/red]")
        return 1

    if not warned:
        console.print("\n[bold green]✓ HyperClaw is healthy and ready to run.[/bold green]")
    else:
        console.print("\n[bold yellow]⚠ HyperClaw has warnings — review above before running tasks.[/bold yellow]")

    return 0
