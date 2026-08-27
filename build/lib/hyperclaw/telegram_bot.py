"""
HyperClaw Telegram Bot
Routes messages to SOLOMON, maintains per-chat conversation history.
"""

import os
import logging
from collections import defaultdict
from typing import Optional

import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .solomon import get_solomon

load_dotenv()

logger = logging.getLogger("hyperclaw.telegram")

# Config
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED_CHAT_IDS = set(
    int(cid.strip())
    for cid in os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",")
    if cid.strip()
)
MAX_HISTORY = 20


class TelegramBot:
    """HyperClaw Telegram bot — routes to SOLOMON."""

    def __init__(self):
        self.application: Optional[Application] = None
        self.conversation_history: dict[int, list[dict]] = defaultdict(list)
        self.solomon = get_solomon()

    def _is_allowed(self, chat_id: int) -> bool:
        """Check if chat_id is in the allowlist."""
        return chat_id in ALLOWED_CHAT_IDS

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command."""
        if not update.effective_chat or not self._is_allowed(update.effective_chat.id):
            return

        greeting = (
            "⚡ HyperClaw online.\n\n"
            "Ready to assist.\n"
            "HyperClaw systems operational.\n\n"
            "/status - System status\n"
            "/clear - Clear history"
        )
        await update.message.reply_text(greeting)

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status command — show system status."""
        if not update.effective_chat or not self._is_allowed(update.effective_chat.id):
            return

        chat_id = update.effective_chat.id
        history_count = len(self.conversation_history.get(chat_id, []))

        # Try to get ATLAS_TRADING status
        trading_status = "offline"
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get("http://localhost:5001/status")
                if resp.status_code == 200:
                    data = resp.json()
                    trading_status = data.get("status", "unknown")
        except Exception:
            trading_status = "unreachable"

        # Get system uptime
        try:
            import subprocess
            result = subprocess.run(["uptime"], capture_output=True, text=True, timeout=5)
            uptime_line = result.stdout.strip()
        except Exception:
            uptime_line = "unknown"

        # Memory stats
        try:
            import psutil
            mem = psutil.virtual_memory()
            mem_stats = f"{mem.percent}% used ({mem.used // (1024**3)}GB / {mem.total // (1024**3)}GB)"
        except Exception:
            mem_stats = "unavailable"

        status_text = (
            f"⚡ **Status**\n"
            f"Agent: online\n"
            f"ATLAS_TRADING: {trading_status}\n"
            f"Uptime: {uptime_line}\n"
            f"Memory: {mem_stats}\n"
            f"History: {history_count} messages"
        )
        await update.message.reply_text(status_text, parse_mode="Markdown")

    async def clear_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /clear command — clear conversation history."""
        if not update.effective_chat or not self._is_allowed(update.effective_chat.id):
            return

        chat_id = update.effective_chat.id
        self.conversation_history[chat_id] = []
        await update.message.reply_text("Conversation history cleared.")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming text messages — stream response from SOLOMON."""
        if not update.effective_chat or not self._is_allowed(update.effective_chat.id):
            logger.warning(f"Blocked message from unauthorized chat: {update.effective_chat.id}")
            return

        if not update.message or not update.message.text:
            return

        chat_id = update.effective_chat.id
        user_message = update.message.text.strip()

        if not user_message:
            return

        logger.info(f"[{chat_id}] User: {user_message[:50]}...")

        # Send typing indicator
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        # Get history for this chat
        history = self.conversation_history[chat_id]

        try:
            # Send placeholder — user sees something immediately
            placeholder = await update.message.reply_text("⚡")

            buffer = ""
            last_edit = ""
            full_response = []
            EDIT_THRESHOLD = 60   # chars accumulated before pushing an edit
            edit_count = 0
            MAX_EDITS = 60        # stay well under Telegram rate limits (~20 edits/msg practical limit)

            async for chunk in self.solomon.stream_chat(user_message, history):
                buffer += chunk
                full_response.append(chunk)

                # Push update when buffer is large enough or chunk ends a sentence
                should_update = (
                    len(buffer) >= EDIT_THRESHOLD
                    or (len(buffer) >= 20 and chunk.endswith((".", "!", "?", "\n")))
                )

                if should_update and edit_count < MAX_EDITS:
                    current_text = "".join(full_response)
                    if current_text != last_edit and len(current_text) <= 4096:
                        try:
                            await placeholder.edit_text(current_text)
                            last_edit = current_text
                            edit_count += 1
                            buffer = ""
                        except Exception:
                            pass  # rate limit or no-change — keep accumulating

            # Final edit with complete response
            response = "".join(full_response)

            if response != last_edit:
                if len(response) <= 4096:
                    try:
                        await placeholder.edit_text(response)
                    except Exception:
                        pass
                else:
                    # Response too long — delete placeholder, send in chunks
                    try:
                        await placeholder.delete()
                    except Exception:
                        pass
                    for i in range(0, len(response), 4096):
                        await update.message.reply_text(response[i:i + 4096])

            # Update history
            history.append({"role": "user", "content": user_message})
            history.append({"role": "assistant", "content": response})

            # Trim to max history
            if len(history) > MAX_HISTORY * 2:
                self.conversation_history[chat_id] = history[-(MAX_HISTORY * 2):]

            logger.info(f"[{chat_id}] SOLOMON streamed: {response[:50]}...")

        except Exception as e:
            logger.error(f"[{chat_id}] Error: {e}", exc_info=True)
            await update.message.reply_text(f"⚡ Error: {e}")

    async def send_message(self, chat_id: int, text: str) -> bool:
        """Send a message to a specific chat (for scheduler alerts)."""
        if not self.application:
            logger.error("Application not initialized")
            return False

        try:
            # Handle Telegram's 4096 char limit
            if len(text) <= 4096:
                await self.application.bot.send_message(chat_id=chat_id, text=text)
            else:
                for i in range(0, len(text), 4096):
                    await self.application.bot.send_message(chat_id=chat_id, text=text[i:i + 4096])
            return True
        except Exception as e:
            logger.error(f"Failed to send message to {chat_id}: {e}")
            return False

    def build(self) -> Application:
        """Build and return the Telegram application."""
        self.application = Application.builder().token(BOT_TOKEN).build()

        # Register handlers
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CommandHandler("clear", self.clear_command))
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
        )

        return self.application


# Singleton instance
_bot: Optional[TelegramBot] = None


def get_telegram_bot() -> TelegramBot:
    """Get or create the Telegram bot singleton."""
    global _bot
    if _bot is None:
        _bot = TelegramBot()
    return _bot
