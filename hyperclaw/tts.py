"""
HyperClaw TTS — ElevenLabs George Voice Integration
Voice: George — configure GEORGE_VOICE_ID env var — British uptempo, the user approved
"""

import os
import base64
import logging
import httpx
from typing import Optional

logger = logging.getLogger("hyperclaw.tts")

# ElevenLabs Configuration
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
GEORGE_VOICE_ID = os.environ.get("ELEVENLABS_GEORGE_VOICE_ID", "")
ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1"

# Voice settings optimized for George
VOICE_SETTINGS = {
    "stability": 0.35,
    "similarity_boost": 0.75,
    "style": 0.65,
    "use_speaker_boost": True
}


async def text_to_speech(text: str, voice_id: str = GEORGE_VOICE_ID) -> Optional[bytes]:
    """
    Convert text to speech using ElevenLabs.
    Returns raw audio bytes (mp3).
    """
    if not ELEVENLABS_API_KEY:
        logger.error("ElevenLabs API key not configured")
        return None

    # Clean text for TTS (no ellipses, no markdown)
    clean_text = text.replace("...", ".").replace("*", "").replace("#", "").strip()

    if not clean_text:
        return None

    url = f"{ELEVENLABS_API_URL}/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "text": clean_text,
        "model_id": "eleven_turbo_v2_5",
        "voice_settings": VOICE_SETTINGS,
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, headers=headers, timeout=30.0)
            if resp.status_code == 200:
                return resp.content
            else:
                logger.error(f"ElevenLabs error: {resp.status_code} - {resp.text[:200]}")
                return None
    except Exception as e:
        logger.error(f"TTS error: {e}")
        return None


async def text_to_speech_base64(text: str, voice_id: str = GEORGE_VOICE_ID) -> Optional[str]:
    """
    Convert text to speech and return as base64-encoded audio.
    """
    audio = await text_to_speech(text, voice_id)
    if audio:
        return base64.b64encode(audio).decode("utf-8")
    return None


async def get_voices() -> list[dict]:
    """List available ElevenLabs voices."""
    if not ELEVENLABS_API_KEY:
        return []

    url = f"{ELEVENLABS_API_URL}/voices"
    headers = {"xi-api-key": ELEVENLABS_API_KEY}

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                return [
                    {
                        "voice_id": v["voice_id"],
                        "name": v["name"],
                        "category": v.get("category", "unknown"),
                    }
                    for v in data.get("voices", [])
                ]
    except Exception as e:
        logger.error(f"Get voices error: {e}")
    return []


async def get_subscription_info() -> dict:
    """Get ElevenLabs subscription/usage info."""
    if not ELEVENLABS_API_KEY:
        return {"error": "API key not configured"}

    url = f"{ELEVENLABS_API_URL}/user/subscription"
    headers = {"xi-api-key": ELEVENLABS_API_KEY}

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "tier": data.get("tier", "unknown"),
                    "character_count": data.get("character_count", 0),
                    "character_limit": data.get("character_limit", 0),
                    "remaining": data.get("character_limit", 0) - data.get("character_count", 0),
                }
    except Exception as e:
        logger.error(f"Subscription info error: {e}")
    return {"error": "Failed to fetch subscription info"}
