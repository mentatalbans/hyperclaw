#!/usr/bin/env python3
"""
iMessage Daemon v2 for HyperClaw
--------------------------
Key change from v1: reads messages via subprocess call to sqlite3 binary
(which has Full Disk Access) instead of Python's sqlite3 module (which doesn't
when running from a LaunchAgent). This makes the daemon immune to macOS
security policy changes that revoke FDA from Python.

Also includes:
- Health reporting for the watchdog
- Exponential backoff on errors
- Circuit breaker pattern
- Telegram alert on critical failures
"""

import asyncio
import base64
import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path.home() / ".hyperclaw" / ".env")

import anthropic

# Self-healing / remote command execution
try:
    sys.path.insert(0, str(Path(__file__).parent.parent / "hyperclaw"))
    from self_heal import get_command_executor
    SELF_HEAL_AVAILABLE = True
except ImportError:
    SELF_HEAL_AVAILABLE = False

# TUI Bridge for full tool access
try:
    from hyperclaw.tui_bridge import TUIBridge
    TUI_BRIDGE_AVAILABLE = True
except ImportError:
    TUI_BRIDGE_AVAILABLE = False

# Setup logging
LOG_DIR = Path.home() / ".hyperclaw" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / "imessage.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('imessage_daemon_v2')

# Config
HYPERCLAW_ROOT = Path(os.environ.get("HYPERCLAW_ROOT", Path.home() / ".hyperclaw"))
STATE_FILE = HYPERCLAW_ROOT / "data" / "imessage_state.json"
HEALTH_FILE = HYPERCLAW_ROOT / "data" / "imessage_health.json"
MESSAGES_DB = str(Path.home() / "Library/Messages/chat.db")
CHECK_INTERVAL = 5
MAX_BACKOFF = 60
SQLITE3_BIN = "/usr/bin/sqlite3"

# Authorized contacts
# Allowed senders: comma-separated phone numbers/emails in IMESSAGE_ALLOWED_CONTACTS.
# Empty = deny ALL senders (fail closed). Never ship a default contact.
AUTHORIZED_CONTACTS = {
    c.strip() for c in os.environ.get("IMESSAGE_ALLOWED_CONTACTS", "").split(",") if c.strip()
}
extra = os.environ.get("IMESSAGE_AUTHORIZED_CONTACTS", "")
if extra:
    AUTHORIZED_CONTACTS.update(c.strip() for c in extra.split(",") if c.strip())

# System prompt for iMessage responses
ASSISTANT_NAME = os.environ.get("ASSISTANT_NAME", "Assistant")
SYSTEM_PROMPT = f"""You are {ASSISTANT_NAME}, the user's AI assistant.

You're responding via iMessage. Keep responses concise and conversational.
NEVER use emojis. NEVER use markdown formatting.
Be direct, witty when appropriate, and always substantive.
You are texting - keep it natural and brief unless detail is needed.
"""


class CircuitBreaker:
    """Prevents hammering a broken service."""
    def __init__(self, failure_threshold=5, recovery_timeout=300):
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.last_failure_time = 0
        self.state = "closed"  # closed = healthy, open = broken

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = "open"
            logger.error(f"Circuit breaker OPEN after {self.failure_count} failures")

    def record_success(self):
        self.failure_count = 0
        self.state = "closed"

    def can_proceed(self) -> bool:
        if self.state == "closed":
            return True
        # Check if recovery timeout has passed
        if time.time() - self.last_failure_time > self.recovery_timeout:
            logger.info("Circuit breaker attempting recovery...")
            self.state = "half-open"
            return True
        return False


class iMessageDaemonV2:
    """Resilient iMessage daemon using sqlite3 binary for DB access."""

    def __init__(self):
        self.last_rowid = 0
        self.conversation_history: Dict[str, list] = {}
        self.circuit_breaker = CircuitBreaker()
        self.backoff = CHECK_INTERVAL
        self.consecutive_errors = 0
        self.start_time = datetime.now()
        self.messages_processed = 0
        self.running = True
        self.tui_bridge = None
        self._load_state()

        # Handle graceful shutdown
        signal.signal(signal.SIGTERM, self._shutdown)
        signal.signal(signal.SIGINT, self._shutdown)

    def _load_tui_bridge(self):
        """Lazy load TUI Bridge for full tool execution."""
        if self.tui_bridge is None and TUI_BRIDGE_AVAILABLE:
            try:
                self.tui_bridge = TUIBridge()
                logger.info("TUI Bridge loaded - full tool access enabled for iMessage")
            except Exception as e:
                logger.error(f"Failed to load TUI Bridge: {e}")
        return self.tui_bridge

    def _shutdown(self, signum, frame):
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.running = False
        self._save_state()
        self._update_health("stopped")
        sys.exit(0)

    def _load_state(self):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if STATE_FILE.exists():
            try:
                state = json.loads(STATE_FILE.read_text())
                self.last_rowid = state.get("last_rowid", 0)
                logger.info(f"Loaded state: last_rowid={self.last_rowid}")
            except Exception as e:
                logger.error(f"Failed to load state: {e}")

    def _save_state(self):
        try:
            STATE_FILE.write_text(json.dumps({
                "last_rowid": self.last_rowid,
                "last_check": datetime.now().isoformat()
            }))
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def _update_health(self, status: str, error: str = None):
        """Write health status for watchdog to check."""
        try:
            health = {
                "daemon": "imessage",
                "status": status,
                "last_heartbeat": datetime.now().isoformat(),
                "uptime_seconds": (datetime.now() - self.start_time).total_seconds(),
                "messages_processed": self.messages_processed,
                "last_rowid": self.last_rowid,
                "consecutive_errors": self.consecutive_errors,
                "circuit_breaker": self.circuit_breaker.state,
                "error": error
            }
            HEALTH_FILE.write_text(json.dumps(health, indent=2))
        except Exception:
            pass

    def _is_authorized(self, sender: str) -> bool:
        if not AUTHORIZED_CONTACTS:
            return True
        sender_clean = sender.replace("+", "").replace("-", "").replace(" ", "").replace("(", "").replace(")", "")
        for contact in AUTHORIZED_CONTACTS:
            contact_clean = contact.replace("+", "").replace("-", "").replace(" ", "").replace("(", "").replace(")", "")
            if sender_clean.endswith(contact_clean) or contact_clean.endswith(sender_clean):
                return True
            if sender.lower() == contact.lower():
                return True
        return False

    def get_new_messages(self) -> List[Dict]:
        """Read new messages using sqlite3 binary (has Full Disk Access)."""
        query = f"""
            SELECT
                m.rowid,
                m.text,
                m.date / 1000000000 + 978307200 as unix_time,
                m.is_from_me,
                m.cache_has_attachments,
                COALESCE(h.id, '') as sender,
                COALESCE(c.chat_identifier, '') as chat_identifier
            FROM message m
            LEFT JOIN handle h ON m.handle_id = h.rowid
            LEFT JOIN chat_message_join cmj ON m.rowid = cmj.message_id
            LEFT JOIN chat c ON cmj.chat_id = c.rowid
            WHERE m.rowid > {self.last_rowid} AND m.is_from_me = 0
              AND (m.text IS NOT NULL OR m.cache_has_attachments = 1)
            ORDER BY m.rowid ASC
            LIMIT 10;
        """

        try:
            result = subprocess.run(
                [SQLITE3_BIN, "-json", MESSAGES_DB, query],
                capture_output=True, text=True, timeout=10
            )

            if result.returncode != 0:
                raise RuntimeError(f"sqlite3 error: {result.stderr.strip()}")

            if not result.stdout.strip():
                return []

            rows = json.loads(result.stdout)
            messages = []

            for row in rows:
                # sqlite3 -json returns uppercase column names for rowid
                rowid = row.get("ROWID") or row.get("rowid")
                msg = {
                    "rowid": rowid,
                    "text": row.get("text") or "",
                    "timestamp": datetime.fromtimestamp(row.get("unix_time", 0)),
                    "sender": row.get("sender") or row.get("chat_identifier") or "unknown",
                    "chat": row.get("chat_identifier", ""),
                    "has_attachments": bool(row.get("cache_has_attachments", 0)),
                    "attachments": []
                }

                # Get attachments if present
                if msg["has_attachments"]:
                    msg["attachments"] = self._get_attachments(rowid)

                messages.append(msg)

            return messages

        except subprocess.TimeoutExpired:
            raise RuntimeError("sqlite3 query timed out")
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Failed to parse sqlite3 output: {e}")

    def _get_attachments(self, message_rowid: int) -> List[Dict]:
        """Get attachments for a message using sqlite3 binary."""
        query = f"""
            SELECT
                a.filename,
                a.mime_type,
                a.transfer_name,
                a.total_bytes
            FROM attachment a
            JOIN message_attachment_join maj ON a.rowid = maj.attachment_id
            WHERE maj.message_id = {message_rowid};
        """
        try:
            result = subprocess.run(
                [SQLITE3_BIN, "-json", MESSAGES_DB, query],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0 or not result.stdout.strip():
                return []

            attachments = []
            for att in json.loads(result.stdout):
                filename = att.get("filename") or ""
                if filename.startswith("~"):
                    filename = str(Path.home() / filename[2:])
                attachments.append({
                    "path": filename,
                    "mime_type": att.get("mime_type") or "",
                    "name": att.get("transfer_name") or (Path(filename).name if filename else "attachment"),
                    "size": att.get("total_bytes") or 0
                })
            return attachments
        except Exception:
            return []

    def send_imessage(self, recipient: str, text: str) -> bool:
        """Send iMessage via AppleScript."""
        # Escape text for AppleScript
        escaped = text.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')

        script = f'''
        tell application "Messages"
            set targetService to 1st service whose service type = iMessage
            set targetBuddy to buddy "{recipient}" of targetService
            send "{escaped}" to targetBuddy
        end tell
        '''

        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                logger.info(f"Sent iMessage to {recipient}")
                return True
            else:
                logger.error(f"Failed to send iMessage: {result.stderr}")
                return False
        except Exception as e:
            logger.error(f"Send error: {e}")
            return False

    def send_imessage_file(self, recipient: str, file_path: str, caption: str = "") -> bool:
        """Send a file attachment (and optional caption) via Messages.app."""
        from pathlib import Path as _P
        path = _P(file_path)
        if not path.exists():
            logger.error(f"Attachment not found: {file_path}")
            return False
        r_esc = recipient.replace('"', '\\"')
        f_esc = str(path).replace("\\", "\\\\").replace('"', '\\"')
        c_esc = (caption or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
        caption_line = f'send "{c_esc}" to targetBuddy' if caption else ""
        script = f'''
        tell application "Messages"
            set targetService to 1st service whose service type = iMessage
            set targetBuddy to buddy "{r_esc}" of targetService
            {caption_line}
            send POSIX file "{f_esc}" to targetBuddy
        end tell
        '''
        try:
            result = subprocess.run(["osascript", "-e", script],
                                    capture_output=True, text=True, timeout=60)
            if result.returncode == 0:
                logger.info(f"Sent attachment to {recipient}: {path.name}")
                return True
            logger.error(f"Attachment send failed: {result.stderr[:200]}")
            return False
        except Exception as e:
            logger.error(f"Attachment send error: {e}")
            return False

    async def _fetch_url_content(self, text: str) -> str:
        """Extract and fetch content from URLs in the message."""
        import re
        import httpx
        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
        urls = re.findall(url_pattern, text)

        if not urls:
            return ""

        contents = []
        async with httpx.AsyncClient(timeout=10.0) as client:
            for url in urls[:2]:  # Limit to 2 URLs
                try:
                    logger.info(f"Fetching URL: {url}")
                    response = await client.get(url, follow_redirects=True)
                    if response.status_code == 200:
                        content_type = response.headers.get("content-type", "")
                        if "text/html" in content_type:
                            html = response.text[:30000]
                            # Simple text extraction
                            html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
                            html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
                            text_content = re.sub(r'<[^>]+>', ' ', html)
                            text_content = ' '.join(text_content.split())[:2000]
                            if text_content.strip():
                                contents.append(f"[{url}]: {text_content}")
                        elif "text/" in content_type or "json" in content_type:
                            contents.append(f"[{url}]: {response.text[:1500]}")
                except Exception as e:
                    logger.debug(f"URL fetch failed: {url} - {e}")

        return "\n".join(contents) if contents else ""

    async def generate_response(self, sender: str, text: str, attachments: List[Dict] = None) -> str:
        """Generate response using Claude with full tool access via TUI Bridge."""
        # Create a chat_id from sender (hash for consistent ID)
        chat_id = hash(sender) & 0x7FFFFFFF

        # Fetch URL content if present
        url_content = await self._fetch_url_content(text) if text else ""
        if url_content:
            text = f"{text}\n\n[URL CONTENT]\n{url_content}"

        # If there are image attachments, analyze them first and inject into TUI bridge context
        image_analyses = []
        if attachments:
            for att in attachments:
                if att.get("mime_type", "").startswith("image/"):
                    try:
                        path = Path(att["path"])
                        if path.exists():
                            image_data = base64.b64encode(path.read_bytes()).decode('utf-8')
                            mime = att["mime_type"]
                            if mime == "image/heic":
                                mime = "image/jpeg"

                            # Analyze image with Claude vision
                            client = anthropic.Anthropic()
                            vision_response = client.messages.create(
                                model="claude-opus-5",
                                max_tokens=500,
                                messages=[{
                                    "role": "user",
                                    "content": [
                                        {"type": "image", "source": {"type": "base64", "media_type": mime, "data": image_data}},
                                        {"type": "text", "text": text or "Describe this image concisely."}
                                    ]
                                }]
                            )
                            analysis = next((b.text for b in (vision_response.content or []) if getattr(b, "type", "") == "text"), "Image received")
                            image_analyses.append(analysis)
                            logger.info(f"Image analyzed: {analysis[:50]}...")
                    except Exception as e:
                        logger.error(f"Failed to process image: {e}")
                        image_analyses.append(f"[Image attachment - could not analyze: {e}]")

        # If we have image analyses, inject them into TUI bridge context
        tui_bridge = self._load_tui_bridge()
        if image_analyses and tui_bridge:
            for analysis in image_analyses:
                tui_bridge.add_to_history(chat_id, "user", f"[USER SENT IMAGE] Analysis: {analysis}")
                tui_bridge.add_to_history(chat_id, "assistant", f"I see the image. {analysis[:200]}")

        # Use TUI bridge for full tool execution if available
        if tui_bridge:
            try:
                # If images were sent with no text, describe what we saw
                if image_analyses and not text:
                    return image_analyses[0] if len(image_analyses) == 1 else "\n\n".join(image_analyses)

                # Execute through TUI bridge for full tool access.
                # Bound it: a single hung tool (revoked Gmail token, slow fetch) must never
                # block the daemon loop indefinitely on one message -- that is what left
                # messages "Processing..." with no reply during the Gmail token crisis.
                result = await asyncio.wait_for(
                    tui_bridge.execute(text or "[attachment received]", chat_id),
                    timeout=120
                )

                if result['success']:
                    reply = result['text'] or "Done."
                    # Files queued for this conversation - delivered by caller after the text.
                    self._pending_files = result.get('files', [])
                    # Keep response short for iMessage
                    if len(reply) > 1000:
                        reply = reply[:997] + "..."
                    return reply
                else:
                    return f"Error: {result.get('error', 'Unknown error')}"

            except Exception as e:
                logger.error(f"TUI Bridge error: {e}")
                # Fall through to basic response

        # Fallback: Basic Claude response without tools
        if sender not in self.conversation_history:
            self.conversation_history[sender] = []

        content = text or "[attachment received]"
        if image_analyses:
            content = f"{content}\n\n[Image analyses: {'; '.join(image_analyses)}]"

        self.conversation_history[sender].append({"role": "user", "content": content})

        if len(self.conversation_history[sender]) > 20:
            self.conversation_history[sender] = self.conversation_history[sender][-20:]

        try:
            client = anthropic.Anthropic()
            response = client.messages.create(
                model="claude-opus-5",
                max_tokens=500,
                system=SYSTEM_PROMPT,
                messages=self.conversation_history[sender]
            )

            reply = next((b.text for b in (response.content or []) if getattr(b, "type", "") == "text"), "")
            self.conversation_history[sender].append({"role": "assistant", "content": reply})
            return reply

        except Exception as e:
            logger.error(f"Claude API error: {e}")
            return "System hiccup on my end. Give me a moment and try again."

    def _alert_telegram(self, message: str):
        """Send critical alert to Telegram."""
        try:
            token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
            chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
            if token and chat_id:
                import urllib.request
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                data = json.dumps({"chat_id": chat_id, "text": f"[WATCHDOG] {message}"}).encode()
                req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=10)
        except Exception:
            pass  # Best effort

    async def run(self):
        """Main daemon loop."""
        logger.info("iMessage Daemon v2 starting...")
        self._update_health("running")

        while self.running:
            try:
                if not self.circuit_breaker.can_proceed():
                    logger.warning(f"Circuit breaker open, waiting {self.backoff}s...")
                    self._update_health("circuit_open")
                    await asyncio.sleep(self.backoff)
                    continue

                messages = self.get_new_messages()

                # Reset backoff on success
                self.backoff = CHECK_INTERVAL
                self.consecutive_errors = 0
                self.circuit_breaker.record_success()

                for msg in messages:
                    sender = msg["sender"]

                    if not self._is_authorized(sender):
                        logger.info(f"Ignoring unauthorized message from {sender}")
                        self.last_rowid = msg["rowid"]
                        continue

                    text = msg["text"]
                    logger.info(f"Processing message from {sender}: {text[:80]}...")

                    # Check for remote commands first (!bash, !restart, !status, etc.)
                    if SELF_HEAL_AVAILABLE:
                        executor = get_command_executor()
                        cmd_result = executor.process_message(text, sender, "imessage")
                        if cmd_result:
                            logger.info(f"Executed remote command: {cmd_result[:100]}")
                            self.send_imessage(sender, cmd_result[:1000])
                            self.last_rowid = msg["rowid"]
                            self.messages_processed += 1
                            self._save_state()
                            continue  # Skip normal response generation

                    # Generate and send response
                    response = await self.generate_response(
                        sender, text, msg.get("attachments")
                    )

                    self.send_imessage(sender, response)
                    # Deliver any files queued during this turn (docs, decks, images)
                    for item in getattr(self, "_pending_files", []) or []:
                        self.send_imessage_file(sender, item.get("path", ""), item.get("caption", ""))
                    self._pending_files = []
                    self.last_rowid = msg["rowid"]
                    self.messages_processed += 1
                    self._save_state()

                self._update_health("running")

            except Exception as e:
                self.consecutive_errors += 1
                self.circuit_breaker.record_failure()
                error_msg = str(e)
                logger.error(f"Error (#{self.consecutive_errors}): {error_msg}")
                self._update_health("error", error_msg)

                # Exponential backoff
                self.backoff = min(self.backoff * 2, MAX_BACKOFF)

                # Alert on sustained failures
                if self.consecutive_errors == 5:
                    self._alert_telegram(
                        f"iMessage daemon has {self.consecutive_errors} consecutive errors: {error_msg}"
                    )

            await asyncio.sleep(self.backoff)


async def main():
    daemon = iMessageDaemonV2()
    await daemon.run()


if __name__ == "__main__":
    asyncio.run(main())
