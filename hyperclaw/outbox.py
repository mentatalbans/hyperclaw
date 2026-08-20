"""
Outbox — per-conversation outbound file queue.

Lets any tool (send_file, doc engines, chart engine) hand a file to the CURRENT
conversation channel without knowing which channel it is. The channel adapter
(telegram_direct, imessage_daemon_v2, email) drains the queue after each turn
and delivers the files natively (sendDocument/sendPhoto, iMessage attachment,
email attachment).

Thread-safe: tui_bridge runs turns in a thread pool, so the "current session"
is tracked per-thread.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Dict, List, Optional

_lock = threading.Lock()
_queues: Dict[int, List[dict]] = {}          # chat_id -> [{path, caption}]
_current = threading.local()                  # per-thread active chat_id

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".webm"}
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aiff"}


def set_current_session(chat_id: Optional[int]) -> None:
    """Bind the running thread to a conversation (called by tui_bridge per turn)."""
    _current.chat_id = chat_id


def get_current_session() -> Optional[int]:
    return getattr(_current, "chat_id", None)


def queue_file(path: str, caption: str = "", chat_id: Optional[int] = None) -> str:
    """Queue a file for delivery to a conversation. Returns a status string
    (tool-friendly). Falls back to the thread's current session."""
    cid = chat_id if chat_id is not None else get_current_session()
    if cid is None:
        return ("No active conversation to deliver to. Use send_file with via="
                "'telegram'/'imessage'/'email' and an explicit recipient instead.")
    p = Path(path).expanduser()
    if not p.exists():
        return f"File not found: {p}"
    if p.stat().st_size > 49 * 1024 * 1024:
        return f"File too large for chat delivery ({p.stat().st_size // (1024*1024)}MB > 49MB). Email it instead."
    with _lock:
        _queues.setdefault(cid, []).append({"path": str(p), "caption": caption or ""})
    return f"Queued {p.name} for delivery in this conversation."


def drain(chat_id: int) -> List[dict]:
    """Take and clear all queued files for a conversation."""
    with _lock:
        return _queues.pop(chat_id, [])


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
