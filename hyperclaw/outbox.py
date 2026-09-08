"""
Outbox — per-conversation outbound file queue.

Lets a tool (currently send_file / media_hub.deliver_file via='here') hand a file
to the CURRENT conversation channel without knowing which channel it is.
(Doc/chart engines do not auto-queue yet — the model must call send_file.) The channel adapter
(telegram_direct, imessage_daemon_v2, email) drains the queue after each turn
and delivers the files natively (sendDocument/sendPhoto, iMessage attachment,
email attachment).

The current session is local to each async context and follows tools dispatched
with asyncio.to_thread. A lock protects the shared outbound queues.
"""

from __future__ import annotations

import threading
import time
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Dict, List, Optional

_lock = threading.Lock()
_queues: Dict[int, List[dict]] = {}          # chat_id -> [{path, caption, queued_at}]
_current: ContextVar[Optional[int]] = ContextVar("hyperclaw_outbox_session", default=None)

MAX_QUEUE_PER_CHAT = 50    # cap so an undrained chat can't grow unbounded
ENTRY_TTL_SECONDS = 900    # stale entries (>15 min) are dropped at drain time

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".webm"}
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aiff"}


def set_current_session(chat_id: Optional[int]) -> Token:
    """Bind this async context to a conversation and return its reset token."""
    return _current.set(chat_id)


def reset_current_session(token: Token) -> None:
    """Restore the previous binding after a nested or completed turn."""
    _current.reset(token)


def get_current_session() -> Optional[int]:
    return _current.get()


def queue_file(path: str, caption: str = "", chat_id: Optional[int] = None) -> str:
    """Queue a file for delivery to a conversation. Returns a status string
    (tool-friendly). Falls back to the current async context's session."""
    cid = chat_id if chat_id is not None else get_current_session()
    if cid is None:
        return ("No active conversation to deliver to. Use send_file with via="
                "'telegram'/'imessage'/'email' and an explicit recipient instead.")
    p = Path(path).expanduser()
    try:
        size = p.stat().st_size
    except OSError:
        return f"File not found: {p}"
    if size > 49 * 1024 * 1024:
        return f"File too large for chat delivery ({size // (1024*1024)}MB > 49MB). Email it instead."
    with _lock:
        q = _queues.setdefault(cid, [])
        q.append({"path": str(p), "caption": caption or "", "queued_at": time.time()})
        if len(q) > MAX_QUEUE_PER_CHAT:
            del q[:-MAX_QUEUE_PER_CHAT]
    return f"Queued {p.name} for delivery in this conversation."


def drain(chat_id: int) -> List[dict]:
    """Take and clear all queued files for a conversation (stale entries dropped)."""
    with _lock:
        entries = _queues.pop(chat_id, [])
    cutoff = time.time() - ENTRY_TTL_SECONDS
    return [e for e in entries if e.get("queued_at", cutoff + 1) > cutoff]


def kind_of(path: str) -> str:
    """'photo' | 'video' | 'audio' | 'document' by extension."""
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXTS:
        return "photo"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return "document"
