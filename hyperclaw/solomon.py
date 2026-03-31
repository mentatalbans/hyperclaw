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
from dotenv import load_dotenv

load_dotenv()

# Paths - use environment variable or default to ~/.hyperclaw
HYPERCLAW_ROOT = Path(os.environ.get("HYPERCLAW_ROOT", Path.home() / ".hyperclaw"))
WORKSPACE_PATH = HYPERCLAW_ROOT / "workspace"
MEMORY_PATH = HYPERCLAW_ROOT / "memory"

MODEL = os.environ.get("HYPERCLAW_MODEL", "claude-sonnet-4-20250514")
MAX_TOKENS = int(os.environ.get("HYPERCLAW_MAX_TOKENS", 4096))
MAX_HISTORY = 20

# Context files to load (in order, skip if missing)
CONTEXT_FILES = [
    "ASSISTANT.md",  # AI personality and name
    "USER.md",       # User preferences
    "MEMORY.md",     # Working memory
]


def _get_ai_name() -> str:
    """Get AI name from config or default."""
    config_file = HYPERCLAW_ROOT / "config" / "settings.json"
    if config_file.exists():
        try:
            import json
            config = json.loads(config_file.read_text())
            return config.get("ai_name", "Assistant")
        except Exception:
            pass
    return os.environ.get("HYPERCLAW_AI_NAME", "Assistant")


class ChatAgent:
    """Chat agent - manages conversation and context."""

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        self.ai_name = _get_ai_name()
        self.system_prompt = self._load_system_prompt()

    def _load_system_prompt(self) -> str:
        """Load workspace context files into system prompt."""
        parts = [
            f"You are {self.ai_name}, a helpful AI assistant.",
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
            return response.content[0].text
        except anthropic.APIError as e:
            return f"[Error: {e}]"
        except Exception as e:
            return f"[Error: {e}]"

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

    def _prepare_messages(self, message: str, history: list[dict]) -> list[dict]:
        """Prepare messages for the API call, trimming to MAX_HISTORY."""
        recent = history[-MAX_HISTORY:] if len(history) > MAX_HISTORY else history
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
