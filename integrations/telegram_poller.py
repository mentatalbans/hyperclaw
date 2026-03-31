#!/usr/bin/env python3
"""
Assistant Telegram Poller — Long polling (no webhook/public URL needed)
Continuously polls Telegram for new messages from the user
Saves to inbox, auto-acknowledges
"""

import os
import json
import time
import logging
import asyncio
import requests
from pathlib import Path
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [Assistant.Telegram] %(message)s',
    handlers=[
        logging.FileHandler('/tmp/telegram_poller.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("Assistant.Telegram.Poller")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED_CHAT_IDS = [int(x) for x in os.environ.get("TELEGRAM_ALLOWED_IDS", "0").split(",") if x]
INBOX_DIR = Path(str(Path.home() / ".hyperclaw/workspace/telegram_inbox"))
INBOX_DIR.mkdir(parents=True, exist_ok=True)
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

def send(chat_id: int, text: str):
    try:
        requests.post(f"{BASE_URL}/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }, timeout=10)
    except Exception as e:
        logger.error(f"Send failed: {e}")

def download_file(file_id: str, filename: str) -> str:
    try:
        r = requests.get(f"{BASE_URL}/getFile?file_id={file_id}", timeout=10)
        file_path = r.json()["result"]["file_path"]
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        content = requests.get(file_url, timeout=30).content
        save_path = INBOX_DIR / filename
        with open(save_path, "wb") as f:
            f.write(content)
        logger.info(f"Downloaded: {save_path} ({len(content)} bytes)")
        return str(save_path)
    except Exception as e:
        logger.error(f"Download failed: {e}")
        return ""

def save_message(data: dict):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    msg_file = INBOX_DIR / f"msg_{timestamp}.json"
    with open(msg_file, "w") as f:
        json.dump(data, f, indent=2)
    return str(msg_file)

def process_update(update: dict):
    message = update.get("message") or update.get("edited_message")
    if not message:
        return
    
    chat_id = message.get("chat", {}).get("id")
    if chat_id not in ALLOWED_CHAT_IDS:
        logger.warning(f"Ignored unauthorized chat_id: {chat_id}")
        return
    
    timestamp = datetime.now().isoformat()
    ts_clean = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    if "text" in message:
        text = message["text"]
        logger.info(f"📩 the user: {text}")
        save_message({"type": "text", "from": "the user", "chat_id": chat_id, "text": text, "timestamp": timestamp})
        send(chat_id, f"⚡ Received.")

    elif "photo" in message:
        photos = message["photo"]
        largest = max(photos, key=lambda p: p.get("file_size", 0))
        file_id = largest["file_id"]
        caption = message.get("caption", "")
        filepath = download_file(file_id, f"photo_{ts_clean}.jpg")
        save_message({"type": "photo", "from": "the user", "chat_id": chat_id, "file_path": filepath, "caption": caption, "timestamp": timestamp})
        logger.info(f"📸 Photo received — {caption}")
        send(chat_id, f"⚡ Image received{' — ' + caption if caption else ''}. Got it.")

    elif "voice" in message:
        file_id = message["voice"]["file_id"]
        filepath = download_file(file_id, f"voice_{ts_clean}.ogg")
        save_message({"type": "voice", "from": "the user", "chat_id": chat_id, "file_path": filepath, "timestamp": timestamp})
        logger.info(f"🎙️ Voice message received")
        send(chat_id, f"⚡ Voice received. Transcribing now.")

    elif "document" in message:
        doc = message["document"]
        filename = doc.get("file_name", f"doc_{ts_clean}")
        filepath = download_file(doc["file_id"], filename)
        save_message({"type": "document", "from": "the user", "chat_id": chat_id, "file_path": filepath, "filename": filename, "timestamp": timestamp})
        logger.info(f"📎 Document received: {filename}")
        send(chat_id, f"⚡ File received: `{filename}`. On it.")

def poll():
    logger.info("⚡ Assistant Telegram Poller — ONLINE. Listening for the user...")
    offset = None
    
    while True:
        try:
            params = {"timeout": 30, "allowed_updates": ["message", "edited_message"]}
            if offset:
                params["offset"] = offset
            
            r = requests.get(f"{BASE_URL}/getUpdates", params=params, timeout=35)
            updates = r.json().get("result", [])
            
            for update in updates:
                process_update(update)
                offset = update["update_id"] + 1
                
        except requests.exceptions.Timeout:
            continue  # Normal for long polling
        except Exception as e:
            logger.error(f"Poll error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    poll()
