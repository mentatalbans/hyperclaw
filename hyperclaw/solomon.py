"""
HyperClaw Chat Agent
Simple chat interface used by Telegram bot and scheduler (chat-only, no tools).
Users can customize the AI name and personality via workspace files.
"""

import os
import asyncio
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Optional

import anthropic
from hyperclaw.api_utils import extract_text
from dotenv import load_dotenv

load_dotenv()

# Paths - use environment variable or default to ~/.hyperclaw
HYPERCLAW_ROOT = Path(os.environ.get("HYPERCLAW_ROOT", Path.home() / ".hyperclaw"))
WORKSPACE_PATH = HYPERCLAW_ROOT / "workspace"
MEMORY_PATH = HYPERCLAW_ROOT / "memory"

MODEL = os.environ.get("HYPERCLAW_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = int(os.environ.get("HYPERCLAW_MAX_TOKENS", 4096))
MAX_HISTORY = 20

# Context files to load (in order, skip if missing)
CONTEXT_FILES = [
    "ASSISTANT.md",  # AI personality and name
    "USER.md",       # User preferences
    "MEMORY.md",     # Working memory
]


def _get_identity() -> tuple:
    """(ai_name, user_name) — onboarding writes ~/.hyperclaw/config.json;
    older builds used config/settings.json. Check both, in that order."""
    import json
    for cf in (HYPERCLAW_ROOT / "config.json",
               HYPERCLAW_ROOT / "config" / "settings.json"):
        if cf.exists():
            try:
                config = json.loads(cf.read_text())
                return (config.get("ai_name") or os.environ.get("HYPERCLAW_AI_NAME", "Assistant"),
                        config.get("user_name") or "")
            except Exception:
                continue
    return (os.environ.get("HYPERCLAW_AI_NAME", "Assistant"), "")


def _get_ai_name() -> str:
    return _get_identity()[0]


class ChatAgent:
    """Chat agent - manages conversation and context."""

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        self.ai_name, self.user_name = _get_identity()
        self.system_prompt = self._load_system_prompt()

    def _load_system_prompt(self) -> str:
        """Load workspace context files into system prompt."""
        whose = (f", the personal AI assistant to {self.user_name}"
                 if self.user_name else ", a helpful AI assistant")
        parts = [
            f"You are {self.ai_name}{whose}.",
            (f"You run on HyperClaw, powered by the Anthropic model `{MODEL}`. "
             f"If asked your name, you are {self.ai_name}; if asked what model "
             f"you use, say so plainly."),
            "",
            "## Core Behaviors",
            "- Be helpful, accurate, and concise",
            "- Execute tasks proactively when given clear instructions",
            "- Ask clarifying questions when needed",
            "- Be honest about limitations",
            "",
            f"Current date: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
        ]

        # The TUI persona file is the primary source of custom behavior
        claude_md = HYPERCLAW_ROOT / "CLAUDE.md"
        if claude_md.exists():
            try:
                parts.append("## Persona & standing instructions\n"
                             + claude_md.read_text(encoding="utf-8") + "\n")
            except Exception:
                pass

        # Load custom context files if they exist
        for filename in CONTEXT_FILES:
            filepath = WORKSPACE_PATH / filename
            if filepath.exists():
                try:
                    content = filepath.read_text(encoding="utf-8")
                    parts.append(f"## {filename}\n{content}\n")
                except Exception:
                    pass

        # Load today's log if exists
        today = datetime.now().strftime("%Y-%m-%d")
        log_path = MEMORY_PATH / f"{today}.md"
        if log_path.exists():
            try:
                content = log_path.read_text(encoding="utf-8")
                parts.append(f"## Today's Log ({today})\n{content}\n")
            except Exception:
                pass

        return "\n".join(parts)

    async def chat(self, message: str, history: list[dict]) -> str:
        """Send a message and get a response."""
        messages = self._prepare_messages(message, history)

        try:
            response = await asyncio.to_thread(
                self.client.messages.create,
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=self.system_prompt,
                messages=messages,
            )
            return extract_text(response)
        except anthropic.APIError as e:
            return f"[Error: {e}]"
        except Exception as e:
            return f"[Error: {e}]"

    async def stream_events(self, message: str, history: list[dict],
                            attachments: list | None = None) -> AsyncIterator[tuple]:
        """Stream ("thinking", delta) and ("text", delta) tuples.

        Lets UIs render the model's thinking while it works
        and then stream the answer. Thinking deltas only occur on models
        with adaptive thinking; text-only models just yield text tuples.
        """
        messages = self._prepare_messages(message, history, attachments)
        try:
            async_client = anthropic.AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
            async with async_client.messages.stream(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=self.system_prompt,
                messages=messages,
            ) as stream:
                async for event in stream:
                    if event.type == "content_block_delta":
                        dtype = getattr(event.delta, "type", "")
                        if dtype == "thinking_delta":
                            yield ("thinking", event.delta.thinking)
                        elif dtype == "text_delta":
                            yield ("text", event.delta.text)
        except anthropic.APIError as e:
            yield ("text", f"[Error: {e}]")
        except Exception as e:
            yield ("text", f"[Error: {e}]")

    async def stream_chat(self, message: str, history: list[dict]) -> AsyncIterator[str]:
        """Stream a response token by token."""
        messages = self._prepare_messages(message, history)

        try:
            async_client = anthropic.AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
            async with async_client.messages.stream(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=self.system_prompt,
                messages=messages,
            ) as stream:
                async for text in stream.text_stream:
                    yield text

        except anthropic.APIError as e:
            yield f"[Error: {e}]"
        except Exception as e:
            yield f"[Error: {e}]"

    def _prepare_messages(self, message: str, history: list[dict],
                          attachments: list | None = None) -> list[dict]:
        """Prepare messages for the API call, trimming to MAX_HISTORY.

        attachments: optional Anthropic content blocks (document / image)
        that ride along with the user's text — how Telegram file uploads
        reach the model."""
        recent = history[-MAX_HISTORY:] if len(history) > MAX_HISTORY else history
        if attachments:
            content = list(attachments) + [{"type": "text", "text": message or "(see attached)"}]
            return list(recent) + [{"role": "user", "content": content}]
        return list(recent) + [{"role": "user", "content": message}]

    def reload_context(self) -> None:
        """Reload the system prompt (hot-reload without restart)."""
        self.ai_name = _get_ai_name()
        self.system_prompt = self._load_system_prompt()


# Singleton instance
_agent: Optional[ChatAgent] = None


def get_chat_agent() -> ChatAgent:
    """Get or create the chat agent singleton."""
    global _agent
    if _agent is None:
        _agent = ChatAgent()
    return _agent


# Backwards compatibility aliases
Solomon = ChatAgent
get_solomon = get_chat_agent
