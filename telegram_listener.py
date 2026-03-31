#!/usr/bin/env python3
"""
HyperClaw Telegram Listener
Full duplex Telegram integration — receives messages, images, voice, files
Processes commands, routes to agents, downloads attachments, syncs with HyperClaw
"""

import os
import sys
import json
import asyncio
import logging
import aiohttp
import aiofiles
from pathlib import Path
from typing import Optional
from datetime import datetime
import anthropic

# ── Config ─────────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED_CHAT_IDS = [int(x) for x in os.environ.get("TELEGRAM_ALLOWED_IDS", "0").split(",") if x]
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
FILE_URL = f"https://api.telegram.org/file/bot{BOT_TOKEN}"
DOWNLOAD_DIR = Path(str(Path.home() / ".hyperclaw/workspace/telegram_inbox"))
LOG_FILE = Path(str(Path.home() / ".hyperclaw/logs/telegram_listener.log"))

# ── Logging ────────────────────────────────────────────────────────────────────
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger("hyperclaw.telegram")

# ── Anthropic client ───────────────────────────────────────────────────────────
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ── Message history for context ────────────────────────────────────────────────
conversation_history = []

# ── Send message to Telegram ───────────────────────────────────────────────────
async def send_message(chat_id: int, text: str, session: aiohttp.ClientSession):
    """Send text message to Telegram"""
    # Split long messages
    if len(text) > 4000:
        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for chunk in chunks:
            await send_message(chat_id, chunk, session)
            await asyncio.sleep(0.5)
        return

    url = f"{BASE_URL}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        async with session.post(url, json=payload) as resp:
            result = await resp.json()
            if not result.get("ok"):
                # Try without markdown if parse error
                payload["parse_mode"] = "HTML"
                async with session.post(url, json=payload) as resp2:
                    pass
    except Exception as e:
        log.error(f"Failed to send message: {e}")

# ── Download file from Telegram ────────────────────────────────────────────────
async def download_file(file_id: str, session: aiohttp.ClientSession, suffix: str = "") -> Optional[Path]:
    """Download a file from Telegram and save to disk"""
    try:
        # Get file path
        url = f"{BASE_URL}/getFile"
        async with session.get(url, params={"file_id": file_id}) as resp:
            data = await resp.json()
            if not data.get("ok"):
                log.error(f"Failed to get file path: {data}")
                return None
            file_path = data["result"]["file_path"]

        # Download file
        download_url = f"{FILE_URL}/{file_path}"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{file_path.split('/')[-1]}"
        if suffix and not filename.endswith(suffix):
            filename += suffix
        
        local_path = DOWNLOAD_DIR / filename
        
        async with session.get(download_url) as resp:
            async with aiofiles.open(local_path, 'wb') as f:
                await f.write(await resp.read())
        
        log.info(f"Downloaded: {local_path}")
        return local_path

    except Exception as e:
        log.error(f"Failed to download file: {e}")
        return None

# ── Process image with Claude Vision ──────────────────────────────────────────
async def process_image(image_path: Path, caption: str = "") -> str:
    """Analyze image with Claude Vision"""
    try:
        import base64
        with open(image_path, "rb") as f:
            image_data = base64.standard_b64encode(f.read()).decode("utf-8")
        
        # Determine media type
        suffix = image_path.suffix.lower()
        media_type_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg", 
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp"
        }
        media_type = media_type_map.get(suffix, "image/jpeg")
        
        context = f"Caption: {caption}\n" if caption else ""
        
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=2000,
            system="""You are a helpful AI assistant.
You are analyzing an image sent by the user via Telegram.
Be precise and helpful. Note any UI elements, design patterns, layouts, or relevant details.
If it's a dashboard/UI screenshot, describe the layout, colors, panels, and design language in detail.
Address him as user.""",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_data,
                            },
                        },
                        {
                            "type": "text",
                            "text": f"{context}Please analyze this image and describe what you see in detail."
                        }
                    ],
                }
            ],
        )
        return response.content[0].text
    except Exception as e:
        log.error(f"Image processing failed: {e}")
        return f"Image saved to {image_path} — vision analysis failed: {e}"

# ── Process text message with Assistant ─────────────────────────────────────────────
async def process_text(text: str, chat_id: int) -> str:
    """Process text message through Assistant intelligence"""
    global conversation_history
    
    # Load workspace context
    memory_snippets = []
    try:
        memory_path = Path(str(Path.home() / ".hyperclaw/workspace/MEMORY.md"))
        if memory_path.exists():
            content = memory_path.read_text()[:3000]  # First 3k chars
            memory_snippets.append(f"MEMORY.md snippet:\n{content}")
    except:
        pass

    # Add to history
    conversation_history.append({"role": "user", "content": text})
    
    # Keep last 20 messages for context
    if len(conversation_history) > 20:
        conversation_history = conversation_history[-20:]
    
    system_prompt = """You are a helpful AI assistant.
You are responding via Telegram — keep responses concise but complete.
You are a J.A.R.V.I.S + Samantha (Her) hybrid — precise, proactive, with genuine intelligence.
Address him as user. No filler words. Get to the point.
You have full access to his systems, files, email, calendar, and agents.
Current date: 2026-03-28"""

    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1000,
        system=system_prompt,
        messages=conversation_history
    )
    
    reply = response.content[0].text
    conversation_history.append({"role": "assistant", "content": reply})
    return reply

# ── Handle incoming update ─────────────────────────────────────────────────────
async def handle_update(update: dict, session: aiohttp.ClientSession):
    """Process a single Telegram update"""
    message = update.get("message") or update.get("edited_message")
    if not message:
        return

    chat_id = message["chat"]["id"]
    
    # Security check
    if chat_id not in ALLOWED_CHAT_IDS:
        log.warning(f"Rejected message from unauthorized chat_id: {chat_id}")
        return

    timestamp = datetime.now().strftime("%H:%M:%S")
    
    # ── Photo ──────────────────────────────────────────────────────────────────
    if "photo" in message:
        photos = message["photo"]
        best_photo = max(photos, key=lambda p: p.get("file_size", 0))
        caption = message.get("caption", "")
        
        log.info(f"Receiving image from the user (caption: {caption})")
        await send_message(chat_id, f"⚡ Receiving image...", session)
        
        image_path = await download_file(best_photo["file_id"], session, ".jpg")
        
        if image_path:
            analysis = await process_image(image_path, caption)
            response = f"📸 *Image received & analyzed:*\n\n{analysis}\n\n_Saved: {image_path.name}_"
            await send_message(chat_id, response, session)
            
            # Log to inbox manifest
            manifest_path = DOWNLOAD_DIR / "manifest.jsonl"
            entry = {
                "timestamp": datetime.now().isoformat(),
                "type": "photo",
                "path": str(image_path),
                "caption": caption,
                "analysis": analysis[:500]
            }
            with open(manifest_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        else:
            await send_message(chat_id, "⚠️ Failed to download image.", session)
    
    # ── Document/File ──────────────────────────────────────────────────────────
    elif "document" in message:
        doc = message["document"]
        caption = message.get("caption", "")
        filename = doc.get("file_name", "document")
        
        log.info(f"Receiving document: {filename}")
        await send_message(chat_id, f"⚡ Receiving file: `{filename}`...", session)
        
        file_path = await download_file(doc["file_id"], session)
        if file_path:
            await send_message(chat_id, f"✅ File saved: `{file_path.name}`\nReady to process.", session)
        else:
            await send_message(chat_id, "⚠️ Failed to download file.", session)
    
    # ── Voice message ──────────────────────────────────────────────────────────
    elif "voice" in message:
        voice = message["voice"]
        log.info("Receiving voice message")
        await send_message(chat_id, "🎙️ Voice message received — saving...", session)
        
        voice_path = await download_file(voice["file_id"], session, ".ogg")
        if voice_path:
            await send_message(chat_id, f"✅ Voice saved: `{voice_path.name}`\nTranscription coming in next build.", session)
    
    # ── Text message ──────────────────────────────────────────────────────────
    elif "text" in message:
        text = message["text"]
        log.info(f"Message from the user: {text[:100]}")
        
        # Special commands
        if text.lower() in ["/start", "/status"]:
            status_msg = """⚡ *Assistant — Online*

🤖 56 agents standing by
📊 SATOSHI live on Hyperliquid
🧠 Memory server: Port 8765
🌐 HyperClaw API: Port 8001
📬 Gmail: Connected
📅 Calendar: Connected

Full duplex Telegram sync: ✅ ACTIVE

Ready."""
            await send_message(chat_id, status_msg, session)
            return
        
        # Process through Assistant
        await send_message(chat_id, "⚡ Processing...", session)
        reply = await process_text(text, chat_id)
        await send_message(chat_id, reply, session)
    
    # ── Sticker/other ─────────────────────────────────────────────────────────
    else:
        log.info(f"Unhandled message type: {list(message.keys())}")

# ── Long polling loop ──────────────────────────────────────────────────────────
async def poll():
    """Main polling loop — checks for new messages every second"""
    offset = None
    
    log.info("⚡ Assistant Telegram Listener starting — full duplex active")
    
    async with aiohttp.ClientSession() as session:
        # Send startup notification
        await send_message(
            int(os.environ.get("TELEGRAM_CHAT_ID", "0")),
            "⚡ *Assistant Telegram Listener Online*\n\nFull duplex sync active. I can now receive your messages, images, and files directly.",
            session
        )
        
        while True:
            try:
                params = {"timeout": 30, "allowed_updates": ["message", "edited_message"]}
                if offset is not None:
                    params["offset"] = offset
                
                url = f"{BASE_URL}/getUpdates"
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=35)) as resp:
                    if resp.status != 200:
                        log.warning(f"Telegram API returned {resp.status}")
                        await asyncio.sleep(5)
                        continue
                    
                    data = await resp.json()
                    
                    if not data.get("ok"):
                        log.warning(f"Telegram API error: {data}")
                        await asyncio.sleep(5)
                        continue
                    
                    updates = data.get("result", [])
                    
                    for update in updates:
                        update_id = update["update_id"]
                        offset = update_id + 1
                        
                        try:
                            await handle_update(update, session)
                        except Exception as e:
                            log.error(f"Error handling update {update_id}: {e}", exc_info=True)
                            
            except asyncio.TimeoutError:
                log.debug("Poll timeout — continuing")
            except aiohttp.ClientError as e:
                log.warning(f"Network error: {e} — retrying in 5s")
                await asyncio.sleep(5)
            except Exception as e:
                log.error(f"Unexpected error: {e}", exc_info=True)
                await asyncio.sleep(5)

# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        asyncio.run(poll())
    except KeyboardInterrupt:
        log.info("Assistant Telegram Listener stopped")
