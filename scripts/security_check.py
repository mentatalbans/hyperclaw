#!/usr/bin/env python3
"""
Assistant Security Check — Full System Audit
Runs every 6 hours. Reports to the user via Telegram.
"""

import subprocess
import os
import json
import socket
import datetime
import requests
from pathlib import Path

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))

SECRETS_DIR = Path(str(Path.home() / ".hyperclaw/workspace/secrets"))
ENV_FILE = Path(str(Path.home() / ".hyperclaw/.env"))
HYPERCLAW_ROOT = Path(os.environ.get("HYPERCLAW_ROOT", str(Path.home() / ".hyperclaw")))

EXPECTED_LAUNCHD = {
    "com.hyperclaw.telegram-listener",
    "com.hyperclaw.telegram-poller",
    "com.hyperclaw.api",
    "com.hyperclaw.plist",  # main hyperclaw
    "com.hyperclaw.prometheus",
    "com.hyperclaw.trading",
    "homebrew.mxcl.tailscale",
    "homebrew.mxcl.tor",
    "com.hyperclaw.security-check",
    "com.hyperclaw.memory-server",
    "com.hyperclaw",
    "com.google.GoogleUpdater.wake",
    "com.google.keystone.agent",
    "com.google.keystone.xpcservice",
}

EXPECTED_PORTS_LOCAL = {
    5001: "ATLAS_TRADING (Hyperliquid)",
    8001: "HyperClaw API",
    8765: "Memory Server",
    9050: "Tor SOCKS",
}

FLAGGED_WORLD_OPEN_PORTS = {5000, 7000, 49152}  # ControlCenter / rapportd — note but don't panic


def run(cmd):
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        return result.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"


def check_firewall():
    out = run("sudo /usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate 2>/dev/null")
    enabled = "enabled" in out.lower()
    return "✅ ENABLED" if enabled else "🚨 DISABLED — CRITICAL"


def check_ssh():
    out = run("sudo systemsetup -getremotelogin 2>/dev/null")
    off = "Off" in out
    return "✅ OFF" if off else "🚨 ON — EXPOSURE RISK"


def check_sip():
    out = run("csrutil status 2>/dev/null")
    enabled = "enabled" in out.lower()
    return "✅ ENABLED" if enabled else "⚠️ DISABLED"


def check_secrets_permissions():
    issues = []
    for f in SECRETS_DIR.iterdir() if SECRETS_DIR.exists() else []:
        mode = oct(f.stat().st_mode)[-3:]
        if mode not in ("600", "400"):
            issues.append(f"{f.name} ({mode})")
    if ENV_FILE.exists():
        mode = oct(ENV_FILE.stat().st_mode)[-3:]
        if mode not in ("600", "400"):
            issues.append(f".env ({mode})")
    return ("✅ All secret files locked (600/400)" if not issues
            else f"⚠️ Permission issues: {', '.join(issues)}")


def check_open_ports():
    out = run("netstat -an | grep LISTEN")
    lines = out.splitlines()
    external_listeners = []
    for line in lines:
        if "127.0.0.1" in line or "::1" in line:
            continue
        parts = line.split()
        if len(parts) >= 4:
            addr = parts[3]
            external_listeners.append(addr)
    if not external_listeners:
        return "✅ No unexpected external listeners"
    flagged = [p for p in external_listeners if any(str(port) in p for port in FLAGGED_WORLD_OPEN_PORTS)]
    if flagged:
        return f"⚠️ System ports open externally (macOS ControlCenter/rapportd — normal): {', '.join(flagged)}"
    return f"ℹ️ External listeners: {', '.join(external_listeners)}"


def check_local_services():
    results = []
    for port, name in EXPECTED_PORTS_LOCAL.items():
        try:
            s = socket.socket()
            s.settimeout(1)
            s.connect(("127.0.0.1", port))
            s.close()
            results.append(f"  ✅ :{port} {name}")
        except:
            results.append(f"  ❌ :{port} {name} — DOWN")
    return "\n".join(results)


def check_trading():
    try:
        r = requests.get("http://127.0.0.1:5001/status", timeout=5)
        d = r.json()
        bal = d.get("hl_balance", "?")
        pos = d.get("open_positions", "?")
        halted = d.get("halted", False)
        status = "🚨 HALTED" if halted else "✅ LIVE"
        return f"{status} | Balance: ${bal:.2f} | Positions: {pos}"
    except:
        return "❌ UNREACHABLE — CRITICAL"


def check_hyperclaw():
    try:
        r = requests.get("http://127.0.0.1:8001/health", timeout=5)
        d = r.json()
        uptime = d.get("uptime_seconds", 0)
        status = d.get("status", "unknown")
        return f"✅ {status.upper()} | Uptime: {int(uptime)}s"
    except:
        return "❌ UNREACHABLE"


def check_launchd():
    out = run("ls ~/Library/LaunchAgents/")
    loaded = set()
    for line in out.splitlines():
        name = line.replace(".plist", "").strip()
        loaded.add(name)
    unexpected = loaded - EXPECTED_LAUNCHD
    if unexpected:
        return f"⚠️ Unexpected LaunchAgents: {', '.join(unexpected)}"
    return "✅ All LaunchAgents match expected set"


def check_disk():
    out = run("df -h /")
    lines = out.splitlines()
    if len(lines) >= 2:
        parts = lines[1].split()
        used = parts[2]
        avail = parts[3]
        cap = parts[4]
        return f"Used: {used} | Free: {avail} | Capacity: {cap}"
    return "Unknown"


def check_outbound_connections():
    out = run("netstat -an | grep ESTABLISHED | grep -v '127.0.0.1\\|::1'")
    lines = [l for l in out.splitlines() if l.strip()]
    # Flag any non-443/993 port connections
    suspicious = []
    for line in lines:
        parts = line.split()
        if len(parts) >= 5:
            remote = parts[4]
            port = remote.split(".")[-1] if "." in remote else remote.split(":")[-1]
            if port not in ("443", "993", "80", "8080"):
                suspicious.append(f"{remote} (port {port})")
    if suspicious:
        return f"⚠️ Non-standard outbound: {', '.join(suspicious[:5])}"
    return f"✅ {len(lines)} outbound connections — all standard ports (443/993)"


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN:
        print("No Telegram token — printing report:\n", message)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": ""}
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        print("Telegram report sent.")
    except Exception as e:
        print(f"Telegram send failed: {e}")


def main():
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M PST")

    report_lines = [
        f"⚡ Assistant SECURITY CHECK — {now}",
        "=" * 38,
        "",
        "[ SYSTEM ]",
        f"  Firewall:     {check_firewall()}",
        f"  SSH Remote:   {check_ssh()}",
        f"  SIP:          {check_sip()}",
        f"  Disk:         {check_disk()}",
        "",
        "[ CREDENTIALS ]",
        f"  {check_secrets_permissions()}",
        "",
        "[ NETWORK ]",
        f"  Open Ports:   {check_open_ports()}",
        f"  Outbound:     {check_outbound_connections()}",
        "",
        "[ SERVICES ]",
        check_local_services(),
        "",
        "[ HYPERCLAW ]",
        f"  API:          {check_hyperclaw()}",
        f"  ATLAS_TRADING:      {check_trading()}",
        "",
        "[ LAUNCHD ]",
        f"  {check_launchd()}",
        "",
        "— Assistant ⚡",
    ]

    report = "\n".join(report_lines)
    print(report)
    send_telegram(report)


if __name__ == "__main__":
    # Load .env manually if dotenv not available
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"'))
    main()
