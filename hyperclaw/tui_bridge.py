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
import threading

# Constants
HYPERCLAW_ROOT = Path(os.environ.get("HYPERCLAW_ROOT", Path.home() / ".hyperclaw"))
SCREENSHOTS_DIR = HYPERCLAW_ROOT / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

# Thread pool for running sync TUI in async context
_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="tui_bridge")

# Per-session state (chat_id -> history)
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
        """Get conversation history for a session."""
        with _session_lock:
            if chat_id not in _session_histories:
                _session_histories[chat_id] = []
            return _session_histories[chat_id]

    def add_to_history(self, chat_id: int, role: str, content: Any):
        """Add message to session history."""
        with _session_lock:
            if chat_id not in _session_histories:
                _session_histories[chat_id] = []
            _session_histories[chat_id].append({"role": role, "content": content})
            # Keep last 30 exchanges (60 messages)
            if len(_session_histories[chat_id]) > 60:
                _session_histories[chat_id] = _session_histories[chat_id][-60:]

    def clear_session(self, chat_id: int):
        """Clear session history."""
        with _session_lock:
            _session_histories[chat_id] = []

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
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            _executor,
            self._execute_sync,
            message,
            chat_id,
            include_history
        )


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

    def _execute_sync(
        self,
        message: str,
        chat_id: int,
        include_history: bool
    ) -> Dict[str, Any]:
        """Synchronous execution (runs in thread pool)."""

        result = {
            'text': '',
            'tools_used': [],
            'screenshots': [],
            'files': [],
            'success': True,
            'error': None
        }

        # Bind this worker thread to the conversation so send_file(via='here')
        # queues files for THIS chat; drained into result['files'] at turn end.
        try:
            from . import outbox as _outbox
        except Exception:
            _outbox = None
        if _outbox is not None:
            try:
                _outbox.set_current_session(chat_id)
            except Exception:
                # Clear any stale binding from a previous turn on this pooled thread so
                # files can't be queued into the WRONG chat; drain below still works.
                try:
                    _outbox.set_current_session(None)
                except Exception:
                    pass

        try:
            # timeout/max_retries: SDK defaults are 600s x 3 attempts, which multiplied by
            # the 3-rung failover ladder and the 12-iteration loop is a multi-hour hang.
            client = anthropic.Anthropic(api_key=self.api_key, timeout=120.0, max_retries=1)
            import time as _walltime
            _turn_deadline = _walltime.monotonic() + 600  # 10 min wall-clock per message

            # Get relevant memories for this message
            memory_context = self._get_relevant_memories(message)

            # Build system prompt with memory context
            system_with_memory = self.system_prompt
            if memory_context:
                system_with_memory += f"\n\nRelevant memories:\n{memory_context}"

            # Build messages with history
            messages = []
            if include_history:
                history = self.get_session_history(chat_id)
                messages.extend(history[-20:])  # Last 10 exchanges

            messages.append({"role": "user", "content": message})

            # Bounded agentic loop. Interactive chat turns should resolve quickly; genuinely large
            # jobs get delegated/queued, not spun inline. The guards below stop runaway loops
            # (the previous version had max_iterations=100 and NO loop/error detection).
            max_iterations = 12
            iteration = 0
            response_text = ""
            # Route to a focused tool subset for this message (fail-safe to all tools).
            selected_tools = self._select_tools(message)
            # Model routing (Fable backend): pick the right model for THIS request.
            try:
                from hyperclaw.model_selector import pick_model
                turn_model = pick_model(message, self.model)
            except Exception:
                turn_model = self.model
            import hashlib as _hashlib, json as _json
            _tool_sig_counts = {}      # (tool, input) signature -> times called this turn
            _consecutive_errors = 0
            _MAX_REPEAT = 3
            _loop_break = False

            while iteration < max_iterations:
                iteration += 1
                if _walltime.monotonic() > _turn_deadline:
                    if not response_text.strip():
                        response_text = ("This is taking longer than my time budget for one message. "
                                         "Tell me which part to prioritize and I'll continue.")
                    break

                # Call Claude (with model failover on overload/transient errors/refusals)
                response, _model_used = self._create_with_failover(
                    client,
                    model=turn_model,
                    deadline=_turn_deadline,
                    max_tokens=8000,
                    system=system_with_memory,
                    tools=selected_tools,
                    messages=messages
                )
                result['model_used'] = _model_used
                if _model_used != turn_model:
                    result['failover_from'] = turn_model
                if getattr(response, "stop_reason", None) == "refusal":
                    sd = getattr(response, "stop_details", None)
                    response_text = response_text or (
                        "I can't help with that request"
                        + (f" ({sd.category})" if sd is not None and getattr(sd, 'category', None) else "")
                        + "."
                    )
                    break

                # Process response
                assistant_content = []
                tool_results = []
                has_text_this_turn = False

                for block in response.content:
                    if block.type == "text":
                        text = block.text
                        if text.strip():
                            has_text_this_turn = True
                        response_text += text
                        assistant_content.append({
                            "type": "text",
                            "text": text
                        })

                    elif block.type == "tool_use":
                        tool_name = block.name
                        tool_input = block.input

                        # Track tool usage
                        result['tools_used'].append({
                            'name': tool_name,
                            'input': tool_input
                        })

                        # Loop guard: refuse to run the SAME tool with identical input repeatedly
                        # (the classic runaway shape). Legit multi-step work varies tool/input, so
                        # this only trips on stuck repetition - not on normal long tool sequences.
                        try:
                            _sig = _hashlib.sha1(
                                (tool_name + _json.dumps(tool_input, sort_keys=True, default=str)).encode()
                            ).hexdigest()
                        except Exception:
                            _sig = tool_name
                        _tool_sig_counts[_sig] = _tool_sig_counts.get(_sig, 0) + 1
                        if _tool_sig_counts[_sig] > _MAX_REPEAT:
                            tool_result = (f"[loop-guard] '{tool_name}' was already called with identical "
                                           f"input {_MAX_REPEAT} times. Refusing to repeat - change "
                                           f"approach or give your final answer.")
                            _loop_break = True
                        else:
                            try:
                                tool_result = self.execute_tool(tool_name, tool_input)
                            except Exception as e:
                                tool_result = f"Tool error: {e}"
                        # Consecutive-error backstop
                        if str(tool_result).startswith(("Error", "Tool error", "[loop-guard]")):
                            _consecutive_errors += 1
                        else:
                            _consecutive_errors = 0

                        # Check for screenshots
                        if tool_name in ['screenshot', 'capture_screen', 'screen']:
                            if isinstance(tool_result, str) and Path(tool_result).exists():
                                result['screenshots'].append(tool_result)
                            elif isinstance(tool_result, dict) and tool_result.get('path'):
                                result['screenshots'].append(tool_result['path'])

                        assistant_content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": tool_name,
                            "input": tool_input
                        })

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(tool_result)[:8000]
                        })

                # Add to messages
                messages.append({"role": "assistant", "content": assistant_content})

                if tool_results:
                    messages.append({"role": "user", "content": tool_results})
                    # Stop if a loop or repeated tool errors were detected this turn.
                    if _loop_break or _consecutive_errors >= 5:
                        if not response_text.strip():
                            response_text = ("I stopped because I was repeating the same step without "
                                             "making progress. Tell me how you'd like to proceed.")
                        break
                    # Otherwise keep working through the tool sequence.
                else:
                    # No tools this turn. (assistant turn already appended above - do NOT re-append.)
                    if response.stop_reason == "max_tokens":
                        messages.append({"role": "user", "content": "Continue from where you left off."})
                        continue
                    # Finished naturally.
                    break

            # If the step budget was exhausted without an answer, say so (don't return blank/hang).
            if iteration >= max_iterations and not response_text.strip():
                response_text = ("I hit my step limit for this turn without finishing. The task may "
                                 "need to be broken down - tell me which part to tackle first.")

            # Update session history
            self.add_to_history(chat_id, "user", message)
            self.add_to_history(chat_id, "assistant", response_text or assistant_content)

            # Record in cross-channel memory (non-blocking)
            try:
                from .memory_bus import get_memory_bus
                bus = get_memory_bus()
                bus.record_exchange(
                    channel="tui_bridge",
                    user_message=message,
                    assistant_message=response_text or str(assistant_content)[:2000],
                    metadata={"chat_id": chat_id},
                    extract_knowledge=True
                )
            except Exception:
                pass  # Don't fail on memory errors

            result['text'] = response_text

        except Exception as e:
            result['success'] = False
            result['error'] = str(e)
            result['text'] = f"Error executing request: {e}"
            # Crash guard: a failed message must never take the process down and must
            # leave forensics. Full traceback to a dedicated, private crash log.
            try:
                import traceback
                crash_log = HYPERCLAW_ROOT / "logs" / "crashes.log"
                crash_log.parent.mkdir(parents=True, exist_ok=True)
                with open(crash_log, "a") as fh:
                    fh.write(f"\n=== {datetime.now().isoformat()} chat={chat_id} ===\n")
                    fh.write(f"message: {str(message)[:500]}\n")
                    traceback.print_exc(file=fh)
                os.chmod(crash_log, 0o600)
            except Exception:
                pass

        # Deliver any files tools queued for this conversation (send_file via='here',
        # doc engines, charts). Channel adapters send these natively after the text.
        try:
            if _outbox is not None:
                result['files'] = _outbox.drain(chat_id)
        except Exception:
            pass

        return result

    async def execute_gmail(self, action: str, **kwargs) -> Dict[str, Any]:
        """
        Execute Gmail-specific operations.

        Actions:
            - inbox: List inbox emails
            - read: Read specific email
            - send: Send email
            - search: Search emails
            - thread: Get email thread
        """
        try:
            from .integrations_layer import (
                gmail_list_inbox, gmail_get_message, gmail_send,
                gmail_search, gmail_get_thread, gmail_read_message_text
            )

            if action == "inbox":
                max_results = kwargs.get('max_results', 10)
                emails = await asyncio.to_thread(gmail_list_inbox, max_results)
                return {'success': True, 'data': emails}

            elif action == "read":
                message_id = kwargs.get('message_id')
                if not message_id:
                    return {'success': False, 'error': 'message_id required'}
                text = await asyncio.to_thread(gmail_read_message_text, message_id)
                return {'success': True, 'data': text}

            elif action == "send":
                to = kwargs.get('to')
                subject = kwargs.get('subject')
                body = kwargs.get('body')
                cc = kwargs.get('cc', '')
                reply_to = kwargs.get('reply_to_id', '')

                if not all([to, subject, body]):
                    return {'success': False, 'error': 'to, subject, and body required'}

                result = await asyncio.to_thread(gmail_send, to, subject, body, cc, reply_to)
                return {'success': True, 'data': result}

            elif action == "search":
                query = kwargs.get('query', '')
                max_results = kwargs.get('max_results', 10)
                results = await asyncio.to_thread(gmail_search, query, max_results)
                return {'success': True, 'data': results}

            elif action == "thread":
                thread_id = kwargs.get('thread_id')
                if not thread_id:
                    return {'success': False, 'error': 'thread_id required'}
                thread = await asyncio.to_thread(gmail_get_thread, thread_id)
                return {'success': True, 'data': thread}

            else:
                return {'success': False, 'error': f'Unknown action: {action}'}

        except Exception as e:
            return {'success': False, 'error': str(e)}


# Singleton instance
_bridge_instance: Optional[TUIBridge] = None

def get_tui_bridge() -> TUIBridge:
    """Get singleton TUI bridge instance."""
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = TUIBridge()
    return _bridge_instance
