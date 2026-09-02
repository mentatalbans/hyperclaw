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


def _print_provider_matrix(probe: bool = False) -> None:
    """Provider/slot matrix per the routing spec; --probe sends one cheap
    request per live provider and reports latency."""
    from hyperclaw.providers import registry, SLOTS
    reg = registry()
    line = "   ".join(f"{n} {'✓' if pr.live else '–'}" for n, pr in reg.providers.items())
    console.print(f"\n[bold]Providers[/bold]   {line}")
    summary = reg.slot_summary()
    items = [(slot, summary.get(slot, "not configured")) for slot in SLOTS]
    for a, b in zip(items[::2], items[1::2] + [None] * (len(items) % 2)):
        left = f"{a[0]} → {a[1]}"
        right = f"{b[0]} → {b[1]}" if b else ""
        console.print(f"[bold]Slots[/bold]       {left:<34}{right}" if a[0] == "primary"
                      else f"            {left:<34}{right}")
    if not probe:
        return
    import time as _t
    console.print("\n[bold]Probe[/bold]")
    for name, prov in reg.providers.items():
        if not prov.live:
            console.print(f"  {name}: skipped (not configured)")
            continue
        t0 = _t.time()
        try:
            if prov.kind == "anthropic":
                import anthropic as _an
                _an.Anthropic(api_key=prov.api_key).messages.create(
                    model=prov.model_for("fast") or prov.model_for(),
                    max_tokens=8, messages=[{"role": "user", "content": "ping"}])
            else:
                import openai as _oa
                _oa.OpenAI(api_key=prov.api_key, base_url=prov.base_url or None).chat.completions.create(
                    model=prov.model_for(), max_tokens=8,
                    messages=[{"role": "user", "content": "ping"}])
            console.print(f"  {name}: ok ({(_t.time()-t0)*1000:.0f}ms)")
        except Exception as e:
            console.print(f"  {name}: FAILED ({type(e).__name__}: {str(e)[:80]})")


def run_doctor(probe: bool = False) -> int:
    """
    Run all health checks and print results.
    Returns 0 if all critical checks pass, 1 if any fail.
    """
    console.print("\n[bold cyan]⚡ HyperClaw Doctor[/bold cyan]\n")
    _print_provider_matrix(probe=probe)

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
    # Load ~/.hyperclaw/.env so doctor works without a sourced shell
    _hc_env = Path.home() / ".hyperclaw" / ".env"
    if _hc_env.exists():
        for _line in _hc_env.read_text().splitlines():
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"'))

    provider = os.environ.get("LLM_PROVIDER", "anthropic")
    checks.append(_check("LLM_PROVIDER", True, provider))

    if provider == "openai_compat":
        oai_key = os.environ.get("OPENAI_API_KEY", "")
        checks.append(_check(
            "OPENAI_API_KEY",
            bool(oai_key),
            f"...{oai_key[-6:]}" if oai_key else "Not set — export OPENAI_API_KEY=...",
        ))
        oai_url = os.environ.get("OPENAI_BASE_URL", "")
        checks.append(_check(
            "OPENAI_BASE_URL",
            bool(oai_url),
            oai_url if oai_url else "Not set — export OPENAI_BASE_URL=http://host/v1",
        ))
        oai_model = os.environ.get("OPENAI_MODEL", "")
        checks.append(_check("OPENAI_MODEL", True, oai_model or "(default)"))
    elif provider == "bedrock":
        region = os.environ.get("AWS_REGION", "") or os.environ.get("AWS_DEFAULT_REGION", "")
        checks.append(_check(
            "AWS_REGION",
            bool(region),
            region if region else "Not set — defaulting to us-east-1",
            warn=not bool(region),
        ))
        bedrock_model = os.environ.get("BEDROCK_MODEL", "anthropic.claude-sonnet-5")
        checks.append(_check("BEDROCK_MODEL", True, bedrock_model))
    elif provider == "anthropic_compat":
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        checks.append(_check(
            "ANTHROPIC_API_KEY",
            bool(anthropic_key),
            f"...{anthropic_key[-6:]}" if anthropic_key else "Not set",
        ))
        compat_url = os.environ.get("ANTHROPIC_BASE_URL", "")
        checks.append(_check(
            "ANTHROPIC_BASE_URL",
            bool(compat_url),
            compat_url if compat_url else "Not set — export ANTHROPIC_BASE_URL=http://host",
        ))
    else:
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
