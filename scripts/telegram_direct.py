#!/usr/bin/env python3
"""
HyperClaw Telegram Bot - Direct polling with TUI Bridge integration.
Full tool execution - can create docs, send emails, control computer, etc.
"""

import asyncio
import base64
import json
import logging
import os
import signal
import sys
import time
import fcntl
from pathlib import Path
from collections import defaultdict

import anthropic
import httpx
from dotenv import load_dotenv

# Downloads directory for attachments
DOWNLOADS_DIR = Path.home() / '.hyperclaw' / 'downloads' / 'telegram'
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Add hyperclaw to path
sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv()

# Hooks integration
try:
    from hyperclaw.hooks import trigger_hook, HookEvent
    HOOKS_AVAILABLE = True
except ImportError:
    HOOKS_AVAILABLE = False

# Memory integration
try:
    from hyperclaw.memory_bus import get_memory_bus
    MEMORY_AVAILABLE = True
except ImportError:
    MEMORY_AVAILABLE = False

# Config
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED_CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
LOCK_FILE = Path("/tmp/gil_telegram.lock")
MAX_HISTORY = 20

# Configure logging
_LOG_DIR = Path(os.environ.get("HYPERCLAW_ROOT", str(Path.home() / ".hyperclaw"))) / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(str(_LOG_DIR / 'telegram_direct.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('telegram_direct')


class SingleInstanceLock:
    """Ensure only one instance runs at a time."""

    def __init__(self):
        self.lock_file = None

    def acquire(self) -> bool:
        try:
            self.lock_file = open(LOCK_FILE, 'w')
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.lock_file.write(str(os.getpid()))
            self.lock_file.flush()
            return True
        except (IOError, OSError):
            logger.error("Another instance is already running")
            return False

    def release(self):
        if self.lock_file:
            try:
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
                self.lock_file.close()
                LOCK_FILE.unlink(missing_ok=True)
            except Exception:
                pass


class GilTelegramBot:
    """HyperClaw Telegram bot with full TUI Bridge tool access."""

    SESSION_TIMEOUT_MINUTES = 30  # Trigger new session after this much silence

    def __init__(self):
        self.running = True
        self.offset = 0
        self.client = httpx.AsyncClient(timeout=30.0)
        self.lock = SingleInstanceLock()
        self.conversation_history: dict[int, list[dict]] = defaultdict(list)
        self.tui_bridge = None
        self._last_message_time: float = 0
        self._session_active: bool = False
        self._current_session_id: str = None

    def signal_handler(self, signum, frame):
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.running = False

    def _load_tui_bridge(self):
        """Lazy load TUI Bridge for full tool execution."""
        if self.tui_bridge is None:
            try:
                from hyperclaw.tui_bridge import TUIBridge
                self.tui_bridge = TUIBridge()
                logger.info("TUI Bridge loaded - full tool access enabled")
            except Exception as e:
                logger.error(f"Failed to load TUI Bridge: {e}")
                return None
        return self.tui_bridge

    @staticmethod
    def _split_for_telegram(text: str, limit: int = 4096) -> list:
        """Split text into <=limit chunks, preferring paragraph/line/space boundaries so long
        replies are delivered IN FULL across multiple messages instead of being truncated."""
        text = text or ""
        if len(text) <= limit:
            return [text] if text else [""]
        chunks, remaining = [], text
        while len(remaining) > limit:
            window = remaining[:limit]
            cut = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind(" "))
            if cut < limit // 2:  # no sensible boundary -> hard cut
                cut = limit
            chunks.append(remaining[:cut].rstrip())
            remaining = remaining[cut:].lstrip()
        if remaining:
            chunks.append(remaining)
        return chunks

    async def send_message(self, text: str, parse_mode: str = None) -> dict:
        """Send message to the allowed chat, splitting across Telegram's 4096-char limit."""
        try:
            result = {}
            for chunk in self._split_for_telegram(text):
                payload = {"chat_id": ALLOWED_CHAT_ID, "text": chunk}
                if parse_mode:
                    payload["parse_mode"] = parse_mode
                response = await self.client.post(f"{API_BASE}/sendMessage", json=payload)
                if response.status_code == 200:
                    result = response.json().get("result", {})
                else:
                    logger.error(f"Failed to send message: {response.status_code}")
            return result

        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return {}

    async def send_full(self, placeholder_message_id, text: str):
        """Deliver a full response: first chunk replaces the placeholder, the rest follow as
        new messages. Replaces the old response[:4096] truncation in the media/voice handlers."""
        chunks = self._split_for_telegram(text or "")
        if placeholder_message_id:
            await self.edit_message(placeholder_message_id, chunks[0] if chunks else "")
            rest = chunks[1:]
        else:
            rest = chunks
        for extra in rest:
            await self.send_message(extra)

    async def send_typing(self):
        """Send typing indicator."""
        try:
            payload = {"chat_id": ALLOWED_CHAT_ID, "action": "typing"}
            await self.client.post(f"{API_BASE}/sendChatAction", json=payload)
        except Exception:
            pass

    async def send_photo(self, photo_path: str, caption: str = None) -> dict:
        """Send a photo to the chat."""
        try:
            path = Path(photo_path)
            if not path.exists():
                logger.error(f"Photo not found: {photo_path}")
                return {}

            with open(path, 'rb') as photo:
                files = {'photo': photo}
                data = {'chat_id': ALLOWED_CHAT_ID}
                if caption:
                    data['caption'] = caption[:1024]

                response = await self.client.post(
                    f"{API_BASE}/sendPhoto",
                    data=data,
                    files=files
                )

                if response.status_code == 200:
                    logger.info(f"Sent photo: {photo_path}")
                    return response.json().get("result", {})
                else:
                    logger.error(f"Failed to send photo: {response.status_code}")
                    return {}

        except Exception as e:
            logger.error(f"Error sending photo: {e}")

    async def send_file(self, file_path: str, caption: str = None) -> dict:
        """Send any file to the chat - photo/video/audio/document auto-detected."""
        try:
            path = Path(file_path)
            if not path.exists():
                logger.error(f"File not found: {file_path}")
                return {}
            ext = path.suffix.lower()
            if ext in {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.heic', '.bmp'}:
                method, field = 'sendPhoto', 'photo'
            elif ext in {'.mp4', '.mov', '.m4v', '.avi', '.webm'}:
                method, field = 'sendVideo', 'video'
            elif ext in {'.mp3', '.m4a', '.wav', '.ogg', '.flac', '.aiff'}:
                method, field = 'sendAudio', 'audio'
            else:
                method, field = 'sendDocument', 'document'

            with open(path, 'rb') as f:
                files = {field: (path.name, f)}
                data = {'chat_id': ALLOWED_CHAT_ID}
                if caption:
                    data['caption'] = caption[:1024]
                response = await self.client.post(f"{API_BASE}/{method}", data=data, files=files)

            if response.status_code == 200:
                logger.info(f"Sent file: {file_path} ({method})")
                return response.json().get("result", {})

            # Odd image formats can fail sendPhoto - retry once as a document.
            if method == 'sendPhoto':
                with open(path, 'rb') as f:
                    response = await self.client.post(
                        f"{API_BASE}/sendDocument",
                        data=data, files={'document': (path.name, f)})
                if response.status_code == 200:
                    logger.info(f"Sent file as document fallback: {file_path}")
                    return response.json().get("result", {})
            logger.error(f"Failed to send file: {response.status_code} {response.text[:200]}")
            return {}
        except Exception as e:
            logger.error(f"Error sending file: {e}")
            return {}
            return {}

    async def edit_message(self, message_id: int, text: str) -> bool:
        """Edit an existing message."""
        try:
            if len(text) > 4096:
                text = text[:4093] + "..."

            payload = {
                "chat_id": ALLOWED_CHAT_ID,
                "message_id": message_id,
                "text": text,
            }

            response = await self.client.post(f"{API_BASE}/editMessageText", json=payload)
            return response.status_code == 200
        except Exception:
            return False

    async def download_file(self, file_id: str, filename: str = None) -> Path:
        """Download a file from Telegram servers."""
        try:
            # Get file path from Telegram
            response = await self.client.get(f"{API_BASE}/getFile", params={"file_id": file_id})
            if response.status_code != 200:
                logger.error(f"Failed to get file info: {response.status_code}")
                return None

            file_info = response.json().get("result", {})
            file_path = file_info.get("file_path")
            if not file_path:
                logger.error("No file_path in response")
                return None

            # Download the file
            download_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
            response = await self.client.get(download_url)
            if response.status_code != 200:
                logger.error(f"Failed to download file: {response.status_code}")
                return None

            # Save to disk
            if not filename:
                filename = Path(file_path).name
            local_path = DOWNLOADS_DIR / f"{int(time.time())}_{filename}"
            local_path.write_bytes(response.content)
            logger.info(f"Downloaded: {local_path}")
            return local_path

        except Exception as e:
            logger.error(f"Download error: {e}")
            return None

    async def _get_services_status(self) -> str:
        """Get status of all services for /services command."""
        import subprocess

        services = {
            "telegram": "telegram_supervisor.py",
            "hermes": "hermes_email_daemon.py",
            "imessage": "imessage_daemon",
            "watchdog": "watchdog.py",
        }

        lines = ["Service Status\n"]

        for name, pattern in services.items():
            # Check if running
            try:
                result = subprocess.run(
                    ["pgrep", "-f", pattern],
                    capture_output=True, text=True, timeout=5
                )
                running = result.returncode == 0
            except Exception:
                running = False

            # Read health file
            health_file = Path.home() / ".hyperclaw" / "data" / f"{name}_health.json"
            health_info = ""
            if health_file.exists():
                try:
                    health = json.loads(health_file.read_text())
                    status = health.get("status", "unknown")
                    error = health.get("error", "")
                    if error:
                        health_info = f" - {error[:40]}"
                    elif status == "degraded":
                        health_info = " - degraded"
                except Exception:
                    pass

            icon = "[OK]" if running else "[DOWN]"
            lines.append(f"  {icon} {name.upper()}{health_info}")

        return "\n".join(lines)

    async def _extract_url_content(self, text: str) -> str:
        """Extract and fetch content from URLs in the message."""
        import re
        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
        urls = re.findall(url_pattern, text)

        if not urls:
            return ""

        contents = []
        for url in urls[:3]:  # Limit to 3 URLs
            try:
                logger.info(f"Fetching URL: {url}")
                response = await self.client.get(url, timeout=10.0, follow_redirects=True)
                if response.status_code == 200:
                    content_type = response.headers.get("content-type", "")
                    if "text/html" in content_type:
                        # Extract text from HTML
                        html = response.text[:50000]
                        # Simple text extraction (strip tags)
                        import re as regex
                        # Remove script/style
                        html = regex.sub(r'<script[^>]*>.*?</script>', '', html, flags=regex.DOTALL | regex.IGNORECASE)
                        html = regex.sub(r'<style[^>]*>.*?</style>', '', html, flags=regex.DOTALL | regex.IGNORECASE)
                        # Remove tags
                        text_content = regex.sub(r'<[^>]+>', ' ', html)
                        # Clean whitespace
                        text_content = ' '.join(text_content.split())[:3000]
                        if text_content.strip():
                            contents.append(f"[{url}]\n{text_content}")
                    elif "application/json" in content_type:
                        contents.append(f"[{url}]\n{response.text[:2000]}")
                    elif "text/" in content_type:
                        contents.append(f"[{url}]\n{response.text[:2000]}")
            except Exception as e:
                logger.debug(f"URL fetch failed: {url} - {e}")

        return "\n\n".join(contents) if contents else ""

    async def analyze_image(self, image_path: Path, caption: str = None) -> str:
        """Analyze an image using Claude's vision API."""
        try:
            # Read and encode image
            image_data = base64.b64encode(image_path.read_bytes()).decode('utf-8')

            # Determine media type
            suffix = image_path.suffix.lower()
            media_types = {
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.png': 'image/png',
                '.gif': 'image/gif',
                '.webp': 'image/webp',
            }
            media_type = media_types.get(suffix, 'image/jpeg')

            # Build prompt
            prompt = caption if caption else "What's in this image? Describe it concisely."

            # Call Claude vision
            client = anthropic.Anthropic()
            response = client.messages.create(
                model="claude-opus-5",
                max_tokens=1024,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_data
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }]
            )

            return response.content[0].text if response.content else "Could not analyze image."

        except Exception as e:
            logger.error(f"Image analysis error: {e}")
            return f"Image analysis failed: {str(e)[:100]}"

    async def handle_document(self, document: dict, caption: str = None) -> str:
        """Handle document attachments."""
        try:
            file_id = document.get("file_id")
            file_name = document.get("file_name", "document")
            mime_type = document.get("mime_type", "")

            # Download the document
            local_path = await self.download_file(file_id, file_name)
            if not local_path:
                return "Failed to download document."

            # Handle based on type
            if mime_type.startswith("image/"):
                return await self.analyze_image(local_path, caption)

            elif mime_type == "application/pdf":
                # Try to extract text from PDF
                try:
                    import pypdf
                    reader = pypdf.PdfReader(str(local_path))
                    text = ""
                    for page in reader.pages[:10]:  # First 10 pages
                        text += page.extract_text() + "\n"

                    if text.strip():
                        # Summarize with Claude
                        prompt = caption if caption else f"Summarize this PDF content concisely:\n\n{text[:8000]}"
                        client = anthropic.Anthropic()
                        response = client.messages.create(
                            model="claude-opus-5",
                            max_tokens=1000,
                            messages=[{"role": "user", "content": prompt}]
                        )
                        return response.content[0].text if response.content else f"PDF extracted. Content: {text[:500]}..."
                    else:
                        return "PDF appears to be image-based. Text extraction not available."
                except ImportError:
                    return f"PDF received: {file_name}. PyPDF not available for text extraction."

            elif mime_type in ("text/plain", "text/csv", "application/json"):
                # Read text files directly
                content = local_path.read_text(errors='ignore')[:5000]
                prompt = caption if caption else f"Analyze this file content concisely:\n\n{content}"
                client = anthropic.Anthropic()
                response = client.messages.create(
                    model="claude-opus-5",
                    max_tokens=1000,
                    messages=[{"role": "user", "content": prompt}]
                )
                return response.content[0].text if response.content else f"File content:\n{content[:1000]}"

            elif mime_type.startswith("audio/") or file_name.endswith(('.mp3', '.m4a', '.wav', '.ogg', '.opus', '.flac', '.aac')):
                # Transcribe audio files
                try:
                    from hyperclaw.voice_input import voice_transcribe
                    transcription = voice_transcribe(str(local_path), use_whisper=True)

                    if not transcription or transcription.startswith("Error"):
                        return f"Could not transcribe audio: {transcription}"

                    # If there's a caption, use it as context
                    if caption:
                        return f"Transcription:\n{transcription}\n\nRegarding your note: {caption}"
                    else:
                        return f"Transcription:\n{transcription}"

                except ImportError:
                    return f"Audio file received: {file_name}. Transcription not available (install openai package)."

            elif mime_type.startswith("video/") or file_name.endswith(('.mp4', '.mov', '.avi', '.mkv')):
                # For videos, extract audio and transcribe
                try:
                    import subprocess
                    from hyperclaw.voice_input import voice_transcribe

                    # Extract audio using ffmpeg
                    audio_path = local_path.with_suffix('.mp3')
                    subprocess.run([
                        'ffmpeg', '-i', str(local_path), '-vn', '-acodec', 'libmp3lame',
                        '-y', str(audio_path)
                    ], capture_output=True, check=True)

                    if audio_path.exists():
                        transcription = voice_transcribe(str(audio_path), use_whisper=True)
                        audio_path.unlink()  # Cleanup

                        if transcription and not transcription.startswith("Error"):
                            return f"Video audio transcription:\n{transcription}"

                    return f"Video received: {file_name}. Audio extraction or transcription failed."

                except Exception as e:
                    return f"Video received: {file_name}. Could not process audio: {str(e)[:50]}"

            else:
                return f"Document received: {file_name} ({mime_type}). Saved to {local_path}"

        except Exception as e:
            logger.error(f"Document handling error: {e}")
            return f"Document processing failed: {str(e)[:100]}"

    async def maintain_typing(self):
        """Continuously send typing indicators."""
        try:
            while True:
                await self.send_typing()
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass

    async def get_response(self, message: str, chat_id: int) -> str:
        """Get response using TUI Bridge with full tool execution."""
        tui_bridge = self._load_tui_bridge()
        if not tui_bridge:
            return "System initializing. Try again in a moment."

        try:
            # Bound the tool-execution call: a single hung tool (revoked Gmail token,
            # slow fetch) must never freeze the poll loop and take the channel down.
            result = await asyncio.wait_for(
                tui_bridge.execute(message, chat_id), timeout=120
            )
            if result['success']:
                return result['text'] or "Done."
            else:
                return f"Error: {result.get('error', 'Unknown error')}"

        except asyncio.TimeoutError:
            logger.error("TUI Bridge timed out after 120s; returning graceful reply")
            return "That one's taking longer than expected. Still on it -- I'll follow up when it lands."
        except Exception as e:
            logger.error(f"TUI Bridge error: {e}")
            return f"Processing error. {str(e)[:100]}"

    async def handle_command(self, text: str) -> str:
        """Handle bot commands."""
        if text == "/start":
            return (
                "Online.\n\n"
                "Full tool access enabled.\n"
                "Commands: /status /services /clear /tools /heal\n\n"
                "What do you need?"
            )
        elif text == "/services":
            # Show all service health
            return await self._get_services_status()
        elif text == "/heal":
            # Trigger self-heal
            try:
                from hyperclaw.self_heal import get_service_monitor
                monitor = get_service_monitor()
                results = monitor.heal_all()
                lines = ["Self-Heal Results:"]
                for name, result in results.items():
                    icon = "[OK]" if "healthy" in result else "[!]"
                    lines.append(f"  {icon} {name}: {result}")
                return "\n".join(lines)
            except Exception as e:
                return f"Self-heal error: {e}"
        elif text == "/status":
            # Get system info
            try:
                import psutil
                mem = psutil.virtual_memory()
                mem_info = f"{mem.percent}% used"
            except Exception:
                mem_info = "unavailable"

            tui_bridge = self._load_tui_bridge()
            tool_count = len(tui_bridge.tools) if tui_bridge else 0

            return (
                f"Status\n\n"
                f"Core: Online\n"
                f"TUI Bridge: {'Active' if tui_bridge else 'Standby'}\n"
                f"Tools: {tool_count} available\n"
                f"Memory: {mem_info}\n"
                f"Mode: Full Execution"
            )
        elif text == "/clear":
            tui_bridge = self._load_tui_bridge()
            if tui_bridge:
                tui_bridge.clear_session(ALLOWED_CHAT_ID)
            return "Session cleared. Fresh context."
        elif text == "/tools":
            tui_bridge = self._load_tui_bridge()
            if tui_bridge and tui_bridge.tools:
                tool_names = sorted([t['name'] for t in tui_bridge.tools[:50]])
                return f"Available tools ({len(tui_bridge.tools)}):\n\n" + ", ".join(tool_names)
            return "Tools not loaded."

        return None

    async def handle_message(self, update: dict):
        """Handle incoming message including photos and documents."""
        try:
            message = update.get("message", {})
            chat = message.get("chat", {})
            chat_id = chat.get("id")
            text = message.get("text", "").strip()
            caption = message.get("caption", "").strip()

            # Security: only respond to allowed chat
            if chat_id != ALLOWED_CHAT_ID:
                logger.warning(f"Blocked message from unauthorized chat: {chat_id}")
                return

            # Session management - trigger new session after silence
            current_time = time.time()
            silence_minutes = (current_time - self._last_message_time) / 60 if self._last_message_time else float('inf')

            if silence_minutes >= self.SESSION_TIMEOUT_MINUTES:
                # Start new session
                try:
                    from hyperclaw.handoff import start_session, track_topic
                    session_id = f"telegram_{int(current_time)}"
                    resumption_prompt = start_session(session_id)
                    self._current_session_id = session_id
                    self._session_active = True
                    logger.info(f"New session started: {session_id} (after {int(silence_minutes)} min silence)")

                    # Inject resumption context into TUI bridge
                    tui_bridge = self._load_tui_bridge()
                    if tui_bridge and resumption_prompt and "NEW CONSCIOUSNESS" not in resumption_prompt:
                        tui_bridge._load_system_prompt()  # Refresh to include handoff
                except Exception as e:
                    logger.debug(f"Session start error (non-critical): {e}")

            self._last_message_time = current_time

            # Trigger hook for incoming message
            if HOOKS_AVAILABLE:
                try:
                    from_user = message.get("from", {})
                    await trigger_hook(HookEvent.MESSAGE_RECEIVED, {
                        "channel": "telegram",
                        "chat_id": chat_id,
                        "message_id": message.get("message_id"),
                        "text": text or caption or "",
                        "sender_id": from_user.get("id"),
                        "sender_name": f"{from_user.get('first_name', '')} {from_user.get('last_name', '')}".strip(),
                        "sender_username": from_user.get("username"),
                        "has_photo": "photo" in message,
                        "has_document": "document" in message,
                        "has_voice": "voice" in message,
                    }, source="telegram_direct")
                except Exception as e:
                    logger.debug(f"Hook trigger error: {e}")

            # Handle photos
            if "photo" in message:
                logger.info("Processing photo...")
                typing_task = asyncio.create_task(self.maintain_typing())
                placeholder = await self.send_message("Analyzing image...")

                try:
                    # Get largest photo (last in array)
                    photos = message["photo"]
                    largest = photos[-1]
                    file_id = largest.get("file_id")

                    # Download and analyze
                    local_path = await self.download_file(file_id, "photo.jpg")
                    if local_path:
                        response = await self.analyze_image(local_path, caption or "What's in this image? Be concise.")
                    else:
                        response = "Failed to download image."

                    typing_task.cancel()
                    if placeholder.get("message_id"):
                        await self.edit_message(placeholder["message_id"], response)

                    # CRITICAL: Inject into TUI bridge history so follow-up messages have context
                    tui_bridge = self._load_tui_bridge()
                    if tui_bridge:
                        image_context = f"[USER SENT IMAGE] Analysis: {response}"
                        if caption:
                            image_context = f"[USER SENT IMAGE with caption: '{caption}'] Analysis: {response}"
                        tui_bridge.add_to_history(chat_id, "user", image_context)
                        tui_bridge.add_to_history(chat_id, "assistant", f"I've analyzed the image. {response}")

                    # Also update local history
                    history = self.conversation_history[chat_id]
                    history.append({"role": "user", "content": f"[Image] {caption or 'User sent an image'}"})
                    history.append({"role": "assistant", "content": response})

                except Exception as e:
                    typing_task.cancel()
                    logger.error(f"Photo processing error: {e}")
                    if placeholder.get("message_id"):
                        await self.edit_message(placeholder["message_id"], f"Image processing failed: {str(e)[:100]}")
                return

            # Handle videos (sent via camera/gallery, not as document)
            if "video" in message:
                video = message["video"]
                video_name = video.get("file_name", "video.mp4")
                duration = video.get("duration", 0)
                file_size = video.get("file_size", 0)
                file_size_mb = round(file_size / (1024 * 1024), 1) if file_size else 0
                logger.info(f"Processing video: {video_name}, {file_size_mb}MB, {duration}s")
                typing_task = asyncio.create_task(self.maintain_typing())
                placeholder = await self.send_message(f"Processing video ({file_size_mb}MB, {duration}s)...")

                try:
                    file_id = video.get("file_id")
                    ext = Path(video_name).suffix if video_name else ".mp4"
                    local_path = await self.download_file(file_id, f"video_{int(time.time())}{ext}")

                    response_parts = []

                    if local_path:
                        # 1) Extract thumbnail and analyze visually
                        try:
                            import subprocess
                            thumb_path = local_path.with_name(local_path.stem + "_thumb.jpg")
                            subprocess.run(
                                ["ffmpeg", "-i", str(local_path), "-ss", "00:00:01", "-vframes", "1", "-y", str(thumb_path)],
                                capture_output=True, timeout=15
                            )
                            if thumb_path.exists():
                                visual = await self.analyze_image(str(thumb_path), "Describe this video frame in detail. Include ALL visible text, dashboards, metrics, charts, or UI elements.")
                                response_parts.append(f"Visual: {visual}")
                        except Exception as e:
                            logger.warning(f"Video thumbnail analysis failed: {e}")

                        # 2) Try to transcribe audio
                        try:
                            import subprocess
                            from hyperclaw.voice_input import voice_transcribe
                            audio_path = local_path.with_suffix('.mp3')
                            subprocess.run(
                                ['ffmpeg', '-i', str(local_path), '-vn', '-acodec', 'libmp3lame', '-y', str(audio_path)],
                                capture_output=True, timeout=30
                            )
                            if audio_path.exists():
                                transcription = voice_transcribe(str(audio_path), use_whisper=True)
                                audio_path.unlink(missing_ok=True)
                                if transcription and not transcription.startswith("Error"):
                                    response_parts.append(f"Audio transcript: {transcription}")
                        except Exception as e:
                            logger.warning(f"Video audio transcription failed: {e}")

                        if response_parts:
                            response = f"Video: {video_name} ({file_size_mb}MB, {duration}s)\n\n" + "\n\n".join(response_parts)
                        else:
                            response = f"Video received and saved: {video_name} ({file_size_mb}MB, {duration}s). Saved to {local_path}"
                    else:
                        response = "Failed to download video."

                    typing_task.cancel()
                    if placeholder.get("message_id"):
                        await self.send_full(placeholder["message_id"], response)

                    # Inject into TUI bridge history
                    tui_bridge = self._load_tui_bridge()
                    if tui_bridge:
                        vid_context = f"[USER SENT VIDEO: {video_name}, {file_size_mb}MB, {duration}s] Analysis: {response}"
                        if caption:
                            vid_context = f"[USER SENT VIDEO with caption: '{caption}'] Analysis: {response}"
                        tui_bridge.add_to_history(chat_id, "user", vid_context)
                        tui_bridge.add_to_history(chat_id, "assistant", response[:500])

                    history = self.conversation_history[chat_id]
                    history.append({"role": "user", "content": f"[Video: {video_name}] {caption or ''}"})
                    history.append({"role": "assistant", "content": response})

                except Exception as e:
                    typing_task.cancel()
                    logger.error(f"Video processing error: {e}", exc_info=True)
                    if placeholder.get("message_id"):
                        await self.edit_message(placeholder["message_id"], f"Video processing failed: {str(e)[:100]}")
                return

            # Handle documents
            if "document" in message:
                doc_name = message["document"].get("file_name", "document")
                logger.info(f"Processing document: {doc_name}")
                typing_task = asyncio.create_task(self.maintain_typing())
                placeholder = await self.send_message("Processing document...")

                try:
                    response = await self.handle_document(message["document"], caption)

                    typing_task.cancel()
                    if placeholder.get("message_id"):
                        await self.send_full(placeholder["message_id"], response)

                    # CRITICAL: Inject into TUI bridge history so follow-up messages have context
                    tui_bridge = self._load_tui_bridge()
                    if tui_bridge:
                        doc_context = f"[USER SENT DOCUMENT: {doc_name}] Content/Analysis: {response}"
                        if caption:
                            doc_context = f"[USER SENT DOCUMENT: {doc_name} with note: '{caption}'] Content/Analysis: {response}"
                        tui_bridge.add_to_history(chat_id, "user", doc_context)
                        tui_bridge.add_to_history(chat_id, "assistant", f"I've processed the document '{doc_name}'. {response[:500]}")

                    # Also update local history
                    history = self.conversation_history[chat_id]
                    history.append({"role": "user", "content": f"[Document: {doc_name}] {caption or ''}"})
                    history.append({"role": "assistant", "content": response})

                except Exception as e:
                    typing_task.cancel()
                    logger.error(f"Document processing error: {e}")
                    if placeholder.get("message_id"):
                        await self.edit_message(placeholder["message_id"], f"Document processing failed: {str(e)[:100]}")
                return

            # Handle voice messages - transcribe and process
            if "voice" in message:
                logger.info("Processing voice message...")
                typing_task = asyncio.create_task(self.maintain_typing())
                placeholder = await self.send_message("Listening to your voice memo...")

                try:
                    voice = message["voice"]
                    file_id = voice.get("file_id")
                    duration = voice.get("duration", 0)

                    # Download voice file
                    local_path = await self.download_file(file_id, "voice.ogg")
                    if not local_path:
                        typing_task.cancel()
                        if placeholder.get("message_id"):
                            await self.edit_message(placeholder["message_id"], "Failed to download voice memo.")
                        return

                    # Transcribe using Whisper
                    try:
                        from hyperclaw.voice_input import voice_transcribe
                        transcription = voice_transcribe(str(local_path), use_whisper=True)

                        if not transcription or transcription.startswith("Error"):
                            typing_task.cancel()
                            if placeholder.get("message_id"):
                                await self.edit_message(placeholder["message_id"], f"Could not transcribe voice memo: {transcription}")
                            return

                        logger.info(f"Transcribed ({duration}s): {transcription[:50]}...")

                        # Update placeholder to show we heard it
                        if placeholder.get("message_id"):
                            await self.edit_message(placeholder["message_id"], f"Heard: \"{transcription[:100]}{'...' if len(transcription) > 100 else ''}\"\n\nProcessing...")

                        # Process transcribed text through TUI Bridge
                        tui_bridge = self._load_tui_bridge()
                        if tui_bridge:
                            result = await tui_bridge.execute(transcription, chat_id)

                            typing_task.cancel()

                            if result['success']:
                                response = result['text'] or "Done."
                                if placeholder.get("message_id"):
                                    await self.send_full(placeholder["message_id"], response)
                            else:
                                if placeholder.get("message_id"):
                                    await self.edit_message(placeholder["message_id"], f"Error: {result.get('error', 'Unknown error')}")
                        else:
                            typing_task.cancel()
                            if placeholder.get("message_id"):
                                await self.edit_message(placeholder["message_id"], f"You said: {transcription}\n\n(Processing system not available)")

                        # Update history
                        history = self.conversation_history[chat_id]
                        history.append({"role": "user", "content": f"[Voice memo]: {transcription}"})
                        history.append({"role": "assistant", "content": result.get('text', '') if tui_bridge else transcription})

                    except ImportError:
                        typing_task.cancel()
                        if placeholder.get("message_id"):
                            await self.edit_message(placeholder["message_id"], "Voice transcription not available. Please install: pip install openai")
                        return

                except Exception as e:
                    typing_task.cancel()
                    logger.error(f"Voice processing error: {e}")
                    if placeholder.get("message_id"):
                        await self.edit_message(placeholder["message_id"], f"Voice processing failed: {str(e)[:100]}")
                return

            # Handle stickers
            if "sticker" in message:
                await self.send_message("Sticker received. How can I help?")
                return

            if not text:
                return

            logger.info(f"Processing: {text[:50]}...")

            # Detect and fetch URLs in the message
            url_context = await self._extract_url_content(text)
            if url_context:
                text = f"{text}\n\n[URL CONTENT FETCHED]\n{url_context}"

            # Handle commands
            if text.startswith("/"):
                cmd_response = await self.handle_command(text)
                if cmd_response:
                    await self.send_message(cmd_response)
                    return

            # Start typing and send placeholder
            typing_task = asyncio.create_task(self.maintain_typing())
            placeholder = await self.send_message("Working on it...")
            placeholder_id = placeholder.get("message_id")

            try:
                # Use TUI Bridge for full tool execution
                tui_bridge = self._load_tui_bridge()
                if not tui_bridge:
                    typing_task.cancel()
                    if placeholder_id:
                        await self.edit_message(placeholder_id, "System initializing...")
                    return

                # Execute with full tool access
                result = await tui_bridge.execute(text, chat_id)

                typing_task.cancel()

                if result['success']:
                    response = result['text'] or "Done."

                    # Log tools used for debugging
                    if result.get('tools_used'):
                        tool_names = [t['name'] for t in result['tools_used']]
                        logger.info(f"Tools used: {tool_names}")

                    # Handle screenshots
                    for screenshot_path in result.get('screenshots', []):
                        await self.send_photo(screenshot_path)

                    # Deliver files the assistant queued for this conversation (docs, decks, images)
                    for item in result.get('files', []):
                        await self.send_file(item.get('path', ''), item.get('caption') or None)

                else:
                    response = f"Error: {result.get('error', 'Unknown error')}"

                # Final response - deliver IN FULL, chunked across Telegram's 4096-char limit
                # (first chunk replaces the "thinking" placeholder, the rest follow as new messages).
                if placeholder_id:
                    chunks = self._split_for_telegram(response)
                    await self.edit_message(placeholder_id, chunks[0] if chunks else response)
                    for extra in chunks[1:]:
                        await self.send_message(extra)

                logger.info(f"Response sent ({len(response)} chars): {response[:50]}...")

                # Record exchange in cross-channel memory
                if MEMORY_AVAILABLE:
                    try:
                        memory = get_memory_bus()
                        memory.record_exchange(
                            channel="telegram",
                            user_message=text,
                            assistant_message=response,
                            metadata={"chat_id": chat_id}
                        )
                    except Exception as e:
                        logger.debug(f"Memory record error: {e}")

            except Exception as e:
                typing_task.cancel()
                logger.error(f"Response error: {e}")
                if placeholder_id:
                    await self.edit_message(placeholder_id, f"Error: {str(e)[:100]}")

        except Exception as e:
            logger.error(f"Handle message error: {e}")

    async def poll_updates(self):
        """Poll for updates from Telegram."""
        consecutive_errors = 0

        while self.running:
            try:
                params = {
                    "offset": self.offset,
                    "timeout": 30,
                    "limit": 10,
                    "allowed_updates": ["message"]
                }

                response = await self.client.get(
                    f"{API_BASE}/getUpdates",
                    params=params,
                    timeout=35.0
                )

                if response.status_code == 409:
                    # Conflict - another instance or webhook
                    logger.error("409 Conflict - killing webhook and retrying")
                    await self.client.post(f"{API_BASE}/deleteWebhook")
                    await asyncio.sleep(2)
                    continue

                if response.status_code != 200:
                    logger.error(f"Poll error: {response.status_code}")
                    consecutive_errors += 1
                    await asyncio.sleep(min(consecutive_errors * 2, 30))
                    continue

                data = response.json()

                if not data.get("ok"):
                    logger.error(f"API error: {data}")
                    consecutive_errors += 1
                    await asyncio.sleep(min(consecutive_errors * 2, 30))
                    continue

                consecutive_errors = 0
                updates = data.get("result", [])

                for update in updates:
                    self.offset = max(self.offset, update.get("update_id", 0) + 1)

                    if "message" in update:
                        await self.handle_message(update)

            except httpx.ReadTimeout:
                # Normal for long polling
                pass
            except Exception as e:
                logger.error(f"Polling error: {e}")
                consecutive_errors += 1
                await asyncio.sleep(min(consecutive_errors * 2, 30))

    async def run(self):
        """Main run loop."""
        # Acquire lock
        if not self.lock.acquire():
            logger.error("Could not acquire lock. Another instance running?")
            return

        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)

        logger.info("HyperClaw Telegram bot starting...")

        try:
            # Delete any existing webhook
            await self.client.post(f"{API_BASE}/deleteWebhook")

            # Test connection
            response = await self.client.get(f"{API_BASE}/getMe")
            if response.status_code == 200:
                bot_info = response.json().get("result", {})
                logger.info(f"Connected: @{bot_info.get('username')}")
            else:
                logger.error("Failed to connect to Telegram API")
                return

            # Pre-load TUI Bridge for full tool access
            self._load_tui_bridge()

            # Start polling
            await self.poll_updates()

        except Exception as e:
            logger.error(f"Fatal error: {e}")
        finally:
            pass  # TUI Bridge handles its own session cleanup

            await self.client.aclose()
            self.lock.release()
            logger.info("Bot shutdown complete")


if __name__ == "__main__":
    bot = GilTelegramBot()
    asyncio.run(bot.run())
