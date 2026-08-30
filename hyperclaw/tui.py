#!/usr/bin/env python3
"""
HyperClaw Terminal — Full computer control with visible thinking.
"""

import os
import sys
import time
import json
import logging

# HTTP client libraries log every request at INFO — pure noise in a chat UI.
for _noisy in ("httpx", "httpx2", "httpcore", "httpcore2", "anthropic"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
import subprocess
import base64
import tempfile
from pathlib import Path
from datetime import datetime

import anthropic
from hyperclaw.api_utils import extract_text
import httpx

# Import vector memory
try:
    from memory.vector_memory import memory_store as vm_store, memory_search as vm_search
    from memory.vector_memory import memory_forget as vm_forget, memory_list as vm_list, memory_stats as vm_stats
    VECTOR_MEMORY_AVAILABLE = True
except ImportError:
    VECTOR_MEMORY_AVAILABLE = False
    # Provide stub functions
    def vm_store(*args, **kwargs): return "Memory not available"
    def vm_search(*args, **kwargs): return []
    def vm_forget(*args, **kwargs): return "Memory not available"
    def vm_list(*args, **kwargs): return []
    def vm_stats(*args, **kwargs): return {}

# Import agents
try:
    from agents.background_monitor import agent_start, agent_stop, agent_status, agent_add_watch
    from agents.planner import plan_create, plan_add_task, plan_status, plan_next, plan_update, plan_list, plan_delete
    from agents.transcriber import audio_transcribe, video_transcribe, audio_record, transcripts_list, transcript_get
    from agents.learning import learning_log, learning_stats, learning_patterns, learning_reflect, learning_advice, learning_feedback
    AGENTS_AVAILABLE = True
except ImportError:
    AGENTS_AVAILABLE = False
    # Provide stub functions so TUI still works
    def agent_start(*args, **kwargs): return "Agents not available"
    def agent_stop(*args, **kwargs): return "Agents not available"
    def agent_status(*args, **kwargs): return "Agents not available"
    def agent_add_watch(*args, **kwargs): return "Agents not available"
    def plan_create(*args, **kwargs): return "Planner not available"
    def plan_add_task(*args, **kwargs): return "Planner not available"
    def plan_status(*args, **kwargs): return "Planner not available"
    def plan_next(*args, **kwargs): return "Planner not available"
    def plan_update(*args, **kwargs): return "Planner not available"
    def plan_list(*args, **kwargs): return []
    def plan_delete(*args, **kwargs): return "Planner not available"
    def audio_transcribe(*args, **kwargs): return "Transcriber not available"
    def video_transcribe(*args, **kwargs): return "Transcriber not available"
    def audio_record(*args, **kwargs): return "Transcriber not available"
    def transcripts_list(*args, **kwargs): return []
    def transcript_get(*args, **kwargs): return "Transcriber not available"
    def learning_log(*args, **kwargs): return "Learning not available"
    def learning_stats(*args, **kwargs): return {}
    def learning_patterns(*args, **kwargs): return []
    def learning_reflect(*args, **kwargs): return "Learning not available"
    def learning_advice(*args, **kwargs): return "Learning not available"
    def learning_feedback(*args, **kwargs): return "Learning not available"

# Paths - use ~/.hyperclaw or HYPERCLAW_ROOT env var
HYPERCLAW_ROOT = Path(os.environ.get("HYPERCLAW_ROOT", Path.home() / ".hyperclaw"))

# Load .env file if it exists
def load_env():
    env_file = HYPERCLAW_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip().strip('"'))

load_env()

def env_var(key, default=""):
    """Get a config value, re-reading ~/.hyperclaw/.env on a miss so
    credentials added mid-session work without a restart."""
    val = os.environ.get(key, "")
    if val:
        return val
    env_file = HYPERCLAW_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith(key + "="):
                val = line.split("=", 1)[1].strip().strip('"')
                if val:
                    os.environ[key] = val
                    return val
    return default

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("HYPERCLAW_MODEL", "claude-sonnet-4-6")
WORKSPACE = HYPERCLAW_ROOT / "workspace"
MEMORY_DIR = HYPERCLAW_ROOT / "memory"
SESSION_FILE = HYPERCLAW_ROOT / "session_history.json"

# Create directories if they don't exist
WORKSPACE.mkdir(parents=True, exist_ok=True)
MEMORY_DIR.mkdir(parents=True, exist_ok=True)

def load_memory_files():
    """Load identity and memory files."""
    memory_content = []

    # Files to load in order
    files = [
        WORKSPACE / "ASSISTANT.md",
        WORKSPACE / "CONFIG.md",
        WORKSPACE / "PREFERENCES.md",
        WORKSPACE / "MEMORY.md",
        MEMORY_DIR / "instincts.md",
        MEMORY_DIR / "core-episodes.md",
    ]

    for f in files:
        if f.exists():
            try:
                content = f.read_text()[:5000]  # Limit size
                memory_content.append(f"## {f.name}\n{content}")
            except:
                pass

    # Load today's log
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    today_log = MEMORY_DIR / f"{today}.md"
    if today_log.exists():
        try:
            content = today_log.read_text()[:3000]
            memory_content.append(f"## Today's Log\n{content}")
        except:
            pass

    return "\n\n".join(memory_content)

def load_session():
    """Load previous session history."""
    if SESSION_FILE.exists():
        try:
            import json
            data = json.loads(SESSION_FILE.read_text())
            # Only load last 20 messages and clean orphaned tool results
            history = data[-20:] if len(data) > 20 else data
            # Will be cleaned after clean_history is defined
            return history
        except:
            pass
    return []

def save_session(history):
    """Save session history."""
    try:
        import json
        # Keep last 50 messages
        to_save = history[-50:] if len(history) > 50 else history
        # Atomic write: a crash mid-write must not corrupt the session file
        _tmp = SESSION_FILE.with_suffix(".json.tmp")
        _tmp.write_text(json.dumps(to_save, indent=2))
        os.replace(_tmp, SESSION_FILE)
    except:
        pass

# Load config

def _swarm_roster() -> dict:
    """Enumerate specialist swarm agents by reading class attributes — no
    instantiation, so no model/router dependencies are needed."""
    import importlib
    import inspect
    import pkgutil
    from collections import defaultdict
    try:
        import swarm.agents as _pkg
        from swarm.agents.base import BaseAgent
    except Exception as e:
        return {"error": f"swarm package unavailable: {e}"}
    seen = {}
    for mod_info in pkgutil.walk_packages(_pkg.__path__, _pkg.__name__ + "."):
        try:
            mod = importlib.import_module(mod_info.name)
        except Exception:
            continue
        for _, obj in inspect.getmembers(mod, inspect.isclass):
            if (issubclass(obj, BaseAgent) and obj is not BaseAgent
                    and obj.__module__ == mod.__name__):
                aid = getattr(obj, "agent_id", obj.__name__)
                dom = getattr(obj, "domain", "unknown")
                seen[f"{dom}.{aid}"] = {
                    "agent_id": aid,
                    "domain": dom,
                    "description": getattr(obj, "description", ""),
                    "task_types": getattr(obj, "supported_task_types", []),
                }
    domains = defaultdict(int)
    for a in seen.values():
        domains[a["domain"]] += 1
    return {"count": len(seen), "domains": dict(domains),
            "agents": sorted(seen.values(), key=lambda a: (a["domain"], a["agent_id"]))}


def load_config():
    """Load config.json with user/AI names."""
    config_path = HYPERCLAW_ROOT / "config.json"
    if config_path.exists():
        try:
            return json.loads(config_path.read_text())
        except:
            pass
    return {"user_name": "User", "ai_name": "Assistant"}

CONFIG = load_config()
AI_NAME = CONFIG.get("ai_name", "Assistant")
USER_NAME = CONFIG.get("user_name", "User")

# Default system prompt
DEFAULT_SYSTEM = f"""You are {AI_NAME}, a powerful AI assistant with full computer control.
You have access to the terminal, files, web, and can automate tasks on this machine.

You can:
- Run any bash command
- Read/write/edit files
- Browse the web and fetch pages
- Control macOS apps via AppleScript
- Take screenshots to see the screen
- Search the web
- Open URLs in browser

Be helpful, precise, and proactive. Think through your approach, then execute.
You have persistent memory across sessions."""

# Load custom persona if available
def get_system_base():
    """Load system prompt from CLAUDE.md or fallback files."""
    # First try CLAUDE.md (personalized during onboarding)
    claude_md = HYPERCLAW_ROOT / "CLAUDE.md"
    if claude_md.exists():
        try:
            return claude_md.read_text()[:6000]
        except:
            pass
    # Fallback to workspace files
    for filename in ["ASSISTANT.md", "CONFIG.md", "SYSTEM.md"]:
        filepath = WORKSPACE / filename
        if filepath.exists():
            try:
                return filepath.read_text()[:4000]
            except:
                pass
    return DEFAULT_SYSTEM

SYSTEM_BASE = get_system_base()

TOOLS = [
    {
        "name": "bash",
        "description": "Execute a bash command. Use for any system task, installing packages, git, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The bash command to run"}
            },
            "required": ["command"]
        }
    },
    {
        "name": "read_file",
        "description": "Read contents of a file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to file"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "write_file",
        "description": "Write content to a file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to file"},
                "content": {"type": "string", "description": "Content to write"}
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "edit_file",
        "description": "Edit a file by replacing old text with new text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to file"},
                "old_text": {"type": "string", "description": "Text to find"},
                "new_text": {"type": "string", "description": "Replacement text"}
            },
            "required": ["path", "old_text", "new_text"]
        }
    },
    {
        "name": "web_fetch",
        "description": "Fetch a webpage and return its content. Use for reading websites, APIs, documentation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch"},
                "method": {"type": "string", "description": "HTTP method (GET, POST)", "default": "GET"},
                "headers": {"type": "object", "description": "Optional headers"},
                "body": {"type": "string", "description": "Optional request body for POST"}
            },
            "required": ["url"]
        }
    },
    {
        "name": "web_search",
        "description": "Search the web using DuckDuckGo. Returns top results.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "screenshot",
        "description": "Take a screenshot of the screen. Returns description of what's visible.",
        "input_schema": {
            "type": "object",
            "properties": {
                "region": {"type": "string", "description": "Optional: 'full' (default), or 'x,y,w,h' for region"}
            }
        }
    },
    {
        "name": "open_url",
        "description": "Open a URL in the default browser.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to open"}
            },
            "required": ["url"]
        }
    },
    {
        "name": "open_app",
        "description": "Open a macOS application.",
        "input_schema": {
            "type": "object",
            "properties": {
                "app_name": {"type": "string", "description": "Application name (e.g., 'Safari', 'Terminal', 'Finder')"}
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "applescript",
        "description": "Run AppleScript for GUI automation. Can control any Mac app, click buttons, type text, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "AppleScript code to execute"}
            },
            "required": ["script"]
        }
    },
    {
        "name": "type_text",
        "description": "Type text using keyboard simulation. Types into whatever app is focused.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to type"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "key_press",
        "description": "Press a key or key combination (e.g., 'return', 'command+s', 'command+shift+4').",
        "input_schema": {
            "type": "object",
            "properties": {
                "keys": {"type": "string", "description": "Key(s) to press"}
            },
            "required": ["keys"]
        }
    },
    {
        "name": "mouse_click",
        "description": "Click at screen coordinates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate"},
                "y": {"type": "integer", "description": "Y coordinate"},
                "button": {"type": "string", "description": "'left' (default), 'right', or 'double'"}
            },
            "required": ["x", "y"]
        }
    },
    {
        "name": "notification",
        "description": "Show a macOS notification.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Notification title"},
                "message": {"type": "string", "description": "Notification message"}
            },
            "required": ["title", "message"]
        }
    },
    {
        "name": "clipboard_read",
        "description": "Read current clipboard contents.",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "clipboard_write",
        "description": "Write text to clipboard.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to copy to clipboard"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "download",
        "description": "Download a file from URL to local path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to download"},
                "path": {"type": "string", "description": "Local path to save file"}
            },
            "required": ["url", "path"]
        }
    },
    {
        "name": "telegram",
        "description": "Send a Telegram message, optionally with a file attached (photo, PDF, doc, video - auto-detected).",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message to send"},
                "file": {"type": "string", "description": "Optional path to a file to send (photo/document/video/audio auto-detected)"}
            },
            "required": ["message"]
        }
    },
    {
        "name": "imessage",
        "description": "Send an iMessage to a contact, optionally with a file/photo attached. Use for casual, human-like texting.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message to send"},
                "recipient": {"type": "string", "description": "Phone number or email."},
                "file": {"type": "string", "description": "Optional path to a file/photo to attach"}
            },
            "required": ["message"]
        }
    },
    {
        "name": "send_file",
        "description": "Deliver ANY file (document, PDF, deck, spreadsheet, image, chart) to a conversation or channel. via='here' sends it back in the CURRENT conversation (Telegram/iMessage - use this when the user asks for a file in chat). Other options: telegram, imessage, email, open (opens locally on the Mac).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to deliver"},
                "via": {"type": "string", "enum": ["here", "telegram", "imessage", "email", "open"], "description": "Delivery channel. Default 'here' = current conversation."},
                "to": {"type": "string", "description": "Recipient: email address (via=email), phone (via=imessage), chat_id (via=telegram), or app name (via=open). Optional otherwise."},
                "caption": {"type": "string", "description": "Caption/message to accompany the file"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "open_file",
        "description": "Open a file locally on the Mac in its default app (or a named app, e.g. Preview, Microsoft Word, Keynote).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File to open"},
                "app": {"type": "string", "description": "Optional app name to open it with"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "imessage_read",
        "description": "Read recent iMessages from a conversation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "recipient": {"type": "string", "description": "Phone number or email to read messages from"},
                "count": {"type": "integer", "description": "Number of recent messages to read (default 10)"}
            }
        }
    },
    {
        "name": "speak",
        "description": "Speak text aloud using ElevenLabs TTS (George voice).",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to speak"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "vision",
        "description": "Analyze an image file and describe what you see. Use after taking a screenshot to see the screen.",
        "input_schema": {
            "type": "object",
            "properties": {
                "image_path": {"type": "string", "description": "Path to image file"},
                "question": {"type": "string", "description": "What to look for or analyze"}
            },
            "required": ["image_path"]
        }
    },
    {
        "name": "email_read",
        "description": "Read recent emails from Gmail inbox.",
        "input_schema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "Number of emails to fetch (default 5)"}
            }
        }
    },
    {
        "name": "email_send",
        "description": "Send an email via Gmail with optional HTML body, attachments, cc/bcc, and in-thread replies. Set CC_EMAIL env to auto-CC an address.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email"},
                "subject": {"type": "string", "description": "Email subject"},
                "body": {"type": "string", "description": "Plain-text body (always required)"},
                "body_html": {"type": "string", "description": "Optional HTML body for rich formatting (headings, tables, bold)"},
                "thread_id": {"type": "string", "description": "Thread ID to reply in-thread"},
                "message_id": {"type": "string", "description": "Message-ID for the In-Reply-To header"},
                "cc": {"type": "string"},
                "bcc": {"type": "string"},
                "attachments": {"type": "array", "items": {"type": "string"}, "description": "File paths to attach"}
            },
            "required": ["to", "subject", "body"]
        }
    },
    {
        "name": "email_search",
        "description": "Search Gmail with a query (Gmail search syntax: from:, subject:, newer_than:, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Gmail search query"},
                "count": {"type": "integer", "description": "Max results (default 10)"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "email_forward",
        "description": "Forward an email (body + attachments) to someone, with an optional note on top.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "Message ID from email_read/email_search"},
                "to": {"type": "string", "description": "Recipient email address"},
                "note": {"type": "string", "description": "Optional note above the forwarded content"},
                "include_attachments": {"type": "boolean", "description": "Forward original attachments too (default true)"}
            },
            "required": ["message_id", "to"]
        }
    },
    {
        "name": "email_draft",
        "description": "Create a Gmail DRAFT (lands in the Drafts folder for review) instead of sending directly. Use when approval is wanted before sending.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient"},
                "subject": {"type": "string"},
                "body": {"type": "string", "description": "Plain-text body"},
                "body_html": {"type": "string", "description": "Optional HTML version for rich formatting"},
                "cc": {"type": "string"},
                "attachments": {"type": "array", "items": {"type": "string"}, "description": "File paths to attach"},
                "thread_id": {"type": "string", "description": "Thread ID to draft a reply into"}
            },
            "required": ["to", "subject", "body"]
        }
    },
    {
        "name": "email_mark",
        "description": "Inbox management: mark an email read/unread, archive/unarchive, star/unstar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
                "mark_read": {"type": "boolean"},
                "archive": {"type": "boolean"},
                "star": {"type": "boolean"}
            },
            "required": ["message_id"]
        }
    },
    {
        "name": "calendar_read",
        "description": "Read upcoming calendar events.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Number of days ahead to check (default 7)"}
            }
        }
    },
    {
        "name": "calendar_create",
        "description": "Create a calendar event.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Event title"},
                "start": {"type": "string", "description": "Start datetime (YYYY-MM-DD HH:MM)"},
                "end": {"type": "string", "description": "End datetime (YYYY-MM-DD HH:MM)"},
                "location": {"type": "string", "description": "Optional location"}
            },
            "required": ["title", "start", "end"]
        }
    },
    # === AGI-LEVEL CAPABILITIES ===
    {
        "name": "python_exec",
        "description": "Execute Python code and return the result. Use for calculations, data processing, or any programmatic task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute"}
            },
            "required": ["code"]
        }
    },
    {
        "name": "github",
        "description": "Interact with GitHub API. Actions: repos, issues, prs, search_code, create_issue, user_info.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "Action: repos, issues, prs, search_code, create_issue, user_info"},
                "owner": {"type": "string", "description": "Repo owner/org"},
                "repo": {"type": "string", "description": "Repository name"},
                "query": {"type": "string", "description": "Search query or issue body"},
                "title": {"type": "string", "description": "Issue title (for create_issue)"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "wikipedia",
        "description": "Search and read Wikipedia articles.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term or article title"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "weather",
        "description": "Get current weather for a location.",
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "City name or coordinates"}
            },
            "required": ["location"]
        }
    },
    {
        "name": "news",
        "description": "Get latest news headlines. Sources: top, tech, business, science, sports.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Category: top, tech, business, science, sports"},
                "query": {"type": "string", "description": "Optional search query"}
            }
        }
    },
    {
        "name": "stock_price",
        "description": "Get stock or crypto price.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Stock ticker (AAPL) or crypto (BTC)"}
            },
            "required": ["symbol"]
        }
    },
    {
        "name": "translate",
        "description": "Translate text between languages.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to translate"},
                "to_lang": {"type": "string", "description": "Target language code (es, fr, de, zh, ja, etc)"},
                "from_lang": {"type": "string", "description": "Source language (auto-detect if omitted)"}
            },
            "required": ["text", "to_lang"]
        }
    },
    {
        "name": "youtube",
        "description": "Get YouTube video info or transcript.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "YouTube video URL"},
                "get_transcript": {"type": "boolean", "description": "Whether to fetch transcript"}
            },
            "required": ["url"]
        }
    },
    {
        "name": "reddit",
        "description": "Get Reddit posts from a subreddit.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subreddit": {"type": "string", "description": "Subreddit name (without r/)"},
                "sort": {"type": "string", "description": "Sort: hot, new, top"},
                "limit": {"type": "integer", "description": "Number of posts (default 10)"}
            },
            "required": ["subreddit"]
        }
    },
    {
        "name": "hackernews",
        "description": "Get Hacker News top/new stories.",
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "description": "Type: top, new, best, ask, show"},
                "limit": {"type": "integer", "description": "Number of stories (default 10)"}
            }
        }
    },
    {
        "name": "arxiv",
        "description": "Search arXiv for research papers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "description": "Max results (default 5)"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "math",
        "description": "Evaluate mathematical expressions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "Math expression (e.g., '2+2', 'sqrt(16)', 'sin(pi/2)')"}
            },
            "required": ["expression"]
        }
    },
    {
        "name": "time_info",
        "description": "Get current time in different timezones.",
        "input_schema": {
            "type": "object",
            "properties": {
                "timezone": {"type": "string", "description": "Timezone (e.g., 'America/New_York', 'UTC', 'Asia/Tokyo')"}
            }
        }
    },
    {
        "name": "dns_lookup",
        "description": "DNS lookup for a domain.",
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Domain name"},
                "record_type": {"type": "string", "description": "Record type: A, AAAA, MX, TXT, NS, CNAME"}
            },
            "required": ["domain"]
        }
    },
    {
        "name": "whois",
        "description": "WHOIS lookup for domain registration info.",
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Domain name"}
            },
            "required": ["domain"]
        }
    },
    {
        "name": "hash",
        "description": "Generate hash of text (md5, sha1, sha256, sha512).",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to hash"},
                "algorithm": {"type": "string", "description": "Algorithm: md5, sha1, sha256, sha512"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "uuid_generate",
        "description": "Generate a UUID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "version": {"type": "integer", "description": "UUID version: 1 or 4 (default 4)"}
            }
        }
    },
    {
        "name": "base64_encode",
        "description": "Encode or decode base64.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to encode/decode"},
                "decode": {"type": "boolean", "description": "True to decode, False to encode"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "json_format",
        "description": "Parse, format, or query JSON data.",
        "input_schema": {
            "type": "object",
            "properties": {
                "json_str": {"type": "string", "description": "JSON string"},
                "query": {"type": "string", "description": "Optional JSONPath query (e.g., '$.data[0].name')"}
            },
            "required": ["json_str"]
        }
    },
    {
        "name": "regex_test",
        "description": "Test a regex pattern against text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern"},
                "text": {"type": "string", "description": "Text to test against"},
                "find_all": {"type": "boolean", "description": "Find all matches vs first match"}
            },
            "required": ["pattern", "text"]
        }
    },
    {
        "name": "pdf_read",
        "description": "Extract text from a PDF file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to PDF file"},
                "pages": {"type": "string", "description": "Page range (e.g., '1-5', 'all')"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "image_generate",
        "description": "Generate an image using AI (requires OPENAI_API_KEY for DALL-E).",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Image description"},
                "size": {"type": "string", "description": "Size: 1024x1024, 512x512, 256x256"},
                "save_path": {"type": "string", "description": "Path to save image"}
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "geocode",
        "description": "Convert address to coordinates or vice versa.",
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Address to geocode"},
                "lat": {"type": "number", "description": "Latitude for reverse geocoding"},
                "lon": {"type": "number", "description": "Longitude for reverse geocoding"}
            }
        }
    },
    {
        "name": "currency",
        "description": "Convert between currencies.",
        "input_schema": {
            "type": "object",
            "properties": {
                "amount": {"type": "number", "description": "Amount to convert"},
                "from_currency": {"type": "string", "description": "Source currency (USD, EUR, etc)"},
                "to_currency": {"type": "string", "description": "Target currency"}
            },
            "required": ["amount", "from_currency", "to_currency"]
        }
    },
    {
        "name": "unit_convert",
        "description": "Convert between units (length, weight, temperature, etc).",
        "input_schema": {
            "type": "object",
            "properties": {
                "value": {"type": "number", "description": "Value to convert"},
                "from_unit": {"type": "string", "description": "Source unit"},
                "to_unit": {"type": "string", "description": "Target unit"}
            },
            "required": ["value", "from_unit", "to_unit"]
        }
    },
    {
        "name": "qr_code",
        "description": "Generate a QR code image.",
        "input_schema": {
            "type": "object",
            "properties": {
                "data": {"type": "string", "description": "Data to encode"},
                "save_path": {"type": "string", "description": "Path to save QR code image"}
            },
            "required": ["data"]
        }
    },
    {
        "name": "wayback",
        "description": "Get archived versions of a URL from Wayback Machine.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to look up"}
            },
            "required": ["url"]
        }
    },
    {
        "name": "slack",
        "description": "Send a message to Slack (requires SLACK_WEBHOOK_URL).",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message to send"},
                "channel": {"type": "string", "description": "Channel name (optional)"}
            },
            "required": ["message"]
        }
    },
    {
        "name": "discord",
        "description": "Send a message to Discord (requires DISCORD_WEBHOOK_URL).",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message to send"}
            },
            "required": ["message"]
        }
    },
    {
        "name": "sql_query",
        "description": "Execute SQL query on a SQLite database.",
        "input_schema": {
            "type": "object",
            "properties": {
                "database": {"type": "string", "description": "Path to SQLite database file"},
                "query": {"type": "string", "description": "SQL query to execute"}
            },
            "required": ["database", "query"]
        }
    },
    {
        "name": "http_request",
        "description": "Make a custom HTTP request with full control over method, headers, body.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Request URL"},
                "method": {"type": "string", "description": "HTTP method: GET, POST, PUT, DELETE, PATCH"},
                "headers": {"type": "object", "description": "Request headers"},
                "body": {"type": "string", "description": "Request body"},
                "json_body": {"type": "object", "description": "JSON body (alternative to body)"}
            },
            "required": ["url"]
        }
    },
    # === VECTOR MEMORY ===
    {
        "name": "memory_store",
        "description": "Store something in long-term vector memory for future recall. Use for important facts, decisions, preferences, or anything worth remembering.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "What to remember"},
                "domain": {"type": "string", "description": "Category: work, personal, technical, finance, etc."},
                "source": {"type": "string", "description": "Source: conversation, observation, research, etc."}
            },
            "required": ["content"]
        }
    },
    {
        "name": "memory_search",
        "description": "Search long-term memory semantically. Finds relevant memories even with different wording.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for"},
                "limit": {"type": "integer", "description": "Max results (default 5)"},
                "domain": {"type": "string", "description": "Filter by domain"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "memory_forget",
        "description": "Delete a memory by ID or by semantic search.",
        "input_schema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "description": "Memory ID to delete"},
                "query": {"type": "string", "description": "Or search and delete matching memory"}
            }
        }
    },
    {
        "name": "memory_list",
        "description": "List recent memories.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of memories (default 20)"},
                "domain": {"type": "string", "description": "Filter by domain"}
            }
        }
    },
    {
        "name": "memory_stats",
        "description": "Get memory statistics - total count, by source, by domain.",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    # === BACKGROUND AGENTS ===
    {
        "name": "agent_start",
        "description": "Start background monitoring agents (email, markets, news). They run continuously and alert via Telegram.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Agent name: email_monitor, market_monitor, news_monitor. Omit to start all."}
            }
        }
    },
    {
        "name": "agent_stop",
        "description": "Stop background monitoring agents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Agent name to stop. Omit to stop all."}
            }
        }
    },
    {
        "name": "agent_status",
        "description": "Get status of the 3 background monitoring agents (email, market, news). For the full 44-agent specialist swarm, use swarm_roster.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "swarm_roster",
        "description": "List all specialist swarm agents (44 across business, personal, scientific, comms, talent, trading, tech, recursive, intelligence, creative domains) with their IDs and descriptions.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "agent_watch",
        "description": "Add a watch target to an agent (email sender, stock symbol, news topic).",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_type": {"type": "string", "description": "email, market, or news"},
                "target": {"type": "string", "description": "What to watch (email address, ticker symbol, or topic)"}
            },
            "required": ["agent_type", "target"]
        }
    },
    # === PLANNING SYSTEM ===
    {
        "name": "plan_create",
        "description": "Create a plan to achieve a goal. Automatically breaks down into tasks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The goal to achieve"},
                "tasks": {"type": "array", "description": "Optional: explicit task list with title, description, depends_on"}
            },
            "required": ["goal"]
        }
    },
    {
        "name": "plan_status",
        "description": "Get status of a plan and its tasks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "Plan ID"}
            },
            "required": ["plan_id"]
        }
    },
    {
        "name": "plan_next",
        "description": "Get the next tasks ready to execute in a plan.",
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "Plan ID"}
            },
            "required": ["plan_id"]
        }
    },
    {
        "name": "plan_update",
        "description": "Update a task status in a plan.",
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "Plan ID"},
                "task_id": {"type": "string", "description": "Task ID"},
                "status": {"type": "string", "description": "Status: in_progress, completed, failed, retry"},
                "result": {"type": "string", "description": "Result or error message"}
            },
            "required": ["plan_id", "task_id", "status"]
        }
    },
    {
        "name": "plan_list",
        "description": "List all plans.",
        "input_schema": {"type": "object", "properties": {}}
    },
    # === AUDIO/VIDEO TRANSCRIPTION ===
    {
        "name": "transcribe_audio",
        "description": "Transcribe an audio file using OpenAI Whisper.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to audio file (mp3, wav, m4a, etc.)"},
                "language": {"type": "string", "description": "Language code (en, es, etc.) or omit for auto-detect"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "transcribe_video",
        "description": "Transcribe a video file (extracts audio first).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to video file"},
                "language": {"type": "string", "description": "Language code or omit for auto-detect"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "record_audio",
        "description": "Record audio from the microphone.",
        "input_schema": {
            "type": "object",
            "properties": {
                "duration": {"type": "integer", "description": "Recording duration in seconds (default 30)"}
            }
        }
    },
    {
        "name": "transcripts_list",
        "description": "List all saved transcripts.",
        "input_schema": {"type": "object", "properties": {}}
    },
    # === SELF-IMPROVEMENT / LEARNING ===
    {
        "name": "learning_log",
        "description": "Log an action and its outcome for learning.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "Type of action taken"},
                "detail": {"type": "string", "description": "What was done"},
                "success": {"type": "boolean", "description": "Whether it succeeded"},
                "feedback": {"type": "string", "description": "User feedback if any"}
            },
            "required": ["action", "detail", "success"]
        }
    },
    {
        "name": "learning_stats",
        "description": "Get learning statistics - success rates, patterns, recent failures.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Number of days to analyze (default 7)"}
            }
        }
    },
    {
        "name": "learning_reflect",
        "description": "Generate a reflection on recent performance with insights and improvements.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "description": "Period: day, week, or month"}
            }
        }
    },
    {
        "name": "learning_advice",
        "description": "Get advice based on past learnings for a type of action.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action_type": {"type": "string", "description": "Type of action to get advice for"}
            },
            "required": ["action_type"]
        }
    },
    {
        "name": "learning_feedback",
        "description": "Record feedback on the most recent action.",
        "input_schema": {
            "type": "object",
            "properties": {
                "feedback": {"type": "string", "description": "Feedback to record"}
            },
            "required": ["feedback"]
        }
    }
]

HISTORY = []
SYSTEM = SYSTEM_BASE  # Will be updated with memories on startup

# Colors
CYAN = "\033[96m"
MAGENTA = "\033[95m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
RED = "\033[91m"
BLUE = "\033[94m"
DIM = "\033[90m"
BOLD = "\033[1m"
RESET = "\033[0m"

def print_thinking(text):
    print(f"\n{DIM}┌─ Thinking ─────────────────────────────────────────{RESET}")
    for line in text.split('\n'):
        print(f"{DIM}│ {line}{RESET}")
    print(f"{DIM}└────────────────────────────────────────────────────{RESET}")

def print_tool(name, details):
    print(f"\n{CYAN}{BOLD}● {name}{RESET}")
    if details:
        print(f"{CYAN}  {details[:200]}{'...' if len(str(details)) > 200 else ''}{RESET}")

def print_code(content, path=None):
    header = f" Writing: {path} " if path else " Code "
    print(f"\n{GREEN}┌─{header}{'─' * max(1, 50 - len(header))}{RESET}")
    lines = content.split('\n')
    for i, line in enumerate(lines[:30], 1):
        print(f"{GREEN}│{DIM}{i:4}{RESET} {line}")
    if len(lines) > 30:
        print(f"{GREEN}│ {DIM}... ({len(lines) - 30} more lines){RESET}")
    print(f"{GREEN}└{'─' * 52}{RESET}")

def print_output(text, color=DIM):
    lines = str(text).strip().split('\n')
    for line in lines[:15]:
        print(f"{color}  {line[:120]}{RESET}")
    if len(lines) > 15:
        print(f"{color}  ... ({len(lines) - 15} more lines){RESET}")

# Tool implementations

def run_bash(command):
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=120, cwd=os.path.expanduser("~"))
        return (result.stdout + result.stderr)[:10000] or "(no output)"
    except subprocess.TimeoutExpired:
        return "Timed out"
    except Exception as e:
        return f"Error: {e}"

def read_file(path):
    try:
        p = Path(path).expanduser()
        return p.read_text()[:50000] if p.exists() else f"Not found: {path}"
    except Exception as e:
        return f"Error: {e}"

def write_file(path, content):
    try:
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"Written {len(content)} bytes"
    except Exception as e:
        return f"Error: {e}"

def edit_file(path, old_text, new_text):
    try:
        p = Path(path).expanduser()
        if not p.exists():
            return f"Not found: {path}"
        content = p.read_text()
        if old_text not in content:
            return "Text not found"
        p.write_text(content.replace(old_text, new_text, 1))
        return "Edited"
    except Exception as e:
        return f"Error: {e}"

def web_fetch(url, method="GET", headers=None, body=None):
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            if method.upper() == "POST":
                resp = client.post(url, headers=headers, content=body)
            else:
                resp = client.get(url, headers=headers)
            # Try to extract text content
            content = resp.text[:20000]
            return f"Status: {resp.status_code}\n\n{content}"
    except Exception as e:
        return f"Error: {e}"

def web_search(query):
    try:
        # Use DuckDuckGo HTML search
        url = f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            # Extract result snippets (basic parsing)
            text = resp.text
            results = []
            import re
            for match in re.finditer(r'class="result__a"[^>]*href="([^"]*)"[^>]*>([^<]*)', text):
                results.append(f"- {match.group(2).strip()}: {match.group(1)}")
                if len(results) >= 8:
                    break
            return "\n".join(results) if results else "No results found"
    except Exception as e:
        return f"Error: {e}"

def screenshot(region="full"):
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        if region == "full":
            subprocess.run(["screencapture", "-x", tmp.name], check=True)
        else:
            subprocess.run(["screencapture", "-x", "-R", region, tmp.name], check=True)
        # Return path - in future could send to vision API
        return f"Screenshot saved: {tmp.name}"
    except Exception as e:
        return f"Error: {e}"

def open_url(url):
    try:
        subprocess.run(["open", url], check=True)
        return f"Opened: {url}"
    except Exception as e:
        return f"Error: {e}"

def open_app(app_name):
    try:
        subprocess.run(["open", "-a", app_name], check=True)
        return f"Opened: {app_name}"
    except Exception as e:
        return f"Error: {e}"

def applescript(script):
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
        return result.stdout or result.stderr or "Done"
    except Exception as e:
        return f"Error: {e}"

def type_text(text):
    # Use AppleScript to type
    escaped = text.replace('\\', '\\\\').replace('"', '\\"')
    script = f'tell application "System Events" to keystroke "{escaped}"'
    return applescript(script)

def key_press(keys):
    # Parse key combo like "command+s"
    parts = keys.lower().split("+")
    key = parts[-1]
    modifiers = parts[:-1]

    modifier_map = {
        "command": "command down",
        "cmd": "command down",
        "shift": "shift down",
        "option": "option down",
        "alt": "option down",
        "control": "control down",
        "ctrl": "control down"
    }

    key_map = {
        "return": "return", "enter": "return",
        "tab": "tab", "escape": "escape", "esc": "escape",
        "space": "space", "delete": "delete", "backspace": "delete",
        "up": "up arrow", "down": "down arrow",
        "left": "left arrow", "right": "right arrow"
    }

    key_code = key_map.get(key, key)
    mods = ", ".join([modifier_map.get(m, "") for m in modifiers if m in modifier_map])

    if mods:
        script = f'tell application "System Events" to key code (key code of "{key_code}") using {{{mods}}}'
    else:
        script = f'tell application "System Events" to keystroke "{key_code}"'

    return applescript(script)

def mouse_click(x, y, button="left"):
    # Use cliclick if available, otherwise AppleScript
    try:
        if button == "double":
            subprocess.run(["cliclick", f"dc:{x},{y}"], check=True)
        elif button == "right":
            subprocess.run(["cliclick", f"rc:{x},{y}"], check=True)
        else:
            subprocess.run(["cliclick", f"c:{x},{y}"], check=True)
        return f"Clicked at ({x}, {y})"
    except FileNotFoundError:
        # Fallback to AppleScript (less reliable)
        script = f'''
        do shell script "python3 -c \\"
import Quartz
event = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, ({x}, {y}), 0)
Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
event = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp, ({x}, {y}), 0)
Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
\\""
        '''
        return applescript(script)
    except Exception as e:
        return f"Error: {e}"

def notification(title, message):
    script = f'display notification "{message}" with title "{title}"'
    return applescript(script)

def clipboard_read():
    try:
        result = subprocess.run(["pbpaste"], capture_output=True, text=True)
        return result.stdout or "(clipboard empty)"
    except Exception as e:
        return f"Error: {e}"

def clipboard_write(text):
    try:
        subprocess.run(["pbcopy"], input=text, text=True, check=True)
        return f"Copied {len(text)} chars to clipboard"
    except Exception as e:
        return f"Error: {e}"

def download(url, path):
    try:
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        with httpx.Client(timeout=60, follow_redirects=True) as client:
            resp = client.get(url)
            p.write_bytes(resp.content)
        return f"Downloaded {len(resp.content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"

def telegram(message, file=None):
    """Send Telegram message, optionally with a file."""
    if file:
        try:
            from hyperclaw.media_hub import telegram_send_file
            return telegram_send_file(file, caption=message)
        except Exception as e:
            return f"Error sending file: {e}"
    try:
        # Load bot token and chat ID from env or config
        bot_token = env_var("TELEGRAM_BOT_TOKEN")
        chat_id = env_var("TELEGRAM_CHAT_ID")

        if not bot_token:
            # Try loading from secrets
            secrets_file = HYPERCLAW_ROOT / "workspace" / "secrets" / ".env"
            if secrets_file.exists():
                for line in secrets_file.read_text().splitlines():
                    if line.startswith("TELEGRAM_BOT_TOKEN="):
                        bot_token = line.split("=", 1)[1].strip().strip('"')
                    if line.startswith("TELEGRAM_CHAT_ID="):
                        chat_id = line.split("=", 1)[1].strip().strip('"')

        if not bot_token:
            return "TELEGRAM_BOT_TOKEN not configured"
        if not chat_id:
            return "TELEGRAM_CHAT_ID not configured"

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        with httpx.Client(timeout=10) as client:
            resp = client.post(url, json={"chat_id": chat_id, "text": message})
            if resp.status_code == 200:
                return "Message sent"
            return f"Telegram error: {resp.text}"
    except Exception as e:
        return f"Error: {e}"

def imessage_send(message, recipient=None, file=None):
    """Send an iMessage via AppleScript, optionally with a file attachment."""
    if file:
        try:
            from hyperclaw.media_hub import imessage_send_file
            return imessage_send_file(file, recipient=recipient or "", message=message)
        except Exception as e:
            return f"Error sending file: {e}"
    try:
        # Default to configured phone number
        if not recipient:
            recipient = os.environ.get("DEFAULT_PHONE", "")
        if not recipient:
            return "No recipient specified and DEFAULT_PHONE not configured"

        # Escape quotes for AppleScript
        message_escaped = message.replace('"', '\\"').replace("'", "'\\''")
        recipient_escaped = recipient.replace('"', '\\"')

        # AppleScript to send iMessage
        script = f'''
        tell application "Messages"
            set targetService to 1st account whose service type = iMessage
            set targetBuddy to participant "{recipient_escaped}" of targetService
            send "{message_escaped}" to targetBuddy
        end tell
        '''

        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=30
        )

        if result.returncode == 0:
            return f"iMessage sent to {recipient}"

        # Fallback: try alternate AppleScript syntax
        script2 = f'''
        tell application "Messages"
            send "{message_escaped}" to buddy "{recipient_escaped}" of (service 1 whose service type is iMessage)
        end tell
        '''

        result2 = subprocess.run(
            ["osascript", "-e", script2],
            capture_output=True, text=True, timeout=30
        )

        if result2.returncode == 0:
            return f"iMessage sent to {recipient}"

        # Final fallback: use chat window approach
        script3 = f'''
        tell application "Messages"
            activate
            delay 0.5
        end tell
        tell application "System Events"
            tell process "Messages"
                keystroke "n" using command down
                delay 0.5
                keystroke "{recipient_escaped}"
                delay 0.5
                keystroke return
                delay 0.5
                keystroke "{message_escaped}"
                delay 0.3
                keystroke return
            end tell
        end tell
        '''

        result3 = subprocess.run(
            ["osascript", "-e", script3],
            capture_output=True, text=True, timeout=30
        )

        if result3.returncode == 0:
            return f"iMessage sent to {recipient} (via UI automation)"

        return f"Failed to send: {result.stderr or result2.stderr or result3.stderr}"

    except Exception as e:
        return f"Error: {e}"

def imessage_read(recipient=None, count=10):
    """Read recent iMessages from a conversation."""
    try:
        # Query the Messages sqlite database directly
        db_path = Path.home() / "Library" / "Messages" / "chat.db"

        if not db_path.exists():
            return "Messages database not accessible"

        import sqlite3
        conn = sqlite3.connect(str(db_path))

        query = """
        SELECT
            datetime(m.date/1000000000 + 978307200, 'unixepoch', 'localtime') as date,
            CASE WHEN m.is_from_me = 1 THEN 'Me' ELSE h.id END as sender,
            m.text
        FROM message m
        LEFT JOIN handle h ON m.handle_id = h.ROWID
        WHERE m.text IS NOT NULL
        """

        if recipient:
            query += f" AND h.id LIKE '%{recipient}%'"

        query += f" ORDER BY m.date DESC LIMIT {count}"

        cursor = conn.execute(query)
        messages = cursor.fetchall()
        conn.close()

        if not messages:
            return "No messages found"

        result = []
        for date, sender, text in reversed(messages):
            result.append(f"[{date}] {sender}: {text}")

        return "\n".join(result)

    except sqlite3.OperationalError as e:
        if "database is locked" in str(e):
            return "Messages database is locked (Messages app may be using it)"
        return f"Database error: {e}"
    except Exception as e:
        return f"Error: {e}"

def speak(text):
    """Text-to-speech using ElevenLabs."""
    try:
        api_key = env_var("ELEVENLABS_API_KEY")
        if not api_key:
            secrets_file = HYPERCLAW_ROOT / "workspace" / "secrets" / ".env"
            if secrets_file.exists():
                for line in secrets_file.read_text().splitlines():
                    if line.startswith("ELEVENLABS_API_KEY="):
                        api_key = line.split("=", 1)[1].strip().strip('"')

        if not api_key:
            # Fallback to macOS say
            subprocess.run(["say", text])
            return "Spoke using macOS voice"

        voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # George
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

        with httpx.Client(timeout=30) as client:
            resp = client.post(
                url,
                headers={"xi-api-key": api_key, "Content-Type": "application/json"},
                json={
                    "text": text,
                    "model_id": "eleven_turbo_v2_5",
                    "voice_settings": {"stability": 0.35, "similarity_boost": 0.75}
                }
            )
            if resp.status_code == 200:
                # Save and play audio
                audio_file = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
                audio_file.write(resp.content)
                audio_file.close()
                subprocess.run(["afplay", audio_file.name])
                return "Spoke with George voice"
            return f"ElevenLabs error: {resp.status_code}"
    except Exception as e:
        return f"Error: {e}"

def vision(image_path, question="Describe what you see in detail."):
    """Analyze image using Claude's vision."""
    try:
        p = Path(image_path).expanduser()
        if not p.exists():
            return f"Image not found: {image_path}"

        image_data = base64.b64encode(p.read_bytes()).decode()
        media_type = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"

        client = anthropic.Anthropic(api_key=API_KEY)
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_data}},
                    {"type": "text", "text": question}
                ]
            }]
        )
        return extract_text(response)
    except Exception as e:
        return f"Error: {e}"

def email_read(count=5):
    """Read recent emails using AppleScript (Mail.app)."""
    try:
        script = f'''
        tell application "Mail"
            set output to ""
            set msgs to messages 1 thru {count} of inbox
            repeat with m in msgs
                set output to output & "---" & return
                set output to output & "From: " & (sender of m) & return
                set output to output & "Subject: " & (subject of m) & return
                set output to output & "Date: " & (date received of m) & return
                set output to output & return
            end repeat
            return output
        end tell
        '''
        return applescript(script)
    except Exception as e:
        return f"Error: {e}"

def email_send(to, subject, body, thread_id=None, message_id=None, cc='', bcc='', attachments=None, body_html=None):
    """Send email via Gmail API. thread_id => reply IN-THREAD. Set CC_EMAIL env to auto-CC."""
    try:
        from hyperclaw.integrations_layer import gmail_send
        result = gmail_send(
            to=to, subject=subject, body=body,
            cc=cc, bcc=bcc or '',
            reply_to_id=thread_id or '',
            in_reply_to=message_id,
            attachments=attachments or None,
            body_html=body_html,
        )
        if isinstance(result, dict) and 'error' in result:
            return f"Gmail error: {result['error']}"
        return f"Sent to {to}" + (f" (thread {str(thread_id)[:8]}...)" if thread_id else "")
    except Exception as e:
        return f"Error: {e}"

def email_search(query, count=10):
    """Search Gmail."""
    try:
        from hyperclaw.integrations_layer import gmail_search
        results = gmail_search(query=query, max_results=count)
        if not results:
            return "No results."
        lines = []
        for r in results:
            lines.append(f"[{r.get('id','')[:12]}] {r.get('from','?')} | {r.get('subject','(no subject)')} | {r.get('date','')}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"

def send_file(path, via="here", to="", caption=""):
    """Deliver any file via any channel (media_hub)."""
    try:
        from hyperclaw.media_hub import deliver_file
        return deliver_file(path, via=via, to=to, caption=caption)
    except Exception as e:
        return f"Error: {e}"

def open_file_local(path, app=""):
    """Open a file locally on the Mac."""
    try:
        from hyperclaw.media_hub import open_file
        return open_file(path, app=app)
    except Exception as e:
        return f"Error: {e}"

def email_forward(message_id, to, note="", include_attachments=True):
    """Forward an email with attachments."""
    try:
        from hyperclaw.integrations_layer import gmail_forward
        result = gmail_forward(message_id, to, note=note, include_attachments=include_attachments)
        if isinstance(result, dict) and result.get('status') == 'sent':
            return f"Forwarded to {to} ({result.get('attachments', 0)} attachments)"
        return f"Forward failed: {result}"
    except Exception as e:
        return f"Error: {e}"

def email_draft(to, subject, body, cc="", attachments=None, body_html=None, thread_id=""):
    """Create a Gmail draft for review."""
    try:
        from hyperclaw.integrations_layer import gmail_create_draft
        result = gmail_create_draft(to, subject, body, cc=cc, attachments=attachments,
                                    body_html=body_html, thread_id=thread_id)
        if isinstance(result, dict) and result.get('status') == 'draft_created':
            return f"Draft created for {to}: '{subject}' - in Drafts folder awaiting review"
        return f"Draft failed: {result}"
    except Exception as e:
        return f"Error: {e}"

def email_mark(message_id, mark_read=None, archive=None, star=None):
    """Mark read/unread, archive, star."""
    try:
        from hyperclaw.integrations_layer import gmail_modify
        result = gmail_modify(message_id, mark_read=mark_read, archive=archive, star=star)
        return str(result)
    except Exception as e:
        return f"Error: {e}"


def calendar_read(days=7):
    """Read upcoming calendar events using AppleScript."""
    try:
        script = f'''
        set now to current date
        set futureDate to now + ({days} * days)
        set output to ""
        tell application "Calendar"
            repeat with c in calendars
                set evts to (every event of c whose start date >= now and start date <= futureDate)
                repeat with e in evts
                    set output to output & (summary of e) & " | " & (start date of e) & return
                end repeat
            end repeat
        end tell
        return output
        '''
        return applescript(script) or "No events found"
    except Exception as e:
        return f"Error: {e}"

def calendar_create(title, start, end, location=""):
    """Create calendar event using AppleScript."""
    try:
        script = f'''
        tell application "Calendar"
            tell calendar "Calendar"
                set startDate to date "{start}"
                set endDate to date "{end}"
                make new event with properties {{summary:"{title}", start date:startDate, end date:endDate, location:"{location}"}}
            end tell
        end tell
        return "Event created"
        '''
        return applescript(script)
    except Exception as e:
        return f"Error: {e}"

# === AGI-LEVEL TOOL IMPLEMENTATIONS ===

def python_exec(code):
    """Execute Python code safely."""
    try:
        import io
        import contextlib
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(code, {"__builtins__": __builtins__})
        return output.getvalue() or "(no output)"
    except Exception as e:
        return f"Error: {e}"

def github(action, owner=None, repo=None, query=None, title=None):
    """GitHub API access."""
    try:
        token = env_var("GITHUB_TOKEN")
        headers = {"Accept": "application/vnd.github.v3+json"}
        if token:
            headers["Authorization"] = f"token {token}"

        base = "https://api.github.com"

        with httpx.Client(timeout=15) as client:
            if action == "repos" and owner:
                resp = client.get(f"{base}/users/{owner}/repos", headers=headers)
                repos = resp.json()[:10]
                return "\n".join([f"- {r['name']}: {r.get('description', 'No description')}" for r in repos])

            elif action == "issues" and owner and repo:
                resp = client.get(f"{base}/repos/{owner}/{repo}/issues", headers=headers)
                issues = resp.json()[:10]
                return "\n".join([f"#{i['number']}: {i['title']}" for i in issues])

            elif action == "prs" and owner and repo:
                resp = client.get(f"{base}/repos/{owner}/{repo}/pulls", headers=headers)
                prs = resp.json()[:10]
                return "\n".join([f"#{p['number']}: {p['title']}" for p in prs])

            elif action == "search_code" and query:
                resp = client.get(f"{base}/search/code", params={"q": query}, headers=headers)
                items = resp.json().get("items", [])[:10]
                return "\n".join([f"- {i['repository']['full_name']}: {i['path']}" for i in items])

            elif action == "create_issue" and owner and repo and title:
                resp = client.post(f"{base}/repos/{owner}/{repo}/issues",
                    headers=headers, json={"title": title, "body": query or ""})
                return f"Created issue #{resp.json().get('number')}"

            elif action == "user_info" and owner:
                resp = client.get(f"{base}/users/{owner}", headers=headers)
                u = resp.json()
                return f"Name: {u.get('name')}\nBio: {u.get('bio')}\nRepos: {u.get('public_repos')}"

            return "Unknown action"
    except Exception as e:
        return f"Error: {e}"

def wikipedia(query):
    """Wikipedia search and read."""
    try:
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{query.replace(' ', '_')}"
        with httpx.Client(timeout=10) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                return f"{data.get('title', '')}\n\n{data.get('extract', 'No content found')}"
            # Try search
            search_url = f"https://en.wikipedia.org/w/api.php?action=opensearch&search={query}&limit=5&format=json"
            resp = client.get(search_url)
            results = resp.json()
            if len(results) > 1 and results[1]:
                return "Search results:\n" + "\n".join([f"- {r}" for r in results[1]])
            return "No results found"
    except Exception as e:
        return f"Error: {e}"

def weather(location):
    """Get weather for a location."""
    try:
        # Using wttr.in (no API key needed)
        url = f"https://wttr.in/{location.replace(' ', '+')}?format=j1"
        with httpx.Client(timeout=10) as client:
            resp = client.get(url)
            data = resp.json()
            current = data["current_condition"][0]
            return f"""Location: {data['nearest_area'][0]['areaName'][0]['value']}
Temperature: {current['temp_F']}°F / {current['temp_C']}°C
Condition: {current['weatherDesc'][0]['value']}
Humidity: {current['humidity']}%
Wind: {current['windspeedMiles']} mph {current['winddir16Point']}"""
    except Exception as e:
        return f"Error: {e}"

def news(category=None, query=None):
    """Get news headlines."""
    try:
        # Using Google News RSS
        if query:
            url = f"https://news.google.com/rss/search?q={query.replace(' ', '+')}"
        else:
            cat_map = {"tech": "technology", "business": "business", "science": "science", "sports": "sports"}
            cat = cat_map.get(category, "")
            url = f"https://news.google.com/rss/topics/{cat}" if cat else "https://news.google.com/rss"

        with httpx.Client(timeout=10) as client:
            resp = client.get(url)
            import re
            items = re.findall(r'<title>(.+?)</title>', resp.text)[2:12]  # Skip RSS title
            return "\n".join([f"- {item}" for item in items]) or "No news found"
    except Exception as e:
        return f"Error: {e}"

def stock_price(symbol):
    """Get stock/crypto price."""
    try:
        # Try Yahoo Finance
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol.upper()}"
        with httpx.Client(timeout=10) as client:
            resp = client.get(url)
            data = resp.json()
            meta = data["chart"]["result"][0]["meta"]
            return f"""{meta.get('shortName', symbol.upper())}
Price: ${meta['regularMarketPrice']:.2f}
Previous Close: ${meta.get('previousClose', 'N/A')}
Market: {meta.get('exchangeName', 'Unknown')}"""
    except Exception as e:
        return f"Error: {e}"

def translate(text, to_lang, from_lang=None):
    """Translate text using LibreTranslate or MyMemory."""
    try:
        # Using MyMemory (free, no API key)
        langpair = f"{from_lang or 'en'}|{to_lang}"
        url = f"https://api.mymemory.translated.net/get?q={text[:500]}&langpair={langpair}"
        with httpx.Client(timeout=10) as client:
            resp = client.get(url)
            data = resp.json()
            translated = data.get("responseData", {}).get("translatedText", "")
            return translated or "Translation failed"
    except Exception as e:
        return f"Error: {e}"

def youtube_info(url, get_transcript=False):
    """Get YouTube video info."""
    try:
        import re
        video_id = re.search(r'(?:v=|/)([a-zA-Z0-9_-]{11})', url)
        if not video_id:
            return "Invalid YouTube URL"
        vid = video_id.group(1)

        # Get video info via oembed
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid}&format=json"
        with httpx.Client(timeout=10) as client:
            resp = client.get(oembed_url)
            data = resp.json()
            result = f"Title: {data.get('title')}\nAuthor: {data.get('author_name')}"

            if get_transcript:
                result += "\n\n(Transcript fetching requires youtube-transcript-api package)"

            return result
    except Exception as e:
        return f"Error: {e}"

def reddit(subreddit, sort="hot", limit=10):
    """Get Reddit posts."""
    try:
        url = f"https://www.reddit.com/r/{subreddit}/{sort}.json?limit={limit}"
        with httpx.Client(timeout=10, headers={"User-Agent": "HyperClaw/1.0"}) as client:
            resp = client.get(url)
            data = resp.json()
            posts = data["data"]["children"]
            return "\n\n".join([
                f"• {p['data']['title']}\n  Score: {p['data']['score']} | Comments: {p['data']['num_comments']}"
                for p in posts
            ])
    except Exception as e:
        return f"Error: {e}"

def hackernews(type_="top", limit=10):
    """Get Hacker News stories."""
    try:
        type_map = {"top": "topstories", "new": "newstories", "best": "beststories", "ask": "askstories", "show": "showstories"}
        endpoint = type_map.get(type_, "topstories")
        with httpx.Client(timeout=15) as client:
            resp = client.get(f"https://hacker-news.firebaseio.com/v0/{endpoint}.json")
            ids = resp.json()[:limit]
            stories = []
            for id in ids:
                story = client.get(f"https://hacker-news.firebaseio.com/v0/item/{id}.json").json()
                stories.append(f"• {story.get('title', 'No title')}\n  Score: {story.get('score', 0)} | {story.get('url', 'No URL')[:60]}")
            return "\n\n".join(stories)
    except Exception as e:
        return f"Error: {e}"

def arxiv_search(query, max_results=5):
    """Search arXiv papers."""
    try:
        url = f"http://export.arxiv.org/api/query?search_query=all:{query}&max_results={max_results}"
        with httpx.Client(timeout=15) as client:
            resp = client.get(url)
            import re
            titles = re.findall(r'<title>(.+?)</title>', resp.text)[1:]  # Skip feed title
            summaries = re.findall(r'<summary>(.+?)</summary>', resp.text, re.DOTALL)
            results = []
            for i, title in enumerate(titles):
                summary = summaries[i][:200] + "..." if i < len(summaries) else ""
                results.append(f"• {title.strip()}\n  {summary.strip()}")
            return "\n\n".join(results) or "No papers found"
    except Exception as e:
        return f"Error: {e}"

def math_eval(expression):
    """Evaluate math expression."""
    try:
        import math
        # Safe eval with math functions
        allowed = {k: v for k, v in math.__dict__.items() if not k.startswith('_')}
        allowed.update({"abs": abs, "round": round, "min": min, "max": max, "sum": sum})
        result = eval(expression, {"__builtins__": {}}, allowed)
        return str(result)
    except Exception as e:
        return f"Error: {e}"

def time_info(timezone=None):
    """Get current time info."""
    try:
        from datetime import datetime
        import time as time_module
        if timezone:
            # Use web API for timezone
            url = f"http://worldtimeapi.org/api/timezone/{timezone}"
            with httpx.Client(timeout=5) as client:
                resp = client.get(url)
                data = resp.json()
                return f"Time in {timezone}: {data['datetime'][:19]}\nDay: {data.get('day_of_week')}"
        else:
            now = datetime.now()
            return f"Local time: {now.strftime('%Y-%m-%d %H:%M:%S')}\nTimezone: {time_module.tzname[0]}\nUTC offset: {time_module.timezone // 3600} hours"
    except Exception as e:
        return f"Error: {e}"

def dns_lookup(domain, record_type="A"):
    """DNS lookup."""
    try:
        result = subprocess.run(["dig", "+short", domain, record_type], capture_output=True, text=True, timeout=10)
        return result.stdout.strip() or f"No {record_type} records found"
    except Exception as e:
        return f"Error: {e}"

def whois_lookup(domain):
    """WHOIS lookup."""
    try:
        result = subprocess.run(["whois", domain], capture_output=True, text=True, timeout=15)
        # Extract key info
        lines = result.stdout.split('\n')
        important = [l for l in lines if any(k in l.lower() for k in ['registrar', 'creation', 'expir', 'name server', 'status'])]
        return '\n'.join(important[:15]) or result.stdout[:1000]
    except Exception as e:
        return f"Error: {e}"

def hash_text(text, algorithm="sha256"):
    """Generate hash."""
    try:
        import hashlib
        algo = getattr(hashlib, algorithm.lower(), None)
        if not algo:
            return f"Unknown algorithm: {algorithm}"
        return algo(text.encode()).hexdigest()
    except Exception as e:
        return f"Error: {e}"

def uuid_gen(version=4):
    """Generate UUID."""
    try:
        import uuid
        if version == 1:
            return str(uuid.uuid1())
        return str(uuid.uuid4())
    except Exception as e:
        return f"Error: {e}"

def base64_op(text, decode=False):
    """Base64 encode/decode."""
    try:
        if decode:
            return base64.b64decode(text).decode('utf-8')
        return base64.b64encode(text.encode()).decode('utf-8')
    except Exception as e:
        return f"Error: {e}"

def json_format_op(json_str, query=None):
    """Format or query JSON."""
    try:
        data = json.loads(json_str)
        if query:
            # Simple path query
            parts = query.replace('$', '').strip('.').split('.')
            result = data
            for part in parts:
                if '[' in part:
                    key, idx = part.split('[')
                    idx = int(idx.rstrip(']'))
                    result = result[key][idx] if key else result[idx]
                else:
                    result = result[part]
            return json.dumps(result, indent=2)
        return json.dumps(data, indent=2)
    except Exception as e:
        return f"Error: {e}"

def regex_test_op(pattern, text, find_all=False):
    """Test regex pattern."""
    try:
        import re
        if find_all:
            matches = re.findall(pattern, text)
            return f"Found {len(matches)} matches:\n" + "\n".join([f"- {m}" for m in matches])
        match = re.search(pattern, text)
        if match:
            return f"Match found: {match.group()}\nSpan: {match.span()}"
        return "No match"
    except Exception as e:
        return f"Error: {e}"

def pdf_read_op(path, pages="all"):
    """Read PDF text."""
    try:
        # Try using pdftotext (poppler)
        p = Path(path).expanduser()
        if not p.exists():
            return f"File not found: {path}"
        if pages != "all":
            result = subprocess.run(["pdftotext", "-f", pages.split("-")[0], "-l", pages.split("-")[-1], str(p), "-"],
                capture_output=True, text=True, timeout=30)
        else:
            result = subprocess.run(["pdftotext", str(p), "-"], capture_output=True, text=True, timeout=30)
        return result.stdout[:10000] or "No text extracted"
    except FileNotFoundError:
        return "pdftotext not installed. Install with: brew install poppler"
    except Exception as e:
        return f"Error: {e}"

def image_generate_op(prompt, size="1024x1024", save_path=None):
    """Generate image with DALL-E."""
    try:
        api_key = env_var("OPENAI_API_KEY")
        if not api_key:
            return "Set OPENAI_API_KEY environment variable"

        with httpx.Client(timeout=60) as client:
            resp = client.post("https://api.openai.com/v1/images/generations",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"prompt": prompt, "size": size, "n": 1})
            data = resp.json()
            url = data["data"][0]["url"]

            if save_path:
                img_resp = client.get(url)
                Path(save_path).expanduser().write_bytes(img_resp.content)
                return f"Image saved to {save_path}"
            return f"Image URL: {url}"
    except Exception as e:
        return f"Error: {e}"

def geocode_op(address=None, lat=None, lon=None):
    """Geocoding."""
    try:
        with httpx.Client(timeout=10) as client:
            if address:
                url = f"https://nominatim.openstreetmap.org/search?q={address}&format=json&limit=1"
                resp = client.get(url, headers={"User-Agent": "HyperClaw/1.0"})
                data = resp.json()
                if data:
                    return f"Location: {data[0]['display_name']}\nLat: {data[0]['lat']}\nLon: {data[0]['lon']}"
                return "Location not found"
            elif lat and lon:
                url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json"
                resp = client.get(url, headers={"User-Agent": "HyperClaw/1.0"})
                data = resp.json()
                return data.get("display_name", "Location not found")
    except Exception as e:
        return f"Error: {e}"

def currency_convert(amount, from_currency, to_currency):
    """Currency conversion."""
    try:
        url = f"https://api.exchangerate-api.com/v4/latest/{from_currency.upper()}"
        with httpx.Client(timeout=10) as client:
            resp = client.get(url)
            data = resp.json()
            rate = data["rates"].get(to_currency.upper())
            if rate:
                result = amount * rate
                return f"{amount} {from_currency.upper()} = {result:.2f} {to_currency.upper()}\nRate: {rate}"
            return f"Unknown currency: {to_currency}"
    except Exception as e:
        return f"Error: {e}"

def unit_convert_op(value, from_unit, to_unit):
    """Unit conversion."""
    try:
        # Common conversions
        conversions = {
            ("km", "mi"): 0.621371, ("mi", "km"): 1.60934,
            ("kg", "lb"): 2.20462, ("lb", "kg"): 0.453592,
            ("c", "f"): lambda x: x * 9/5 + 32, ("f", "c"): lambda x: (x - 32) * 5/9,
            ("m", "ft"): 3.28084, ("ft", "m"): 0.3048,
            ("l", "gal"): 0.264172, ("gal", "l"): 3.78541,
            ("cm", "in"): 0.393701, ("in", "cm"): 2.54,
        }
        key = (from_unit.lower(), to_unit.lower())
        if key in conversions:
            conv = conversions[key]
            if callable(conv):
                result = conv(value)
            else:
                result = value * conv
            return f"{value} {from_unit} = {result:.4f} {to_unit}"
        return f"Unknown conversion: {from_unit} to {to_unit}"
    except Exception as e:
        return f"Error: {e}"

def qr_code_gen(data, save_path=None):
    """Generate QR code."""
    try:
        # Use qrencode CLI or API
        if save_path:
            result = subprocess.run(["qrencode", "-o", save_path, data], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                return f"QR code saved to {save_path}"
        # Fallback: use web service
        url = f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={data}"
        return f"QR code URL: {url}"
    except FileNotFoundError:
        return f"QR code URL: https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={data}"
    except Exception as e:
        return f"Error: {e}"

def wayback_lookup(url):
    """Wayback Machine lookup."""
    try:
        api_url = f"https://archive.org/wayback/available?url={url}"
        with httpx.Client(timeout=10) as client:
            resp = client.get(api_url)
            data = resp.json()
            snapshots = data.get("archived_snapshots", {})
            if snapshots.get("closest"):
                snap = snapshots["closest"]
                return f"Archived: {snap['timestamp']}\nURL: {snap['url']}"
            return "No archived version found"
    except Exception as e:
        return f"Error: {e}"

def slack_send(message, channel=None):
    """Send Slack message via webhook."""
    try:
        webhook_url = env_var("SLACK_WEBHOOK_URL")
        if not webhook_url:
            return "Set SLACK_WEBHOOK_URL environment variable"
        payload = {"text": message}
        if channel:
            payload["channel"] = channel
        with httpx.Client(timeout=10) as client:
            resp = client.post(webhook_url, json=payload)
            return "Message sent" if resp.status_code == 200 else f"Error: {resp.text}"
    except Exception as e:
        return f"Error: {e}"

def discord_send(message):
    """Send Discord message via webhook."""
    try:
        webhook_url = env_var("DISCORD_WEBHOOK_URL")
        if not webhook_url:
            return "Set DISCORD_WEBHOOK_URL environment variable"
        with httpx.Client(timeout=10) as client:
            resp = client.post(webhook_url, json={"content": message})
            return "Message sent" if resp.status_code == 204 else f"Error: {resp.text}"
    except Exception as e:
        return f"Error: {e}"

def sql_query_op(database, query):
    """Execute SQLite query."""
    try:
        import sqlite3
        p = Path(database).expanduser()
        conn = sqlite3.connect(str(p))
        cursor = conn.cursor()
        cursor.execute(query)
        if query.strip().upper().startswith("SELECT"):
            rows = cursor.fetchall()
            cols = [d[0] for d in cursor.description] if cursor.description else []
            conn.close()
            if rows:
                header = " | ".join(cols)
                data = "\n".join([" | ".join(str(c) for c in row) for row in rows[:50]])
                return f"{header}\n{'-'*len(header)}\n{data}"
            return "No results"
        conn.commit()
        conn.close()
        return f"Query executed. Rows affected: {cursor.rowcount}"
    except Exception as e:
        return f"Error: {e}"

def http_request_op(url, method="GET", headers=None, body=None, json_body=None):
    """Custom HTTP request."""
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            kwargs = {"headers": headers or {}}
            if json_body:
                kwargs["json"] = json_body
            elif body:
                kwargs["content"] = body
            resp = client.request(method.upper(), url, **kwargs)
            return f"Status: {resp.status_code}\nHeaders: {dict(resp.headers)}\n\nBody:\n{resp.text[:5000]}"
    except Exception as e:
        return f"Error: {e}"

def execute_tool(name, input_data):
    """Execute a tool and return result."""
    if name == "bash":
        cmd = input_data["command"]
        print_tool("bash", cmd)
        result = run_bash(cmd)
        print_output(result)
        return result
    elif name == "read_file":
        path = input_data["path"]
        print_tool("read_file", path)
        result = read_file(path)
        print_output(result[:500] + "..." if len(result) > 500 else result)
        return result
    elif name == "write_file":
        path = input_data["path"]
        content = input_data["content"]
        print_code(content, path)
        result = write_file(path, content)
        print(f"{GREEN}  ✓ {result}{RESET}")
        return result
    elif name == "edit_file":
        print_tool("edit_file", f"{input_data['path']}")
        print(f"{RED}  - {input_data['old_text'][:80]}...{RESET}")
        print(f"{GREEN}  + {input_data['new_text'][:80]}...{RESET}")
        result = edit_file(input_data["path"], input_data["old_text"], input_data["new_text"])
        print(f"{YELLOW}  ✓ {result}{RESET}")
        return result
    elif name == "web_fetch":
        print_tool("web_fetch", input_data["url"])
        result = web_fetch(input_data["url"], input_data.get("method", "GET"), input_data.get("headers"), input_data.get("body"))
        print_output(result[:500])
        return result
    elif name == "web_search":
        print_tool("web_search", input_data["query"])
        result = web_search(input_data["query"])
        print_output(result, BLUE)
        return result
    elif name == "screenshot":
        print_tool("screenshot", input_data.get("region", "full"))
        result = screenshot(input_data.get("region", "full"))
        print(f"{GREEN}  {result}{RESET}")
        return result
    elif name == "open_url":
        print_tool("open_url", input_data["url"])
        result = open_url(input_data["url"])
        print(f"{GREEN}  ✓ {result}{RESET}")
        return result
    elif name == "open_app":
        print_tool("open_app", input_data["app_name"])
        result = open_app(input_data["app_name"])
        print(f"{GREEN}  ✓ {result}{RESET}")
        return result
    elif name == "applescript":
        print_tool("applescript", input_data["script"][:100])
        result = applescript(input_data["script"])
        print_output(result)
        return result
    elif name == "type_text":
        print_tool("type_text", f'"{input_data["text"][:50]}..."')
        result = type_text(input_data["text"])
        print(f"{GREEN}  ✓ Typed{RESET}")
        return result
    elif name == "key_press":
        print_tool("key_press", input_data["keys"])
        result = key_press(input_data["keys"])
        print(f"{GREEN}  ✓ Pressed{RESET}")
        return result
    elif name == "mouse_click":
        print_tool("mouse_click", f"({input_data['x']}, {input_data['y']})")
        result = mouse_click(input_data["x"], input_data["y"], input_data.get("button", "left"))
        print(f"{GREEN}  ✓ {result}{RESET}")
        return result
    elif name == "notification":
        print_tool("notification", input_data["title"])
        result = notification(input_data["title"], input_data["message"])
        print(f"{GREEN}  ✓ Sent{RESET}")
        return result
    elif name == "clipboard_read":
        print_tool("clipboard_read", "")
        result = clipboard_read()
        print_output(result[:200])
        return result
    elif name == "clipboard_write":
        print_tool("clipboard_write", input_data["text"][:50])
        result = clipboard_write(input_data["text"])
        print(f"{GREEN}  ✓ {result}{RESET}")
        return result
    elif name == "download":
        print_tool("download", f"{input_data['url']} -> {input_data['path']}")
        result = download(input_data["url"], input_data["path"])
        print(f"{GREEN}  ✓ {result}{RESET}")
        return result
    elif name == "telegram":
        print_tool("telegram", input_data["message"][:50])
        result = telegram(input_data["message"], input_data.get("file"))
        print(f"{GREEN}  ✓ {result}{RESET}")
        return result
    elif name == "imessage":
        print_tool("imessage", input_data["message"][:50])
        result = imessage_send(input_data["message"], input_data.get("recipient"), input_data.get("file"))
        print(f"{GREEN}  ✓ {result}{RESET}")
        return result
    elif name == "send_file":
        print_tool("send_file", f"{input_data['path']} via {input_data.get('via', 'here')}")
        result = send_file(input_data["path"], input_data.get("via", "here"),
                           input_data.get("to", ""), input_data.get("caption", ""))
        print(f"{GREEN}  ✓ {result}{RESET}")
        return result
    elif name == "open_file":
        print_tool("open_file", input_data["path"])
        result = open_file_local(input_data["path"], input_data.get("app", ""))
        print(f"{GREEN}  ✓ {result}{RESET}")
        return result
    elif name == "email_search":
        print_tool("email_search", f"query: {input_data['query']}")
        result = email_search(input_data["query"], input_data.get("count", 10))
        print_output(result)
        return result
    elif name == "email_forward":
        print_tool("email_forward", f"{input_data['message_id'][:12]}... -> {input_data['to']}")
        result = email_forward(input_data["message_id"], input_data["to"],
                               input_data.get("note", ""), input_data.get("include_attachments", True))
        print(f"{GREEN}  ✓ {result}{RESET}")
        return result
    elif name == "email_draft":
        print_tool("email_draft", f"to: {input_data['to']} | {input_data['subject'][:40]}")
        result = email_draft(input_data["to"], input_data["subject"], input_data["body"],
                             input_data.get("cc", ""), input_data.get("attachments"),
                             input_data.get("body_html"), input_data.get("thread_id", ""))
        print(f"{GREEN}  ✓ {result}{RESET}")
        return result
    elif name == "email_mark":
        print_tool("email_mark", input_data["message_id"][:12])
        result = email_mark(input_data["message_id"], input_data.get("mark_read"),
                            input_data.get("archive"), input_data.get("star"))
        print(f"{GREEN}  ✓ {result}{RESET}")
        return result
    elif name == "imessage_read":
        print_tool("imessage_read", input_data.get("recipient", "all"))
        result = imessage_read(input_data.get("recipient"), input_data.get("count", 10))
        print_output(result)
        return result
    elif name == "speak":
        print_tool("speak", input_data["text"][:50])
        result = speak(input_data["text"])
        print(f"{GREEN}  ✓ {result}{RESET}")
        return result
    elif name == "vision":
        print_tool("vision", input_data["image_path"])
        result = vision(input_data["image_path"], input_data.get("question", "Describe what you see."))
        print_output(result)
        return result
    elif name == "email_read":
        print_tool("email_read", f"{input_data.get('count', 5)} emails")
        result = email_read(input_data.get("count", 5))
        print_output(result)
        return result
    elif name == "email_send":
        print_tool("email_send", f"To: {input_data['to']}")
        result = email_send(input_data["to"], input_data["subject"], input_data["body"],
                            input_data.get("thread_id"), input_data.get("message_id"),
                            input_data.get("cc", ""), input_data.get("bcc", ""),
                            input_data.get("attachments"), input_data.get("body_html"))
        print(f"{GREEN}  ✓ {result}{RESET}")
        return result
    elif name == "calendar_read":
        print_tool("calendar_read", f"Next {input_data.get('days', 7)} days")
        result = calendar_read(input_data.get("days", 7))
        print_output(result)
        return result
    elif name == "calendar_create":
        print_tool("calendar_create", input_data["title"])
        result = calendar_create(input_data["title"], input_data["start"], input_data["end"], input_data.get("location", ""))
        print(f"{GREEN}  ✓ {result}{RESET}")
        return result
    # === AGI-LEVEL TOOL HANDLERS ===
    elif name == "python_exec":
        print_tool("python_exec", input_data["code"][:80])
        result = python_exec(input_data["code"])
        print_output(result)
        return result
    elif name == "github":
        print_tool("github", f"{input_data['action']} {input_data.get('owner', '')}")
        result = github(input_data["action"], input_data.get("owner"), input_data.get("repo"), input_data.get("query"), input_data.get("title"))
        print_output(result, BLUE)
        return result
    elif name == "wikipedia":
        print_tool("wikipedia", input_data["query"])
        result = wikipedia(input_data["query"])
        print_output(result)
        return result
    elif name == "weather":
        print_tool("weather", input_data["location"])
        result = weather(input_data["location"])
        print_output(result, CYAN)
        return result
    elif name == "news":
        print_tool("news", input_data.get("category", "top"))
        result = news(input_data.get("category"), input_data.get("query"))
        print_output(result, BLUE)
        return result
    elif name == "stock_price":
        print_tool("stock_price", input_data["symbol"])
        result = stock_price(input_data["symbol"])
        print_output(result, GREEN)
        return result
    elif name == "translate":
        print_tool("translate", f"{input_data['text'][:30]} -> {input_data['to_lang']}")
        result = translate(input_data["text"], input_data["to_lang"], input_data.get("from_lang"))
        print_output(result)
        return result
    elif name == "youtube":
        print_tool("youtube", input_data["url"])
        result = youtube_info(input_data["url"], input_data.get("get_transcript", False))
        print_output(result)
        return result
    elif name == "reddit":
        print_tool("reddit", f"r/{input_data['subreddit']}")
        result = reddit(input_data["subreddit"], input_data.get("sort", "hot"), input_data.get("limit", 10))
        print_output(result)
        return result
    elif name == "hackernews":
        print_tool("hackernews", input_data.get("type", "top"))
        result = hackernews(input_data.get("type", "top"), input_data.get("limit", 10))
        print_output(result)
        return result
    elif name == "arxiv":
        print_tool("arxiv", input_data["query"])
        result = arxiv_search(input_data["query"], input_data.get("max_results", 5))
        print_output(result)
        return result
    elif name == "math":
        print_tool("math", input_data["expression"])
        result = math_eval(input_data["expression"])
        print(f"{GREEN}  = {result}{RESET}")
        return result
    elif name == "time_info":
        print_tool("time_info", input_data.get("timezone", "local"))
        result = time_info(input_data.get("timezone"))
        print_output(result, CYAN)
        return result
    elif name == "dns_lookup":
        print_tool("dns_lookup", f"{input_data['domain']} {input_data.get('record_type', 'A')}")
        result = dns_lookup(input_data["domain"], input_data.get("record_type", "A"))
        print_output(result)
        return result
    elif name == "whois":
        print_tool("whois", input_data["domain"])
        result = whois_lookup(input_data["domain"])
        print_output(result)
        return result
    elif name == "hash":
        print_tool("hash", f"{input_data.get('algorithm', 'sha256')}({input_data['text'][:20]}...)")
        result = hash_text(input_data["text"], input_data.get("algorithm", "sha256"))
        print(f"{GREEN}  {result}{RESET}")
        return result
    elif name == "uuid_generate":
        print_tool("uuid_generate", f"v{input_data.get('version', 4)}")
        result = uuid_gen(input_data.get("version", 4))
        print(f"{GREEN}  {result}{RESET}")
        return result
    elif name == "base64_encode":
        print_tool("base64", "decode" if input_data.get("decode") else "encode")
        result = base64_op(input_data["text"], input_data.get("decode", False))
        print_output(result)
        return result
    elif name == "json_format":
        print_tool("json_format", input_data.get("query", "format"))
        result = json_format_op(input_data["json_str"], input_data.get("query"))
        print_output(result)
        return result
    elif name == "regex_test":
        print_tool("regex_test", input_data["pattern"])
        result = regex_test_op(input_data["pattern"], input_data["text"], input_data.get("find_all", False))
        print_output(result)
        return result
    elif name == "pdf_read":
        print_tool("pdf_read", input_data["path"])
        result = pdf_read_op(input_data["path"], input_data.get("pages", "all"))
        print_output(result)
        return result
    elif name == "image_generate":
        print_tool("image_generate", input_data["prompt"][:50])
        result = image_generate_op(input_data["prompt"], input_data.get("size", "1024x1024"), input_data.get("save_path"))
        print_output(result, GREEN)
        return result
    elif name == "geocode":
        print_tool("geocode", input_data.get("address", f"{input_data.get('lat')},{input_data.get('lon')}"))
        result = geocode_op(input_data.get("address"), input_data.get("lat"), input_data.get("lon"))
        print_output(result)
        return result
    elif name == "currency":
        print_tool("currency", f"{input_data['amount']} {input_data['from_currency']} -> {input_data['to_currency']}")
        result = currency_convert(input_data["amount"], input_data["from_currency"], input_data["to_currency"])
        print(f"{GREEN}  {result}{RESET}")
        return result
    elif name == "unit_convert":
        print_tool("unit_convert", f"{input_data['value']} {input_data['from_unit']} -> {input_data['to_unit']}")
        result = unit_convert_op(input_data["value"], input_data["from_unit"], input_data["to_unit"])
        print(f"{GREEN}  {result}{RESET}")
        return result
    elif name == "qr_code":
        print_tool("qr_code", input_data["data"][:30])
        result = qr_code_gen(input_data["data"], input_data.get("save_path"))
        print_output(result, GREEN)
        return result
    elif name == "wayback":
        print_tool("wayback", input_data["url"])
        result = wayback_lookup(input_data["url"])
        print_output(result)
        return result
    elif name == "slack":
        print_tool("slack", input_data["message"][:50])
        result = slack_send(input_data["message"], input_data.get("channel"))
        print(f"{GREEN}  ✓ {result}{RESET}")
        return result
    elif name == "discord":
        print_tool("discord", input_data["message"][:50])
        result = discord_send(input_data["message"])
        print(f"{GREEN}  ✓ {result}{RESET}")
        return result
    elif name == "sql_query":
        print_tool("sql_query", f"{input_data['database']}: {input_data['query'][:50]}")
        result = sql_query_op(input_data["database"], input_data["query"])
        print_output(result)
        return result
    elif name == "http_request":
        print_tool("http_request", f"{input_data.get('method', 'GET')} {input_data['url']}")
        result = http_request_op(input_data["url"], input_data.get("method", "GET"), input_data.get("headers"), input_data.get("body"), input_data.get("json_body"))
        print_output(result)
        return result
    # === VECTOR MEMORY HANDLERS ===
    elif name == "memory_store":
        if not VECTOR_MEMORY_AVAILABLE:
            return "Vector memory not available"
        print_tool("memory_store", input_data["content"][:60])
        result = vm_store(input_data["content"], source=input_data.get("source", "conversation"), domain=input_data.get("domain"))
        print(f"{GREEN}  ✓ {result}{RESET}")
        return result
    elif name == "memory_search":
        if not VECTOR_MEMORY_AVAILABLE:
            return "Vector memory not available"
        print_tool("memory_search", input_data["query"])
        results = vm_search(input_data["query"], limit=input_data.get("limit", 5), domain=input_data.get("domain"))
        if results:
            output = "\n".join([f"[{r['similarity']}] {r['content'][:100]}..." for r in results])
            print_output(output, BLUE)
            return json.dumps(results, indent=2)
        print(f"{YELLOW}  No memories found{RESET}")
        return "No memories found"
    elif name == "memory_forget":
        if not VECTOR_MEMORY_AVAILABLE:
            return "Vector memory not available"
        print_tool("memory_forget", input_data.get("memory_id") or input_data.get("query", "")[:40])
        result = vm_forget(memory_id=input_data.get("memory_id"), query=input_data.get("query"))
        print(f"{YELLOW}  {result}{RESET}")
        return result
    elif name == "memory_list":
        if not VECTOR_MEMORY_AVAILABLE:
            return "Vector memory not available"
        print_tool("memory_list", f"limit={input_data.get('limit', 20)}")
        memories = vm_list(limit=input_data.get("limit", 20), domain=input_data.get("domain"))
        if memories:
            output = "\n".join([f"{m['id']}: {m['content']}" for m in memories])
            print_output(output)
            return json.dumps(memories, indent=2)
        print(f"{YELLOW}  No memories stored{RESET}")
        return "No memories stored"
    elif name == "memory_stats":
        if not VECTOR_MEMORY_AVAILABLE:
            return "Vector memory not available"
        print_tool("memory_stats", "")
        stats = vm_stats()
        print_output(json.dumps(stats, indent=2), CYAN)
        return json.dumps(stats, indent=2)
    # === BACKGROUND AGENT HANDLERS ===
    elif name == "agent_start":
        if not AGENTS_AVAILABLE:
            return "Agents not available"
        print_tool("agent_start", input_data.get("name", "all"))
        result = agent_start(input_data.get("name"))
        print(f"{GREEN}  ✓ {result}{RESET}")
        return result
    elif name == "agent_stop":
        if not AGENTS_AVAILABLE:
            return "Agents not available"
        print_tool("agent_stop", input_data.get("name", "all"))
        result = agent_stop(input_data.get("name"))
        print(f"{YELLOW}  {result}{RESET}")
        return result
    elif name == "agent_status":
        if not AGENTS_AVAILABLE:
            return "Agents not available"
        print_tool("agent_status", "")
        status = agent_status()
        print_output(json.dumps(status, indent=2), CYAN)
        return json.dumps(status, indent=2)
    elif name == "swarm_roster":
        print_tool("swarm_roster", "")
        roster = _swarm_roster()
        print_output(json.dumps({"count": roster.get("count"), "domains": roster.get("domains")}, indent=2), CYAN)
        return json.dumps(roster, indent=2)
    elif name == "agent_watch":
        if not AGENTS_AVAILABLE:
            return "Agents not available"
        print_tool("agent_watch", f"{input_data['agent_type']}: {input_data['target']}")
        result = agent_add_watch(input_data["agent_type"], input_data["target"])
        print(f"{GREEN}  ✓ {result}{RESET}")
        return result
    # === PLANNING HANDLERS ===
    elif name == "plan_create":
        if not AGENTS_AVAILABLE:
            return "Agents not available"
        print_tool("plan_create", input_data["goal"][:50])
        result = plan_create(input_data["goal"], input_data.get("tasks"))
        print_output(f"Created plan {result['id']} with {len(result['tasks'])} tasks", GREEN)
        return json.dumps(result, indent=2)
    elif name == "plan_status":
        if not AGENTS_AVAILABLE:
            return "Agents not available"
        print_tool("plan_status", input_data["plan_id"])
        result = plan_status(input_data["plan_id"])
        progress = result.get("progress", {})
        print_output(f"Progress: {progress.get('completed', 0)}/{progress.get('total', 0)} ({progress.get('percent', 0)}%)", CYAN)
        return json.dumps(result, indent=2)
    elif name == "plan_next":
        if not AGENTS_AVAILABLE:
            return "Agents not available"
        print_tool("plan_next", input_data["plan_id"])
        tasks = plan_next(input_data["plan_id"])
        if tasks:
            for t in tasks:
                print(f"{CYAN}  → {t['id']}: {t['title']}{RESET}")
        else:
            print(f"{YELLOW}  No tasks ready{RESET}")
        return json.dumps(tasks, indent=2)
    elif name == "plan_update":
        if not AGENTS_AVAILABLE:
            return "Agents not available"
        print_tool("plan_update", f"{input_data['task_id']} → {input_data['status']}")
        result = plan_update(input_data["plan_id"], input_data["task_id"], input_data["status"], input_data.get("result"))
        print(f"{GREEN}  ✓ {result}{RESET}")
        return result
    elif name == "plan_list":
        if not AGENTS_AVAILABLE:
            return "Agents not available"
        print_tool("plan_list", "")
        plans = plan_list()
        for p in plans:
            print(f"{CYAN}  {p['id']}: {p['goal']} [{p['status']}]{RESET}")
        return json.dumps(plans, indent=2)
    # === TRANSCRIPTION HANDLERS ===
    elif name == "transcribe_audio":
        if not AGENTS_AVAILABLE:
            return "Agents not available"
        print_tool("transcribe_audio", input_data["path"])
        result = audio_transcribe(input_data["path"], input_data.get("language"))
        if "text" in result:
            print_output(result["text"][:300] + "..." if len(result.get("text", "")) > 300 else result.get("text", ""))
        else:
            print(f"{RED}  {result.get('error', 'Unknown error')}{RESET}")
        return json.dumps(result, indent=2)
    elif name == "transcribe_video":
        if not AGENTS_AVAILABLE:
            return "Agents not available"
        print_tool("transcribe_video", input_data["path"])
        result = video_transcribe(input_data["path"], input_data.get("language"))
        if "text" in result:
            print_output(result["text"][:300] + "...")
        else:
            print(f"{RED}  {result.get('error', 'Unknown error')}{RESET}")
        return json.dumps(result, indent=2)
    elif name == "record_audio":
        if not AGENTS_AVAILABLE:
            return "Agents not available"
        duration = input_data.get("duration", 30)
        print_tool("record_audio", f"{duration}s")
        print(f"{YELLOW}  Recording for {duration} seconds...{RESET}")
        result = audio_record(duration)
        print(f"{GREEN}  ✓ Recorded: {result.get('recorded', 'unknown')}{RESET}")
        return json.dumps(result, indent=2)
    elif name == "transcripts_list":
        if not AGENTS_AVAILABLE:
            return "Agents not available"
        print_tool("transcripts_list", "")
        transcripts = transcripts_list()
        for t in transcripts[:10]:
            print(f"{DIM}  {t['name']}{RESET}")
        return json.dumps(transcripts, indent=2)
    # === LEARNING HANDLERS ===
    elif name == "learning_log":
        if not AGENTS_AVAILABLE:
            return "Agents not available"
        print_tool("learning_log", f"{input_data['action']}: {'✓' if input_data['success'] else '✗'}")
        result = learning_log(input_data["action"], input_data["detail"], input_data["success"], input_data.get("feedback"))
        print(f"{GREEN if input_data['success'] else YELLOW}  {result}{RESET}")
        return result
    elif name == "learning_stats":
        if not AGENTS_AVAILABLE:
            return "Agents not available"
        print_tool("learning_stats", f"last {input_data.get('days', 7)} days")
        stats = learning_stats(input_data.get("days", 7))
        print(f"{CYAN}  Success rate: {stats['success_rate']}% ({stats['successes']}/{stats['total_interactions']}){RESET}")
        return json.dumps(stats, indent=2)
    elif name == "learning_reflect":
        if not AGENTS_AVAILABLE:
            return "Agents not available"
        print_tool("learning_reflect", input_data.get("period", "week"))
        reflection = learning_reflect(input_data.get("period", "week"))
        if reflection.get("insights"):
            print(f"{CYAN}  Insights:{RESET}")
            for i in reflection["insights"]:
                print(f"{YELLOW}    • {i}{RESET}")
        return json.dumps(reflection, indent=2)
    elif name == "learning_advice":
        if not AGENTS_AVAILABLE:
            return "Agents not available"
        print_tool("learning_advice", input_data["action_type"])
        advice = learning_advice(input_data["action_type"])
        print_output(advice, CYAN)
        return advice
    elif name == "learning_feedback":
        if not AGENTS_AVAILABLE:
            return "Agents not available"
        print_tool("learning_feedback", input_data["feedback"][:40])
        result = learning_feedback(input_data["feedback"])
        print(f"{GREEN}  ✓ {result}{RESET}")
        return result
    return f"Unknown tool: {name}"


def clean_history(history):
    """
    Repair history so it satisfies the Messages API tool contract:
    - a tool_result is only valid if the IMMEDIATELY preceding assistant
      message contains the matching tool_use;
    - an assistant tool_use is only valid if the NEXT message answers it;
    - history must start with a user message and contain no empty messages.

    The passes are iterated to a fixpoint: dropping a leading assistant
    message (or merging neighbors) can orphan a tool_result that was valid
    a moment earlier, so one sweep is not enough — repair until stable.
    """
    def _sweep(msgs):
        # Pass 1: drop tool_results not answered by the directly preceding
        # assistant message.
        cleaned = []
        for msg in msgs:
            role = msg.get("role")
            content = msg.get("content")
            if role == "user" and isinstance(content, list):
                prev = cleaned[-1] if cleaned else None
                valid_ids = set()
                if prev and prev.get("role") == "assistant" and isinstance(prev.get("content"), list):
                    valid_ids = {b.get("id") for b in prev["content"]
                                 if isinstance(b, dict) and b.get("type") == "tool_use"}
                kept = [b for b in content
                        if not (isinstance(b, dict) and b.get("type") == "tool_result"
                                and b.get("tool_use_id") not in valid_ids)]
                if kept:
                    cleaned.append({"role": "user", "content": kept})
            elif content:
                cleaned.append(dict(msg))

        # Pass 2: strip assistant tool_use blocks the next message never answers.
        for i, msg in enumerate(cleaned):
            if msg.get("role") == "assistant" and isinstance(msg.get("content"), list):
                nxt = cleaned[i + 1] if i + 1 < len(cleaned) else None
                answered = set()
                if nxt and nxt.get("role") == "user" and isinstance(nxt.get("content"), list):
                    answered = {b.get("tool_use_id") for b in nxt["content"]
                                if isinstance(b, dict) and b.get("type") == "tool_result"}
                msg["content"] = [b for b in msg["content"]
                                  if not (isinstance(b, dict) and b.get("type") == "tool_use"
                                          and b.get("id") not in answered)]

        # Pass 3: drop empties, merge consecutive same-role, start with user.
        merged = []
        for msg in cleaned:
            if not msg.get("content"):
                continue
            if merged and merged[-1]["role"] == msg["role"]:
                a, b = merged[-1]["content"], msg["content"]
                if isinstance(a, str) and isinstance(b, str):
                    merged[-1]["content"] = a + "\n" + b
                else:
                    a = a if isinstance(a, list) else [{"type": "text", "text": a}]
                    b = b if isinstance(b, list) else [{"type": "text", "text": b}]
                    merged[-1]["content"] = a + b
            else:
                merged.append(msg)
        while merged and merged[0]["role"] != "user":
            merged.pop(0)
        return merged

    if not history:
        return []
    current = list(history)
    for _ in range(5):  # fixpoint in practice within 2-3 sweeps
        swept = _sweep(current)
        if swept == current:
            break
        current = swept
    return current


def chat(message):
    """Send message with streaming output."""
    global HISTORY

    client = anthropic.Anthropic(api_key=API_KEY)
    HISTORY.append({"role": "user", "content": message})

    print(f"\n{DIM}Processing...{RESET}")

    _api_retries = 0
    while True:
        try:
            # Use streaming for real-time output
            assistant_content = []
            tool_results = []
            current_text = ""
            current_thinking = ""
            in_thinking = False
            in_text = False
            current_tool = None
            printed_name = False

            with client.messages.stream(
                model=MODEL,
                max_tokens=16000,
                thinking={
                    "type": "enabled",
                    "budget_tokens": 10000
                },
                system=SYSTEM,
                tools=TOOLS,
                messages=HISTORY
            ) as stream:
                for event in stream:
                    # Handle different event types
                    if event.type == "content_block_start":
                        if hasattr(event.content_block, 'type'):
                            if event.content_block.type == "thinking":
                                in_thinking = True
                                current_thinking = ""
                                print(f"\n{DIM}┌─ Thinking ─────────────────────────────────────────{RESET}")
                            elif event.content_block.type == "text":
                                in_text = True
                                current_text = ""
                                if not printed_name:
                                    print(f"\n{MAGENTA}{BOLD}{AI_NAME}:{RESET} ", end="", flush=True)
                                    printed_name = True
                            elif event.content_block.type == "tool_use":
                                current_tool = {
                                    "id": event.content_block.id,
                                    "name": event.content_block.name,
                                    "input": {}
                                }

                    elif event.type == "content_block_delta":
                        if hasattr(event.delta, 'thinking'):
                            # Stream thinking
                            chunk = event.delta.thinking
                            current_thinking += chunk
                            # Print each line as it comes
                            for char in chunk:
                                if char == '\n':
                                    print(f"{RESET}")
                                    print(f"{DIM}│ ", end="", flush=True)
                                else:
                                    print(f"{DIM}{char}", end="", flush=True)

                        elif hasattr(event.delta, 'text'):
                            # Stream text response
                            chunk = event.delta.text
                            current_text += chunk
                            print(chunk, end="", flush=True)

                        elif hasattr(event.delta, 'partial_json'):
                            # Tool input being built
                            pass

                    elif event.type == "content_block_stop":
                        if in_thinking:
                            in_thinking = False
                            print(f"{RESET}")
                            print(f"{DIM}└────────────────────────────────────────────────────{RESET}")
                        elif in_text:
                            in_text = False
                            print()  # Newline after text
                            if current_text:
                                assistant_content.append({"type": "text", "text": current_text})

                    elif event.type == "message_delta":
                        pass  # End of message

                # Get final message for tool use
                final = stream.get_final_message()

                for block in final.content:
                    if block.type == "tool_use":
                        # Execute tool
                        result = execute_tool(block.name, block.input)
                        assistant_content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input
                        })
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(result)[:10000]
                        })

        except anthropic.BadRequestError as e:
            error_msg = str(e)
            print(f"{RED}API Error: {error_msg[:200]}{RESET}")

            # Clean orphaned tool results first
            HISTORY = clean_history(HISTORY)

            # If still have history, try trimming
            if len(HISTORY) > 4:
                print(f"{YELLOW}Cleaning and trimming history...{RESET}")
                HISTORY = HISTORY[-4:]
                HISTORY = clean_history(HISTORY)
                if HISTORY:
                    continue

            # Last resort: reset, but carry over recent plain-text turns
            # so the model keeps conversational context ("fix that" must
            # still mean something after a reset).
            print(f"{RED}Resetting conversation (keeping recent context){RESET}")
            recent_text = [m for m in HISTORY
                           if isinstance(m.get("content"), str) and m["content"].strip()][-6:]
            while recent_text and recent_text[0]["role"] != "user":
                recent_text.pop(0)
            HISTORY = recent_text
            if not HISTORY or HISTORY[-1].get("content") != message:
                HISTORY.append({"role": "user", "content": message})
            continue

        except anthropic.APIError as e:
            print(f"{RED}API Error: {str(e)[:200]}{RESET}")
            status = getattr(e, "status_code", None)
            # 4xx errors (bad model, bad auth, bad request) won't fix
            # themselves — retrying just hammers the API. Fail fast.
            if status is not None and 400 <= status < 500 and status != 429:
                if status == 404:
                    print(f"{YELLOW}Model '{MODEL}' not found — it may be retired. "
                          f"Set HYPERCLAW_MODEL to a current model (e.g. claude-sonnet-4-6).{RESET}")
                break
            print(f"{YELLOW}Retrying...{RESET}")
            time.sleep(min(2 ** _api_retries, 30))
            _api_retries += 1
            if _api_retries > 5:
                print(f"{RED}Giving up after {_api_retries} retries.{RESET}")
                break
            continue

        except Exception as e:
            print(f"{RED}Unexpected error: {str(e)[:200]}{RESET}")
            # Don't reset, just continue
            continue

        # Add assistant message
        if assistant_content:
            HISTORY.append({"role": "assistant", "content": assistant_content})

        # If tools were used, add results and continue
        if tool_results:
            HISTORY.append({"role": "user", "content": tool_results})
        else:
            break

        if hasattr(final, 'stop_reason') and final.stop_reason == "end_turn":
            break

    # Trim history and clean orphaned tool results
    if len(HISTORY) > 40:
        HISTORY = HISTORY[-40:]
        HISTORY = clean_history(HISTORY)

def main():
    global HISTORY, SYSTEM

    if not API_KEY:
        print(f"\n{RED}ANTHROPIC_API_KEY not set.{RESET}")
        print(f"\nRun {CYAN}hyperclaw init{RESET} to set up your API key.")
        print(f"Or set it manually: export ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    # Load memories and build system prompt
    print(f"\n{DIM}Loading memories...{RESET}")
    memories = load_memory_files()
    SYSTEM = SYSTEM_BASE + "\n\n--- MEMORY ---\n" + memories if memories else SYSTEM_BASE

    # Load previous session and clean any orphaned tool results
    HISTORY = load_session()
    HISTORY = clean_history(HISTORY)
    session_msg = f"Resumed session ({len(HISTORY)} messages)" if HISTORY else "New session"

    print(f"\n{CYAN}{BOLD}=== HyperClaw ==={RESET}")
    print(f"{DIM}Full computer control: terminal, files, web, GUI, screen{RESET}")
    print(f"{DIM}{session_msg} | /reset to clear, /quit to exit{RESET}\n")

    while True:
        try:
            user_input = input(f"{CYAN}>{RESET} ").strip()

            if not user_input:
                continue

            if user_input.startswith("/"):
                cmd = user_input.lower()
                if cmd == "/reset":
                    HISTORY = []
                    save_session(HISTORY)
                    print(f"{GREEN}Reset.{RESET}\n")
                elif cmd in ("/quit", "/exit", "/q"):
                    save_session(HISTORY)
                    print(f"{DIM}Goodbye.{RESET}\n")
                    break
                else:
                    print(f"{YELLOW}Unknown: {cmd}{RESET}\n")
                continue

            chat(user_input)
            save_session(HISTORY)  # Save after each exchange
            print()

        except KeyboardInterrupt:
            print("\n")
        except EOFError:
            print(f"\n{DIM}Goodbye.{RESET}")
            break

if __name__ == "__main__":
    main()
