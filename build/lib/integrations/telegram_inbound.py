#!/usr/bin/env python3
"""
Assistant Telegram Inbound Handler
Receives messages, images, voice from the user → routes to Assistant
"""

import os
import asyncio
import logging
import json
import httpx
from pathlib import Path
from datetime import datetime
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Assistant.Telegram.Inbound")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_CHAT_IDS = [int(x) for x in os.environ.get("TELEGRAM_ALLOWED_IDS", "0").split(",") if x]
HYPERCLAW_API = "http://localhost:8001"
INBOX_DIR = Path(str(Path.home() / ".hyperclaw/workspace/telegram_inbox"))
INBOX_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)

def save_message(data: dict):
    """Save incoming message to inbox for Assistant processing"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    msg_file = INBOX_DIR / f"msg_{timestamp}.json"
    with open(msg_file, "w") as f:
        json.dump(data, f, indent=2)
    logger.info(f"Saved message: {msg_file}")
    return msg_file

async def download_file(file_id: str, filename: str):
    """Download file (image/voice) from Telegram"""
    async with httpx.AsyncClient() as client:
        # Get file path
        r = await client.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}")
        file_path = r.json()["result"]["file_path"]
        
        # Download file
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        r = await client.get(file_url)
        
        save_path = INBOX_DIR / filename
        with open(save_path, "wb") as f:
            f.write(r.content)
        logger.info(f"Downloaded file: {save_path}")
        return str(save_path)

def send_telegram(chat_id: int, text: str):
    """Send reply back to the user"""
    import requests
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})

@app.route("/telegram/webhook", methods=["POST"])
def webhook():
    data = request.json
    logger.info(f"Incoming: {json.dumps(data, indent=2)[:500]}")
    
    message = data.get("message") or data.get("edited_message")
    if not message:
        return jsonify({"ok": True})
    
    chat_id = message.get("chat", {}).get("id")
    
    # Security — only the user
    if chat_id not in ALLOWED_CHAT_IDS:
        logger.warning(f"Blocked unauthorized chat_id: {chat_id}")
        return jsonify({"ok": True})
    
    timestamp = datetime.now().isoformat()
    
    # Text message
    if "text" in message:
        text = message["text"]
        logger.info(f"Text from the user: {text}")
        
        payload = {
            "type": "text",
            "from": "the user",
            "chat_id": chat_id,
            "text": text,
            "timestamp": timestamp
        }
        save_message(payload)
        
        # Auto-acknowledge
        send_telegram(chat_id, f"⚡ Received. Processing now.")
        
    # Photo/image
    elif "photo" in message:
        photos = message["photo"]
        largest = max(photos, key=lambda p: p.get("file_size", 0))
        file_id = largest["file_id"]
        caption = message.get("caption", "")
        
        timestamp_clean = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"photo_{timestamp_clean}.jpg"
        
        # Download async
        loop = asyncio.new_event_loop()
        file_path = loop.run_until_complete(download_file(file_id, filename))
        loop.close()
        
        payload = {
            "type": "photo",
            "from": "the user", 
            "chat_id": chat_id,
            "file_path": file_path,
            "caption": caption,
            "timestamp": timestamp
        }
        save_message(payload)
        send_telegram(chat_id, f"⚡ Image received{' — ' + caption if caption else ''}. On it.")
        
    # Voice message
    elif "voice" in message:
        file_id = message["voice"]["file_id"]
        timestamp_clean = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"voice_{timestamp_clean}.ogg"
        
        loop = asyncio.new_event_loop()
        file_path = loop.run_until_complete(download_file(file_id, filename))
        loop.close()
        
        payload = {
            "type": "voice",
            "from": "the user",
            "chat_id": chat_id, 
            "file_path": file_path,
            "timestamp": timestamp
        }
        save_message(payload)
        send_telegram(chat_id, f"⚡ Voice message received. Transcribing now.")
        
    # Document/file
    elif "document" in message:
        doc = message["document"]
        file_id = doc["file_id"]
        filename = doc.get("file_name", f"doc_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        
        loop = asyncio.new_event_loop()
        file_path = loop.run_until_complete(download_file(file_id, filename))
        loop.close()
        
        payload = {
            "type": "document",
            "from": "the user",
            "chat_id": chat_id,
            "file_path": file_path,
            "filename": filename,
            "timestamp": timestamp
        }
        save_message(payload)
        send_telegram(chat_id, f"⚡ File received: {filename}. Reviewing now.")
    
    return jsonify({"ok": True})

@app.route("/telegram/inbox", methods=["GET"])
def get_inbox():
    """Get all pending messages from the user"""
    messages = []
    for f in sorted(INBOX_DIR.glob("*.json")):
        with open(f) as fp:
            messages.append(json.load(fp))
    return jsonify({"count": len(messages), "messages": messages})

@app.route("/telegram/health", methods=["GET"])
def health():
    return jsonify({"status": "online", "service": "Assistant Telegram Inbound"})

if __name__ == "__main__":
    logger.info("⚡ Assistant Telegram Inbound — ONLINE on port 8766")
    app.run(host="0.0.0.0", port=8766, debug=False)
