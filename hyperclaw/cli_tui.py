#!/usr/bin/env python3
"""
HyperClaw Terminal UI — Production-ready with signal handling and smart model routing.
Features:
- Ctrl+C to interrupt streaming
- Automatic model selection (cheap for simple, powerful for complex)
- Persistent memory across sessions
- Out-of-box setup verification
- Better error recovery
"""

import asyncio
import json
import os
import signal
import sys
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

# Ensure we can import hyperclaw modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

# ============================================================================
# CONFIGURATION
# ============================================================================

HYPERCLAW_ROOT = Path(os.environ.get("HYPERCLAW_ROOT", Path.home() / ".hyperclaw"))
WORKSPACE = HYPERCLAW_ROOT / "workspace"
MEMORY_DIR = HYPERCLAW_ROOT / "memory"
SECRETS_DIR = WORKSPACE / "secrets"
SESSION_FILE = HYPERCLAW_ROOT / "session_history.json"
CONFIG_FILE = HYPERCLAW_ROOT / "config.json"

# Load environment
env_path = SECRETS_DIR / ".env"
if env_path.exists():
    load_dotenv(env_path)
else:
    load_dotenv()

# Terminal colors
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"

# ============================================================================
# SIGNAL HANDLING
# ============================================================================

STOP_STREAMING = False
STREAMING_ACTIVE = False


def signal_handler(signum, frame):
    """Handle Ctrl+C to interrupt streaming."""
    global STOP_STREAMING
    if STREAMING_ACTIVE:
        STOP_STREAMING = True
        print(f"\n{YELLOW}[Interrupting...]{RESET}", flush=True)
    else:
        print(f"\n{DIM}Use /quit to exit{RESET}")


# Install signal handler
signal.signal(signal.SIGINT, signal_handler)


# ============================================================================
# CONFIGURATION LOADING
# ============================================================================

def load_config() -> dict:
    """Load config with AI/user names."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    return {"user_name": "User", "ai_name": "HyperClaw"}


CONFIG = load_config()
AI_NAME = CONFIG.get("ai_name", "HyperClaw")
USER_NAME = CONFIG.get("user_name", "User")


def check_setup() -> dict:
    """Check if HyperClaw is set up properly."""
    checks = {
        "api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "workspace": WORKSPACE.exists(),
        "memory": MEMORY_DIR.exists(),
        "chatjimmy": bool(os.environ.get("CHATJIMMY_API_KEY")),
        "database": bool(os.environ.get("DATABASE_URL")),
    }
    return checks


def ensure_setup():
    """Ensure directories exist."""
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    (MEMORY_DIR / "daily").mkdir(exist_ok=True)


# ============================================================================
# SESSION MANAGEMENT
# ============================================================================

def load_session() -> list:
    """Load previous session history."""
    if SESSION_FILE.exists():
        try:
            data = json.loads(SESSION_FILE.read_text())
            return data[-30:] if len(data) > 30 else data
        except Exception:
            pass
    return []


def save_session(history: list):
    """Save session history."""
    try:
        to_save = history[-50:] if len(history) > 50 else history
        SESSION_FILE.write_text(json.dumps(to_save, indent=2))
    except Exception:
        pass


def load_memory_context() -> str:
    """Load memory files for context."""
    parts = []

    files = [
        WORKSPACE / "SOUL.md",
        WORKSPACE / "IDENTITY.md",
        WORKSPACE / "USER.md",
        WORKSPACE / "MEMORY.md",
        MEMORY_DIR / "instincts.md",
    ]

    for f in files:
        if f.exists():
            try:
                content = f.read_text()[:3000]
                parts.append(f"## {f.name}\n{content}")
            except Exception:
                pass

    # Today's log
    today = datetime.now().strftime("%Y-%m-%d")
    today_log = MEMORY_DIR / "daily" / f"{today}.md"
    if today_log.exists():
        try:
            content = today_log.read_text()[-2000:]
            parts.append(f"## Today's Log\n{content}")
        except Exception:
            pass

    return "\n\n".join(parts)


# ============================================================================
# CHAT ENGINE
# ============================================================================

class TUIChat:
    """Terminal UI Chat with smart routing and signal handling."""

    def __init__(self):
        self.history = []
        self.model_router = None
        self.memory_manager = None
        self._initialized = False

    async def initialize(self):
        """Initialize components."""
        if self._initialized:
            return

        try:
            from hyperclaw.model_router import get_model_router
            self.model_router = get_model_router()
        except ImportError:
            pass

        try:
            from hyperclaw.memory_manager import get_memory_manager
            self.memory_manager = await get_memory_manager()
        except ImportError:
            pass

        self._initialized = True

    def build_system_prompt(self) -> str:
        """Build system prompt with memory context."""
        base = f"""You are {AI_NAME}, an AI assistant with full computer control capabilities.

## Core Behaviors
- Be proactive and execute tasks efficiently
- Be concise but thorough when needed
- Use the right tool for each job
- Remember context across conversations

## Available Tools
- bash: Run any terminal command
- read_file, write_file, edit_file: File operations
- web_fetch, web_search: Internet access
- screenshot: See the screen
- applescript: macOS automation
- And more...

Current time: {datetime.now().strftime('%Y-%m-%d %H:%M')}
"""
        memory_context = load_memory_context()
        if memory_context:
            base += f"\n\n--- MEMORY ---\n{memory_context}"

        return base

    async def chat(self, message: str, stream: bool = True) -> str:
        """Send a message and get response with streaming."""
        global STOP_STREAMING, STREAMING_ACTIVE

        if not self._initialized:
            await self.initialize()

        # Classify complexity for model selection
        is_simple = self._is_simple_query(message)

        # Select model based on complexity
        if is_simple and self.model_router:
            model_id = "chatjimmy" if os.environ.get("CHATJIMMY_API_KEY") else "claude-haiku"
            model_name = "ChatJimmy" if model_id == "chatjimmy" else "Haiku"
        else:
            model_id = "claude-sonnet"
            model_name = "Sonnet"

        print(f"{DIM}[{model_name}]{RESET} ", end="", flush=True)

        # Add to history
        self.history.append({"role": "user", "content": message})

        system = self.build_system_prompt()

        try:
            if self.model_router and not stream:
                # Use model router for non-streaming
                response, metadata = await self.model_router.call(
                    message=message,
                    system=system,
                    history=self.history[:-1],
                )
                self.history.append({"role": "assistant", "content": response})
                return response
            else:
                # Direct streaming with Anthropic
                return await self._stream_anthropic(message, system, model_id)

        except Exception as e:
            error_msg = f"[Error: {str(e)[:100]}]"
            print(f"{RED}{error_msg}{RESET}")
            return error_msg

    async def _stream_anthropic(self, message: str, system: str, model_id: str) -> str:
        """Stream response from Anthropic API with interrupt handling."""
        global STOP_STREAMING, STREAMING_ACTIVE
        import anthropic

        STOP_STREAMING = False
        STREAMING_ACTIVE = True

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return "[Error: ANTHROPIC_API_KEY not set]"

        client = anthropic.Anthropic(api_key=api_key)

        # Map model_id to actual model
        model_map = {
            "chatjimmy": "claude-haiku-4-5-20251001",  # Fallback if ChatJimmy unavailable
            "claude-haiku": "claude-haiku-4-5-20251001",
            "claude-sonnet": "claude-sonnet-4-20250514",
            "claude-opus": "claude-opus-4-20250514",
        }
        model = model_map.get(model_id, "claude-sonnet-4-20250514")

        full_response = []
        start_time = time.time()
        max_time = 120  # 2 minute timeout

        try:
            with client.messages.stream(
                model=model,
                max_tokens=4096,
                system=system,
                messages=self.history,
            ) as stream:
                print(f"{MAGENTA}{BOLD}{AI_NAME}:{RESET} ", end="", flush=True)

                for text in stream.text_stream:
                    # Check for interrupt
                    if STOP_STREAMING:
                        print(f"\n{YELLOW}[Stopped]{RESET}")
                        break

                    # Check timeout
                    if time.time() - start_time > max_time:
                        print(f"\n{YELLOW}[Timeout]{RESET}")
                        break

                    full_response.append(text)
                    print(text, end="", flush=True)

                print()  # Newline at end

        except anthropic.APIError as e:
            print(f"\n{RED}API Error: {e}{RESET}")
        except Exception as e:
            print(f"\n{RED}Error: {e}{RESET}")
        finally:
            STREAMING_ACTIVE = False

        response_text = "".join(full_response)
        if response_text:
            self.history.append({"role": "assistant", "content": response_text})

        return response_text

    def _is_simple_query(self, message: str) -> bool:
        """Check if query is simple enough for fast model."""
        message_lower = message.lower().strip()

        # Very short messages
        if len(message) < 30:
            return True

        # Simple patterns
        simple_patterns = [
            "hi", "hello", "hey", "thanks", "ok", "yes", "no",
            "what time", "what date", "what day",
            "list", "show", "get", "check",
        ]

        for pattern in simple_patterns:
            if message_lower.startswith(pattern):
                return True

        return False

    def reset(self):
        """Reset conversation history."""
        self.history = []


# ============================================================================
# SLASH COMMANDS
# ============================================================================

def handle_command(cmd: str, chat: TUIChat) -> bool:
    """Handle slash commands. Returns True if should continue loop."""
    cmd = cmd.lower().strip()

    if cmd in ("/quit", "/exit", "/q"):
        save_session(chat.history)
        print(f"{DIM}Goodbye.{RESET}\n")
        return False

    elif cmd == "/reset":
        chat.reset()
        save_session([])
        print(f"{GREEN}Conversation reset.{RESET}\n")

    elif cmd == "/status":
        checks = check_setup()
        print(f"\n{BOLD}HyperClaw Status{RESET}")
        print(f"  API Key: {'OK' if checks['api_key'] else 'Missing'}")
        print(f"  ChatJimmy: {'OK' if checks['chatjimmy'] else 'Not configured (optional)'}")
        print(f"  Database: {'OK' if checks['database'] else 'Not configured (optional)'}")
        print(f"  History: {len(chat.history)} messages")
        print()

    elif cmd == "/models":
        print(f"\n{BOLD}Available Models{RESET}")
        print(f"  ChatJimmy - Simple queries (cheapest)")
        print(f"  Claude Haiku - Fast tasks")
        print(f"  Claude Sonnet - Complex tasks (default)")
        print(f"  Claude Opus - Deep reasoning")
        print()

    elif cmd == "/save":
        save_session(chat.history)
        print(f"{GREEN}Session saved.{RESET}\n")

    elif cmd == "/help":
        print(f"\n{BOLD}Commands{RESET}")
        print(f"  /reset   - Clear conversation")
        print(f"  /status  - Show system status")
        print(f"  /models  - List available models")
        print(f"  /save    - Save session")
        print(f"  /help    - Show this help")
        print(f"  /quit    - Exit")
        print()
        print(f"{BOLD}Tips{RESET}")
        print(f"  - Press Ctrl+C to interrupt streaming")
        print(f"  - Simple queries use cheaper models automatically")
        print()

    else:
        print(f"{YELLOW}Unknown command: {cmd}{RESET}")
        print(f"{DIM}Type /help for available commands{RESET}\n")

    return True


# ============================================================================
# MAIN
# ============================================================================

async def main_async():
    """Async main loop."""
    # Check setup
    checks = check_setup()

    if not checks["api_key"]:
        print(f"\n{RED}ANTHROPIC_API_KEY not set.{RESET}")
        print(f"{DIM}Run 'hyperclaw setup' or set the environment variable.{RESET}\n")
        return

    # Ensure directories
    ensure_setup()

    # Initialize chat
    chat = TUIChat()
    await chat.initialize()

    # Load previous session
    chat.history = load_session()
    session_msg = f"Resumed ({len(chat.history)} msgs)" if chat.history else "New session"

    # Welcome message
    print(f"\n{CYAN}{BOLD}=== HyperClaw ==={RESET}")
    print(f"{DIM}AI Assistant with smart model routing{RESET}")
    print(f"{DIM}{session_msg} | Ctrl+C to stop | /help for commands{RESET}")

    if checks["chatjimmy"]:
        print(f"{DIM}ChatJimmy enabled for cost savings{RESET}")

    print()

    # Main loop
    while True:
        try:
            user_input = input(f"{CYAN}>{RESET} ").strip()

            if not user_input:
                continue

            # Handle commands
            if user_input.startswith("/"):
                if not handle_command(user_input, chat):
                    break
                continue

            # Chat
            await chat.chat(user_input)
            save_session(chat.history)
            print()

        except KeyboardInterrupt:
            if not STREAMING_ACTIVE:
                print(f"\n{DIM}Use /quit to exit{RESET}")
        except EOFError:
            save_session(chat.history)
            print(f"\n{DIM}Goodbye.{RESET}")
            break


def main():
    """Entry point."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
