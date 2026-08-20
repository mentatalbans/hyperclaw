"""
Media Hub — unified outbound file delivery across every channel.

deliver_file(path, via, to, caption) is the single entry point:
    via='here'      -> queue for the current conversation (Telegram or iMessage,
                       whichever this turn came from) via outbox
    via='telegram'  -> Telegram sendPhoto/sendVideo/sendAudio/sendDocument
    via='imessage'  -> Messages.app attachment via AppleScript
    via='email'     -> Gmail with attachment (integrations_layer.gmail_send)
    via='open'      -> open the file locally on the Mac (Preview/Word/etc.)

Every sender is direct and synchronous — no daemon dependencies — so this works
from the TUI, the Telegram bot, the iMessage daemon, and cron jobs alike.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import httpx

from . import outbox

MAX_TG_BYTES = 49 * 1024 * 1024


def _resolve(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    return p


# ── Telegram ─────────────────────────────────────────────────────────────────

def telegram_send_file(path: str, caption: str = "", chat_id: str = "") -> str:
    """Send any file over Telegram, picking the right API method by type."""
    p = _resolve(path)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    cid = str(chat_id or os.environ.get("TELEGRAM_CHAT_ID", ""))
    if not token or not cid:
        return "Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)"
    if p.stat().st_size > MAX_TG_BYTES:
        return f"File too large for Telegram ({p.stat().st_size // (1024*1024)}MB > 49MB). Email it instead."

    kind = outbox.kind_of(str(p))
    method, field = {
        "photo": ("sendPhoto", "photo"),
        "video": ("sendVideo", "video"),
        "audio": ("sendAudio", "audio"),
        "document": ("sendDocument", "document"),
    }[kind]
    data = {"chat_id": cid}
    if caption:
        data["caption"] = caption[:1024]
    try:
        with open(p, "rb") as f:
            with httpx.Client(timeout=120) as client:
                r = client.post(f"https://api.telegram.org/bot{token}/{method}",
                                data=data, files={field: (p.name, f)})
        if r.status_code == 200:
            return f"Sent {p.name} via Telegram ({kind})"
        # Photos with odd dimensions/format can fail — retry as document.
        if kind == "photo":
            with open(p, "rb") as f:
                with httpx.Client(timeout=120) as client:
                    r2 = client.post(f"https://api.telegram.org/bot{token}/sendDocument",
                                     data=data, files={"document": (p.name, f)})
            if r2.status_code == 200:
                return f"Sent {p.name} via Telegram (document)"
        return f"Telegram send failed: {r.status_code} {r.text[:200]}"
    except Exception as e:
        return f"Telegram send error: {e}"


# ── iMessage ─────────────────────────────────────────────────────────────────

def imessage_send_file(path: str, recipient: str = "", message: str = "") -> str:
    """Send a file (and optional text) over iMessage via Messages.app."""
    p = _resolve(path)
    recipient = recipient or os.environ.get("OWNER_PHONE", "") or os.environ.get("DEFAULT_PHONE", "")
    if not recipient:
        return "No iMessage recipient (set OWNER_PHONE)"
    r_esc = recipient.replace('"', '\\"')
    m_esc = (message or "").replace("\\", "\\\\").replace('"', '\\"')
    f_esc = str(p).replace("\\", "\\\\").replace('"', '\\"')
    text_line = f'send "{m_esc}" to targetBuddy' if message else ""
    script = f'''
    tell application "Messages"
        set targetService to 1st account whose service type = iMessage
        set targetBuddy to participant "{r_esc}" of targetService
        {text_line}
        send POSIX file "{f_esc}" to targetBuddy
    end tell
    '''
    try:
        res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=60)
        if res.returncode == 0:
            return f"Sent {p.name} via iMessage to {recipient}"
        return f"iMessage send failed: {res.stderr.strip()[:200]}"
    except Exception as e:
        return f"iMessage send error: {e}"


# ── Email ────────────────────────────────────────────────────────────────────

def email_send_file(path: str, to: str, subject: str = "", body: str = "", cc: str = "") -> str:
    """Email a file as an attachment via Gmail."""
    p = _resolve(path)
    try:
        from .integrations_layer import gmail_send
        result = gmail_send(
            to=to,
            subject=subject or f"File: {p.name}",
            body=body or f"Attached: {p.name}",
            cc=cc,
            attachments=[str(p)],
        )
        if isinstance(result, dict) and result.get("status") == "sent":
            return f"Emailed {p.name} to {to}"
        return f"Email send failed: {result}"
    except Exception as e:
        return f"Email send error: {e}"


# ── Local ────────────────────────────────────────────────────────────────────

def open_file(path: str, app: str = "") -> str:
    """Open a file locally on the Mac (default app, or a named app)."""
    p = _resolve(path)
    cmd = ["open", str(p)] if not app else ["open", "-a", app, str(p)]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if res.returncode == 0:
        return f"Opened {p.name}" + (f" in {app}" if app else "")
    return f"Open failed: {res.stderr.strip()[:200]}"


# ── Unified entry ────────────────────────────────────────────────────────────

def deliver_file(path: str, via: str = "here", to: str = "", caption: str = "") -> str:
    """Single entry point for delivering a file anywhere."""
    via = (via or "here").lower().strip()
    if via in ("here", "chat", "conversation"):
        return outbox.queue_file(path, caption=caption)
    if via == "telegram":
        return telegram_send_file(path, caption=caption, chat_id=to)
    if via == "imessage":
        return imessage_send_file(path, recipient=to, message=caption)
    if via == "email":
        if not to:
            return "Email delivery needs a recipient (to=...)"
        return email_send_file(path, to=to, body=caption)
    if via == "open":
        return open_file(path, app=to)
    return f"Unknown delivery channel: {via} (use here|telegram|imessage|email|open)"
