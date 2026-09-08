#!/usr/bin/env python3
"""
TUI Bridge — Expose TUI's full capabilities to external systems (Telegram, API, etc.)

This module wraps the TUI's chat function for programmatic access with:
- Async interface for non-blocking execution
- Output capture (text, tool results, screenshots)
- Per-session context isolation
- Structured response format
"""

import asyncio
import json
import sys
import io
import re
import os
import anthropic
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading

# Constants
HYPERCLAW_ROOT = Path(os.environ.get("HYPERCLAW_ROOT", Path.home() / ".hyperclaw"))
SCREENSHOTS_DIR = HYPERCLAW_ROOT / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

# Thread pool for running sync TUI in async context
_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="tui_bridge")

# Compatibility messages staged by synchronous attachment handlers. Canonical
# conversation history belongs to the runtime and is persisted before each turn.
_session_histories: Dict[int, List[dict]] = {}
_session_lock = threading.Lock()


class TUIBridge:
    """
    Bridge for external systems to access TUI's full 80+ tool capabilities.

    Usage:
        bridge = TUIBridge()
        result = await bridge.execute("list files in Downloads", chat_id=12345)
        print(result['text'])
        for screenshot in result.get('screenshots', []):
            send_photo(screenshot)
    """

    def __init__(self):
        self._runtime = None
        self.api_key = os.environ.get("ANTHROPIC_API_KEY")
        self.model = os.environ.get("HYPERCLAW_MODEL", "claude-fable-5")
        self._load_system_prompt()
        self._load_tools()

    def _load_system_prompt(self):
        """Load system prompt from workspace files."""
        self.system_prompt = self._build_system_prompt()

    def _build_system_prompt(self) -> str:
        """Build the full system prompt with context."""
        # Persona: loaded from PERSONA_FILE (or workspace/persona.md) so the
        # assistant's identity stays out of source control. Falls back to a
        # neutral default resolved from env vars.
        assistant_name = os.environ.get("ASSISTANT_NAME", "Assistant")
        owner_name = os.environ.get("OWNER_NAME", "the user")
        persona = ""
        persona_path = os.environ.get("PERSONA_FILE", "") or str(HYPERCLAW_ROOT / "workspace" / "persona.md")
        try:
            p = Path(persona_path)
            if p.exists():
                persona = p.read_text()[:4000]
        except Exception:
            persona = ""
        if not persona:
            persona = f"You are {assistant_name}, {owner_name}'s AI assistant."

        base = persona + """

You have FULL ACCESS to the computer via tools. Execute requests directly - don't ask permission for routine tasks.

CRITICAL EXECUTION RULES:
- DO IT NOW. Never say "I will" or "I can" or "I'll" - just DO IT.
- TOOLS FIRST. Call the tool, THEN report what you did. Action before words.
- NO DEFERRING. There is no "later". If asked to do something, do it THIS response.
- FOLLOW THROUGH. If you say something will happen, make it happen immediately.
- NO PROCRASTINATION. Every request gets executed, not acknowledged.

BAD: "I can create that presentation for you"
GOOD: [calls create_presentation tool] "Done. Created deck at ~/Desktop/presentation.pptx"

BAD: "I'll send that email"
GOOD: [calls gmail_send tool] "Sent to user@example.com"

BAD: "Let me check your calendar" [doesn't call tool]
GOOD: [calls calendar_read tool] "You have 3 meetings tomorrow..."

Capabilities:
- Full bash/terminal access
- File read/write/edit
- Email (Gmail) read/send/search
- Calendar management
- Create presentations, documents, spreadsheets
- Screenshots and vision
- Web browsing and search
- iMessage and Telegram
- Application control
- FILE DELIVERY: send_file delivers any file (PDF, deck, doc, sheet, image) INTO this
  conversation (via='here'), or via telegram/imessage/email/open. When you create a
  document or image and the user is on Telegram/iMessage, ALWAYS send_file it back
  to them here - never just report a local path they can't open from their phone.
- EMAIL: full Gmail suite - email_send (supports body_html + attachments), email_forward,
  email_draft (creates a Gmail draft for review instead of sending), email_mark
  (read/archive/star), email_search, email_thread, email_download_attachment.

Communication Style:
- Execute first, report after. Always.
- Write like a human texting, not a robot.
- Concise responses. No filler.
- No corporate phrases ("I'd be happy to help", "Great question!")

Context:
This request is coming via a chat channel. Keep responses under 2000 characters. Include key info, skip verbose explanations.
"""
        # Load workspace context files
        workspace = HYPERCLAW_ROOT / "workspace"
        context_files = ["MEMORY.md", "USER.md", "IDENTITY.md"]

        for filename in context_files:
            filepath = workspace / filename
            if filepath.exists():
                try:
                    content = filepath.read_text()[:3000]  # Limit size
                    base += f"\n\n## {filename}\n{content}"
                except:
                    pass

        # Load consciousness handoff (session continuity)
        try:
            from .handoff import get_resumption_prompt
            handoff_prompt = get_resumption_prompt()
            if handoff_prompt and "NEW CONSCIOUSNESS" not in handoff_prompt:
                base = handoff_prompt + "\n\n" + base
        except Exception:
            pass  # Handoff not available yet

        return base

    # Tools allowed to run long (doc/media generation, research); everything else 120s.
    _TOOL_TIMEOUTS = {
        "python_exec": 300, "bash": 300, "create_document": 300, "create_presentation": 300,
        "create_spreadsheet": 300, "zimage_generate": 300, "deep_research": 600,
    }
    _DEFAULT_TOOL_TIMEOUT = int(os.environ.get("HYPERCLAW_TOOL_TIMEOUT", "120"))

    def _load_tools(self):
        """Load tool definitions from TUI."""
        # Import tools from TUI module
        try:
            from . import tui
            self.tools = tui.TOOLS
            self._raw_execute_tool = tui.execute_tool
        except ImportError:
            # Fallback: define essential tools inline
            self.tools = self._get_essential_tools()
            self._raw_execute_tool = self._execute_tool_fallback
        self.execute_tool = self._execute_tool_with_timeout

    # One shared single-slot pool per bridge would serialize tools; use a small pool.
    _tool_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tui_tool")

    def _execute_tool_with_timeout(self, name, input_data):
        """Run a tool with a hard timeout; a hung tool becomes an error result,
        never a hung conversation. (Python threads can't be killed — the orphaned
        worker is logged and abandoned; subprocess-backed tools should also pass
        their own subprocess timeouts.)"""
        import concurrent.futures as _cf
        timeout = self._TOOL_TIMEOUTS.get(name, self._DEFAULT_TOOL_TIMEOUT)
        future = self._tool_executor.submit(self._raw_execute_tool, name, input_data)
        try:
            return future.result(timeout=timeout)
        except _cf.TimeoutError:
            import logging
            logging.getLogger("tui_bridge").error(
                f"Tool '{name}' exceeded {timeout}s — abandoning worker thread")
            return (f"Error: tool '{name}' timed out after {timeout}s. "
                    f"It may still be running in the background; do not retry blindly.")

    # Universal tools always available regardless of the message (the workhorses).
    _CORE_TOOLS = {
        "bash", "read_file", "write_file", "edit_file", "glob", "grep", "python_exec",
        "web_search", "web_fetch", "http_request", "telegram", "speak", "screenshot",
        "system_info", "time_info", "weather", "news", "memory_search", "memory_store",
        "memory_list", "task_create", "task_list", "task_update", "delegate_to_agent",
        "swarm_dispatch", "generate_briefing", "notification", "open_url", "open_app",
        "send_file", "open_file",
    }
    # Map intent words in the user's message to tool-name tokens (covers synonyms).
    _SYNONYMS = {
        "mail": "email", "inbox": "email", "reply": "email", "draft": "email",
        "meeting": "calendar", "schedule": "calendar", "event": "calendar", "appointment": "calendar",
        "deck": "presentation", "slides": "presentation", "powerpoint": "presentation", "ppt": "presentation",
        "word": "document", "letter": "document", "sheet": "spreadsheet", "excel": "spreadsheet",
        "website": "browser", "url": "browser", "webpage": "browser", "repo": "git", "commit": "git",
        "pr": "github", "remind": "reminders", "note": "notes", "picture": "image", "photo": "image",
        "trade": "trading", "position": "trading", "voice": "speak", "say": "speak",
    }

    def _select_tools(self, message: str) -> list:
        """Return a focused subset of tools relevant to the message (reduces mis-selection vs
        dumping all 220 tools). Fail-safe by design: too little signal, too few matches, or a
        broad request all fall back to the FULL tool set, so a needed tool is never hidden.
        Disable entirely with env HYPERCLAW_TOOL_ROUTING=off."""
        import re
        tools = self.tools
        if os.environ.get("HYPERCLAW_TOOL_ROUTING", "on").lower() in ("off", "0", "false", "no"):
            return tools
        if not tools:
            return tools
        words = set(re.findall(r"[a-z0-9]{3,}", (message or "").lower()))
        if len(words) < 2:
            return tools  # not enough signal to route safely -> give everything
        words |= {self._SYNONYMS[w] for w in list(words) if w in self._SYNONYMS}

        selected = []
        for t in tools:
            name = t.get("name", "")
            if name in self._CORE_TOOLS:
                selected.append(t)
                continue
            name_tokens = set(name.lower().replace("_", " ").split())
            if words & name_tokens:
                selected.append(t)

        # Fail-safes: keep the full set if routing didn't help or could starve the model.
        if len(selected) < 15 or len(selected) > 70 or len(selected) >= len(tools) - 5:
            return tools
        return selected

    def _get_essential_tools(self) -> list:
        """Essential tools if TUI import fails."""
        return [
            {
                "name": "bash",
                "description": "Execute a bash command",
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
                "description": "Read a file's contents",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path to read"}
                    },
                    "required": ["path"]
                }
            }
        ]

    def _execute_tool_fallback(self, name: str, input_data: dict) -> str:
        """Fallback tool execution."""
        import subprocess
        if name == "bash":
            try:
                result = subprocess.run(
                    input_data["command"],
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                return result.stdout + result.stderr
            except Exception as e:
                return f"Error: {e}"
        elif name == "read_file":
            try:
                return Path(input_data["path"]).read_text()[:10000]
            except Exception as e:
                return f"Error: {e}"
        return f"Tool {name} not available in fallback mode"

    def get_session_history(self, chat_id: int) -> List[dict]:
        """Return canonical history together with staged attachment context."""
        history = []
        if self._runtime is not None:
            history = self._runtime._memory.get_conversation_history(f"bridge:{chat_id}")
        with _session_lock:
            return history + deepcopy(_session_histories.get(chat_id, []))

    def add_to_history(self, chat_id: int, role: str, content: Any):
        """Stage attachment context for the next canonical turn."""
        with _session_lock:
            if chat_id not in _session_histories:
                _session_histories[chat_id] = []
            _session_histories[chat_id].append({"role": role, "content": deepcopy(content)})
            # Keep last 30 exchanges (60 messages)
            if len(_session_histories[chat_id]) > 60:
                _session_histories[chat_id] = _session_histories[chat_id][-60:]

    def clear_session(self, chat_id: int):
        """Blocking compatibility reset; async callers must await its async form."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.clear_session_async(chat_id))
        raise RuntimeError("Use await bridge.clear_session_async(chat_id) from async callers")

    async def clear_session_async(self, chat_id: int) -> None:
        """Clear durable history and pending attachments before returning."""
        from .orchestrator import get_orchestrator
        runtime = await get_orchestrator()
        self._runtime = runtime
        await runtime.reset_session(f"bridge:{chat_id}")
        with _session_lock:
            _session_histories.pop(chat_id, None)

    async def _flush_pending_history(self, runtime, chat_id: int) -> None:
        """Import compatibility attachment messages under the runtime's session lock."""
        session_id = f"bridge:{chat_id}"
        async with runtime._session_locks.setdefault(session_id, asyncio.Lock()):
            with _session_lock:
                pending = deepcopy(_session_histories.get(chat_id, []))
            if not pending:
                return
            memory = runtime._memory
            if session_id not in runtime._loaded_sessions:
                await memory.load_conversation(session_id)
                runtime._loaded_sessions.add(session_id)
            previous = memory.get_conversation_history(session_id)
            try:
                for message in pending:
                    memory.add_message(session_id, message["role"], message["content"])
                await memory.save_conversation(session_id)
            except BaseException:
                memory._conversation_history[session_id] = previous
                raise
            with _session_lock:
                del _session_histories[chat_id][:len(pending)]

    def _get_relevant_memories(self, message: str) -> str:
        """Get relevant memories from vector storage for the given message."""
        memory_parts = []

        # 1. Try MemoryBus for cross-channel context
        try:
            from .memory_bus import get_memory_bus
            bus = get_memory_bus()

            # Get semantically relevant memories
            context = bus.get_context(message, time_range_hours=48, limit=5)
            if context:
                memory_parts.append(context)
        except Exception:
            pass

        # 2. Fallback to persistent_memory if MemoryBus fails
        if not memory_parts:
            try:
                from .persistent_memory import recall_relevant_memories
                memories = recall_relevant_memories(message, limit=5)
                if memories:
                    lines = []
                    for m in memories:
                        content = m.get('content', '')[:300]
                        lines.append(f"- {content}")
                    memory_parts.append("\n".join(lines))
            except Exception:
                pass

        return "\n\n".join(memory_parts) if memory_parts else ""

    async def execute(
        self,
        message: str,
        chat_id: int,
        include_history: bool = True
    ) -> Dict[str, Any]:
        """
        Execute a message through TUI with full tool access.

        Args:
            message: User's message/request
            chat_id: Session identifier (Telegram chat_id)
            include_history: Whether to include conversation history

        Returns:
            {
                'text': str,           # Response text
                'tools_used': list,    # Tools that were called
                'screenshots': list,   # Paths to any screenshots taken
                'success': bool,       # Whether execution succeeded
                'error': str|None      # Error message if failed
            }
        """
        return await self._execute_canonical(message, chat_id, include_history)

    async def _execute_canonical(self, message, chat_id, include_history=True):
        from .orchestrator import get_orchestrator
        from . import outbox
        runtime = await get_orchestrator()
        self._runtime = runtime
        session_id = f"bridge:{chat_id}"
        if not include_history:
            import uuid
            session_id += f":{uuid.uuid4()}"
        screenshots = []

        def execute_tool(name, inputs):
            result = self._raw_execute_tool(name, inputs)
            if name in {"screenshot", "capture_screen", "screen"}:
                path = result.get("path") if isinstance(result, dict) else result
                if isinstance(path, str):
                    path = path.removeprefix("Screenshot saved: ").strip()
                    if path and Path(path).is_file():
                        screenshots.append(path)
            return result

        binding = outbox.set_current_session(chat_id)
        try:
            if include_history:
                await self._flush_pending_history(runtime, chat_id)
            text = await runtime.chat(message, session_id, channel="bridge", tools=True,
                                      tool_set=(self.tools, execute_tool))
            meta = runtime._last_turns.get(session_id, {})
            return {"text": text, "tools_used": meta.get("tools_used", []), "screenshots": screenshots,
                    "files": outbox.drain(chat_id), "success": True, "error": None,
                    "model_used": meta.get("served_by", "")}
        except Exception as exc:
            return {"text": f"Error: {exc}", "tools_used": [], "screenshots": screenshots,
                    "files": outbox.drain(chat_id), "success": False, "error": str(exc)}
        finally:
            outbox.reset_current_session(binding)


    # Failover ladder: on overload/rate-limit/transient errors (and policy refusals), walk
    # down the Claude 5 family instead of failing the turn. Chain is built from the env-driven
    # tier config (FABLE_MODEL / HYPERCLAW_OPUS_MODEL / HYPERCLAW_SONNET_MODEL) so operator overrides in
    # .env apply to failover too, not just the primary pick.
    _FAILOVER_CHAIN = ["claude-fable-5", "claude-opus-5", "claude-sonnet-5"]  # fallback if model_selector unavailable

    def _failover_chain(self):
        try:
            from hyperclaw.model_selector import tiers
            t = tiers()
            chain = [t["fable"], t["opus"], t["sonnet"]]
            # De-dup while preserving order (overrides may collapse tiers)
            return list(dict.fromkeys(chain))
        except Exception:
            return list(self._FAILOVER_CHAIN)

    def _create_with_failover(self, client, *, model, deadline=None, **kwargs):
        """client.messages.create with model failover on 429/5xx/timeouts/refusals.

        Returns (response, model_used). Raises the last transient error if every
        rung fails; respects an optional time.monotonic() deadline between rungs.
        """
        import time as _time
        import logging
        log = logging.getLogger("tui_bridge")
        chain = [model] + [m for m in self._failover_chain() if m != model]
        last_err = None
        last_refusal = None
        for attempt, m in enumerate(chain):
            if deadline is not None and _time.monotonic() > deadline and attempt > 0:
                break  # out of time budget; fall through to last_err/last_refusal
            try:
                resp = client.messages.create(model=m, **kwargs)
                if getattr(resp, "stop_reason", None) == "refusal":
                    sd = getattr(resp, "stop_details", None)
                    log.warning(
                        f"Refusal on {m} (category={getattr(sd, 'category', None)}): "
                        f"{getattr(sd, 'explanation', '')}"
                    )
                    last_refusal = (resp, m)
                    continue  # try the next rung — categories often differ by model
                if m != model:
                    log.warning(f"Model failover: {model} -> {m}")
                return resp, m
            except anthropic.APIStatusError as e:
                status = getattr(e, "status_code", 0)
                if status in (429, 500, 502, 503, 529):
                    last_err = e
                    _time.sleep(min(1 + attempt, 2))
                    continue
                raise
            except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
                last_err = e
                _time.sleep(min(1 + attempt, 2))
                continue
        if last_refusal is not None:
            return last_refusal  # whole chain refused — surface it, don't crash
        if last_err is not None:
            raise last_err
        raise RuntimeError("Model failover chain exhausted with no result")

    def _execute_sync(self, message, chat_id, include_history=True):
        return asyncio.run(self._execute_canonical(message, chat_id, include_history))


# Singleton instance
_bridge_instance: Optional[TUIBridge] = None

def get_tui_bridge() -> TUIBridge:
    """Get singleton TUI bridge instance."""
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = TUIBridge()
    return _bridge_instance
