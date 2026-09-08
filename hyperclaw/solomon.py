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

MODEL = os.environ.get("OPENAI_MODEL") or os.environ.get("HYPERCLAW_MODEL", "claude-sonnet-4-6")
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


def _model_family(model: str) -> str:
    """Human-readable family for a model id, or "" if unknown."""
    m = model.lower()
    if "hyperspeed" in m or m.startswith("hyper-nimbus/"):
        return "HyperAI"
    if "claude" in m:
        return "Claude"
    return ""


class ChatAgent:
    """Chat agent - manages conversation and context."""

    def __init__(self):
        from .inference import Inference
        self.inference = Inference()
        self.ai_name, self.user_name = _get_identity()
        self.system_prompt = self._load_system_prompt()

    def _load_system_prompt(self, model: str = "") -> str:
        """Load workspace context files into system prompt.

        model: the model id actually serving the conversation; defaults to
        the configured MODEL so callers routing to another provider can
        rebuild the prompt with the real model."""
        model = model or MODEL
        family = _model_family(model)
        family_clause = f", part of the {family} model family" if family else ""
        whose = (f", the personal AI assistant to {self.user_name}"
                 if self.user_name else ", a helpful AI assistant")
        parts = [
            f"You are {self.ai_name}{whose}.",
            (f"You run on HyperClaw. The model serving this conversation is "
             f"`{model}`{family_clause}. If asked your name, you are "
             f"{self.ai_name}; if asked what model you use, give that model "
             f"id exactly."),
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
        response, _ = await self.inference.complete(
            self._prepare_messages(message, history), self._load_system_prompt(self._model()),
            max_tokens=MAX_TOKENS)
        return extract_text(response)

    def _model(self, slot="primary"):
        candidates = self.inference.candidates(slot)
        return candidates[0][1] if candidates else "unconfigured"

    async def stream_events(self, message: str, history: list[dict],
                            attachments: list | None = None) -> AsyncIterator[tuple]:
        needs = {"chat", "streaming"}
        for block in attachments or []:
            if block.get("type") == "image":
                needs.add("images")
            elif block.get("type") == "document":
                needs.add("documents")
        slot = "vision" if attachments else "primary"
        async for item in self.inference.stream_events(
                self._prepare_messages(message, history, attachments),
                self._load_system_prompt(self._model(slot)), slot=slot,
                required_capabilities=needs, max_tokens=MAX_TOKENS):
            yield item

    async def stream_chat(self, message: str, history: list[dict]) -> AsyncIterator[str]:
        async for kind, text in self.stream_events(message, history):
            if kind == "text":
                yield text

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
