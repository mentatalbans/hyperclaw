"""
HyperClaw Telegram Bot
Routes messages to SOLOMON, maintains per-chat conversation history.
"""

import os
import json as _json
import logging
from collections import defaultdict
from pathlib import Path as _Path
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

from pathlib import Path

HYPERCLAW_ROOT = Path(os.environ.get("HYPERCLAW_ROOT", Path.home() / ".hyperclaw"))
# ~/.hyperclaw/.env is where onboarding saves credentials; CWD .env as fallback
load_dotenv(HYPERCLAW_ROOT / ".env")
load_dotenv()

logger = logging.getLogger("hyperclaw.telegram")

TENANT_ID = os.environ.get("OWNER_ID", "local")

_db_pool = None


async def _get_db_pool():
    global _db_pool
    if _db_pool is not None:
        return _db_pool
    mount = _Path(os.environ.get("SECRETS_MOUNT", "/mnt/secrets")) / "hyperclaw_prod_shared_civilization-db"
    try:
        import asyncpg
        if mount.exists():
            cfg = _json.loads(mount.read_text())
            host, port, database = cfg["host"], int(cfg["port"]), cfg["database"]
            user, password = cfg["username"], cfg["password"]
        else:
            host = os.environ.get("POSTGRES_HOST", "")
            if not host:
                return None
            port = int(os.environ.get("POSTGRES_PORT", 5432))
            database = os.environ.get("POSTGRES_DB", "hypernimbus")
            user = os.environ.get("POSTGRES_USER", "")
            password = os.environ.get("POSTGRES_PASSWORD", "")
        _db_pool = await asyncpg.create_pool(
            host=host, port=port, database=database,
            user=user, password=password, ssl="require",
            min_size=1, max_size=3,
        )
        logger.info("DB pool connected")
        return _db_pool
    except Exception as e:
        logger.warning(f"DB unavailable — using in-memory history: {e}")
        return None


async def _load_history(tenant_id: str, chat_id: int) -> list:
    pool = await _get_db_pool()
    if not pool:
        return []
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT messages FROM hyperclaw_sessions WHERE tenant_id=$1 AND chat_id=$2",
                tenant_id, chat_id,
            )
            return _json.loads(row["messages"]) if row else []
    except Exception as e:
        logger.warning(f"[{chat_id}] DB load failed: {e}")
        return []


async def _save_history(tenant_id: str, chat_id: int, messages: list) -> None:
    pool = await _get_db_pool()
    if not pool:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO hyperclaw_sessions (tenant_id, chat_id, messages, updated_at)
                VALUES ($1, $2, $3::jsonb, now())
                ON CONFLICT (tenant_id, chat_id) DO UPDATE
                SET messages = EXCLUDED.messages, updated_at = now()
                """,
                tenant_id, chat_id, _json.dumps(messages),
            )
    except Exception as e:
        logger.warning(f"[{chat_id}] DB save failed: {e}")


def _allowed_chat_ids() -> set:
    """Union of TELEGRAM_ALLOWED_CHAT_IDS (comma list) and TELEGRAM_CHAT_ID
    (single ID written by onboarding). Also reads from CSI-mounted secret file.
    Deny-by-default when all sources are empty."""
    ids = set()
    for var in ("TELEGRAM_ALLOWED_CHAT_IDS", "TELEGRAM_CHAT_ID"):
        for cid in os.environ.get(var, "").split(","):
            cid = cid.strip()
            if cid.lstrip("-").isdigit():
                ids.add(int(cid))
    mount = _Path(os.environ.get("SECRETS_MOUNT", "/mnt/secrets")) / "telegram"
    if mount.exists():
        try:
            raw = _json.loads(mount.read_text())
            for key in ("TELEGRAM_CHAT_ID", "ALLOWED_CHAT_IDS"):
                for cid in str(raw.get(key, "")).split(","):
                    cid = cid.strip()
                    if cid.lstrip("-").isdigit():
                        ids.add(int(cid))
        except Exception:
            pass
    return ids


# Config
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED_CHAT_IDS = _allowed_chat_ids()
MAX_HISTORY = 20
# Show the model's thinking live in an italic preview before the answer
# streams in. Set TELEGRAM_SHOW_THINKING=0 to disable.
SHOW_THINKING = os.environ.get("TELEGRAM_SHOW_THINKING", "1") not in ("0", "false", "no")


class TelegramBot:
    """HyperClaw Telegram bot — routes to SOLOMON."""

    def __init__(self):
        self.application: Optional[Application] = None
        self.conversation_history: dict[int, list[dict]] = defaultdict(list)
        self.pending_attachments: dict[int, list] = defaultdict(list)
        self._db_loaded: set[int] = set()
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
        self._db_loaded.discard(chat_id)
        await _save_history(TENANT_ID, chat_id, [])
        await update.message.reply_text("Conversation history cleared.")

    MAX_FILE_BYTES = 20 * 1024 * 1024  # Telegram bot API download cap

    async def handle_file(self, update, context) -> None:
        """Receive a document or photo and stage it for the next model turn.

        PDFs and images go to the model natively as Anthropic content
        blocks; small text files are inlined; anything else is noted by
        name so the model can at least acknowledge it."""
        chat_id = update.effective_chat.id
        if chat_id not in ALLOWED_CHAT_IDS:
            return
        import base64

        msg = update.message
        try:
            if msg.photo:
                tg_file = await msg.photo[-1].get_file()
                raw = bytes(await tg_file.download_as_bytearray())
                block = {"type": "image",
                         "source": {"type": "base64", "media_type": "image/jpeg",
                                    "data": base64.b64encode(raw).decode()}}
                label = "photo"
            else:
                doc = msg.document
                if doc.file_size and doc.file_size > self.MAX_FILE_BYTES:
                    await msg.reply_text(f"That file is too large for me to pull down "
                                         f"({doc.file_size // (1024*1024)} MB - limit 20 MB).")
                    return
                tg_file = await doc.get_file()
                raw = bytes(await tg_file.download_as_bytearray())
                name = doc.file_name or "file"
                mime = (doc.mime_type or "").lower()
                if mime == "application/pdf" or name.lower().endswith(".pdf"):
                    block = {"type": "document",
                             "source": {"type": "base64", "media_type": "application/pdf",
                                        "data": base64.b64encode(raw).decode()}}
                elif mime.startswith("image/"):
                    block = {"type": "image",
                             "source": {"type": "base64", "media_type": mime,
                                        "data": base64.b64encode(raw).decode()}}
                elif mime.startswith("text/") or name.lower().endswith(
                        (".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".py", ".log")):
                    text = raw.decode("utf-8", errors="replace")[:100_000]
                    block = {"type": "text", "text": f"[Contents of {name}]\n\n{text}"}
                else:
                    block = {"type": "text",
                             "text": f"[The user sent a file named {name} ({mime or 'unknown type'}) "
                                     f"that could not be read directly.]"}
                label = name
            self.pending_attachments[chat_id].append(block)
            logger.info(f"[{chat_id}] staged attachment: {label}")
        except Exception as e:
            logger.error(f"[{chat_id}] file intake failed: {e}", exc_info=True)
            await msg.reply_text(f"Couldn't read that file: {e}")
            return

        caption = (msg.caption or "").strip()
        if caption:
            # A caption is a question about the file - answer it immediately.
            # PTB Message objects are frozen; bypass to inject the caption as text.
            object.__setattr__(update.message, "text", caption)
            await self.handle_message(update, context)
        else:
            await msg.reply_text(f"Got it - {label} received. Ask me anything about it.")

    async def _finalize_markdown(self, placeholder, text: str) -> None:
        """Re-render the finished answer with Markdown; keep the plain-text
        version if Telegram rejects the entities (unbalanced markers)."""
        try:
            await placeholder.edit_text(text, parse_mode="Markdown")
        except Exception:
            pass

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

        # Lazy-load history from DB on first message this session
        if chat_id not in self._db_loaded:
            db_history = await _load_history(TENANT_ID, chat_id)
            if db_history:
                self.conversation_history[chat_id] = db_history
            self._db_loaded.add(chat_id)

        history = self.conversation_history[chat_id]

        # Telegram expires the typing indicator after ~5s — keep it alive for
        # the whole turn so the user always sees the bot working.
        import asyncio as _asyncio

        typing_alive = True

        # First indicator immediately — the refresh task only keeps it alive.
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            pass

        async def _keep_typing():
            while typing_alive:
                await _asyncio.sleep(4)
                if not typing_alive:
                    break
                try:
                    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                except Exception:
                    pass

        typing_task = _asyncio.create_task(_keep_typing())

        try:
            # Placeholder — the user sees activity from the first moment
            placeholder = await update.message.reply_text("💭 thinking…")

            buffer = ""
            last_edit = ""
            full_response = []
            thinking_buf = []
            answer_started = False
            EDIT_THRESHOLD = 60   # chars accumulated before pushing an edit
            edit_count = 0
            MAX_EDITS = 60        # stay well under Telegram rate limits (~20 edits/msg practical limit)

            attachments = self.pending_attachments.pop(chat_id, [])
            async for kind, chunk in self.solomon.stream_events(
                    user_message, history, attachments=attachments):
                if kind == "thinking":
                    if not SHOW_THINKING or answer_started:
                        continue
                    thinking_buf.append(chunk)
                    buffer += chunk
                    # Update the italic thinking preview at the same cadence
                    if len(buffer) >= EDIT_THRESHOLD and edit_count < MAX_EDITS:
                        preview = "".join(thinking_buf)[-800:]
                        text = f"💭 _{preview}_"
                        if text != last_edit:
                            try:
                                await placeholder.edit_text(text, parse_mode="Markdown")
                                last_edit = text
                                edit_count += 1
                                buffer = ""
                            except Exception:
                                pass
                    continue

                # kind == "text" — the answer itself
                if not answer_started:
                    answer_started = True
                    buffer = ""
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

            if len(response) <= 4096:
                # Push the complete text, then re-render with Markdown so
                # **bold** and lists display properly instead of raw markers.
                if response != last_edit:
                    try:
                        await placeholder.edit_text(response)
                    except Exception:
                        pass
                await self._finalize_markdown(placeholder, response)
            if response != last_edit:
                if len(response) <= 4096:
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

            await _save_history(TENANT_ID, chat_id, self.conversation_history[chat_id])
            logger.info(f"[{chat_id}] SOLOMON streamed: {response[:50]}...")

        except Exception as e:
            logger.error(f"[{chat_id}] Error: {e}", exc_info=True)
            await update.message.reply_text(f"⚡ Error: {e}")
        finally:
            typing_alive = False
            typing_task.cancel()

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

    def build(self, token: str = "") -> Application:
        """Build and return the Telegram application."""
        self.application = Application.builder().token(token or BOT_TOKEN).build()

        # Register handlers
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CommandHandler("clear", self.clear_command))
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
        )
        self.application.add_handler(
            MessageHandler(filters.Document.ALL | filters.PHOTO, self.handle_file)
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


def main() -> None:
    """Run the HyperClaw Telegram bot (entry point: hyperclaw-telegram)."""
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("httpx", "httpcore", "telegram.ext"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if not BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set. Add it to ~/.hyperclaw/.env "
                         "(get a token from @BotFather with /newbot).")
    if not ALLOWED_CHAT_IDS:
        raise SystemExit("No allowed chats configured. Set TELEGRAM_CHAT_ID or "
                         "TELEGRAM_ALLOWED_CHAT_IDS in ~/.hyperclaw/.env — "
                         "the bot denies all messages otherwise.")

    # Drain any stale getUpdates connection before starting the polling loop.
    # Without this, Telegram returns a Conflict error when a previous process
    # didn't shut down cleanly (the long-poll stays open on Telegram's side for ~30s).
    async def _drain():
        import asyncio as _asyncio
        from telegram import Bot
        async with Bot(token=BOT_TOKEN) as _bot:
            await _bot.get_updates(offset=-1, timeout=0)
    import asyncio as _asyncio
    _asyncio.run(_drain())

    bot = get_telegram_bot()
    app = bot.build()
    from hyperclaw.providers import registry
    logger.info(registry().startup_line())
    logger.info(f"HyperClaw Telegram bot starting — {len(ALLOWED_CHAT_IDS)} allowed chat(s), "
                f"thinking preview {'on' if SHOW_THINKING else 'off'}")
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
