"""
Voice Provider Abstraction Layer.
Unified interface for STT (Speech-to-Text) and TTS (Text-to-Speech) providers.
"""
from __future__ import annotations

import os
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

import httpx

logger = logging.getLogger(__name__)


@dataclass
class TranscriptionResult:
    """Speech-to-text result."""
    text: str
    language: str | None = None
    duration: float | None = None
    words: list[dict] | None = None
    raw: dict | None = None


@dataclass
class SpeechResult:
    """Text-to-speech result."""
    audio: bytes
    format: str = "mp3"
    duration: float | None = None


class VoiceProvider(ABC):
    """Base class for voice providers."""
    name: str = "base"


class STTProvider(VoiceProvider):
    """Abstract base for Speech-to-Text providers."""

    @abstractmethod
    async def transcribe(
        self,
        audio: bytes | Path,
        language: str | None = None,
        **kwargs,
    ) -> TranscriptionResult:
        """Transcribe audio to text."""
        pass


class TTSProvider(VoiceProvider):
    """Abstract base for Text-to-Speech providers."""

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        **kwargs,
    ) -> SpeechResult:
        """Synthesize text to speech."""
        pass

    async def stream(
        self,
        text: str,
        voice: str | None = None,
        **kwargs,
    ) -> AsyncIterator[bytes]:
        """Stream synthesized audio. Default: yield full audio."""
        result = await self.synthesize(text, voice, **kwargs)
        yield result.audio


# ═══════════════════════════════════════════════════════════════════════════════
# OPENAI WHISPER (STT)
# ═══════════════════════════════════════════════════════════════════════════════

class WhisperProvider(STTProvider):
    """OpenAI Whisper speech-to-text."""

    name = "whisper"

    MODELS = ["whisper-1"]

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.base_url = "https://api.openai.com/v1"

    async def transcribe(
        self,
        audio: bytes | Path,
        language: str | None = None,
        model: str = "whisper-1",
        **kwargs,
    ) -> TranscriptionResult:
        if isinstance(audio, Path):
            audio = audio.read_bytes()

        async with httpx.AsyncClient() as client:
            files = {"file": ("audio.mp3", audio, "audio/mpeg")}
            data = {"model": model}
            if language:
                data["language"] = language

            response = await client.post(
                f"{self.base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files=files,
                data=data,
                timeout=120.0,
            )
            response.raise_for_status()
            result = response.json()

            return TranscriptionResult(
                text=result["text"],
                language=result.get("language"),
                duration=result.get("duration"),
                raw=result,
            )


# ═══════════════════════════════════════════════════════════════════════════════
# DEEPGRAM (STT)
# ═══════════════════════════════════════════════════════════════════════════════

class DeepgramProvider(STTProvider):
    """Deepgram speech-to-text."""

    name = "deepgram"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("DEEPGRAM_API_KEY")
        self.base_url = "https://api.deepgram.com/v1"

    async def transcribe(
        self,
        audio: bytes | Path,
        language: str | None = None,
        model: str = "nova-2",
        **kwargs,
    ) -> TranscriptionResult:
        if isinstance(audio, Path):
            audio = audio.read_bytes()

        params = {"model": model, "smart_format": "true"}
        if language:
            params["language"] = language

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/listen",
                headers={
                    "Authorization": f"Token {self.api_key}",
                    "Content-Type": "audio/mpeg",
                },
                params=params,
                content=audio,
                timeout=120.0,
            )
            response.raise_for_status()
            result = response.json()

            transcript = result["results"]["channels"][0]["alternatives"][0]

            return TranscriptionResult(
                text=transcript["transcript"],
                language=result["results"].get("detected_language"),
                duration=result["metadata"].get("duration"),
                words=transcript.get("words"),
                raw=result,
            )


# ═══════════════════════════════════════════════════════════════════════════════
# ASSEMBLYAI (STT)
# ═══════════════════════════════════════════════════════════════════════════════

class AssemblyAIProvider(STTProvider):
    """AssemblyAI speech-to-text."""

    name = "assemblyai"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("ASSEMBLYAI_API_KEY")
        self.base_url = "https://api.assemblyai.com/v2"

    async def transcribe(
        self,
        audio: bytes | Path,
        language: str | None = None,
        **kwargs,
    ) -> TranscriptionResult:
        if isinstance(audio, Path):
            audio = audio.read_bytes()

        async with httpx.AsyncClient() as client:
            # Upload audio
            upload_response = await client.post(
                f"{self.base_url}/upload",
                headers={"Authorization": self.api_key},
                content=audio,
                timeout=120.0,
            )
            upload_response.raise_for_status()
            audio_url = upload_response.json()["upload_url"]

            # Start transcription
            transcript_request = {"audio_url": audio_url}
            if language:
                transcript_request["language_code"] = language

            transcript_response = await client.post(
                f"{self.base_url}/transcript",
                headers={"Authorization": self.api_key},
                json=transcript_request,
                timeout=30.0,
            )
            transcript_response.raise_for_status()
            transcript_id = transcript_response.json()["id"]

            # Poll for completion
            import asyncio
            while True:
                status_response = await client.get(
                    f"{self.base_url}/transcript/{transcript_id}",
                    headers={"Authorization": self.api_key},
                    timeout=30.0,
                )
                status_response.raise_for_status()
                result = status_response.json()

                if result["status"] == "completed":
                    return TranscriptionResult(
                        text=result["text"],
                        language=result.get("language_code"),
                        duration=result.get("audio_duration"),
                        words=result.get("words"),
                        raw=result,
                    )
                elif result["status"] == "error":
                    raise RuntimeError(f"Transcription failed: {result.get('error')}")

                await asyncio.sleep(1)


# ═══════════════════════════════════════════════════════════════════════════════
# ELEVENLABS (TTS)
# ═══════════════════════════════════════════════════════════════════════════════

class ElevenLabsProvider(TTSProvider):
    """ElevenLabs text-to-speech."""

    name = "elevenlabs"

    VOICES = {
        "rachel": "21m00Tcm4TlvDq8ikWAM",
        "domi": "AZnzlk1XvdvUeBnXmlld",
        "bella": "EXAVITQu4vr4xnSDxMaL",
        "antoni": "ErXwobaYiN019PkySvjV",
        "elli": "MF3mGyEYCl7XYWbV9V6O",
        "josh": "TxGEqnHWrfWFTfGW9XjX",
        "arnold": "VR6AewLTigWG4xSOukaG",
        "adam": "pNInz6obpgDQGcFmaJgB",
        "sam": "yoZ06aMxZJJ28mfd3POQ",
        "george": "JBFqnCBsd6RMkjVDRZzb",
    }

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("ELEVENLABS_API_KEY")
        self.base_url = "https://api.elevenlabs.io/v1"

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        model: str = "eleven_turbo_v2_5",
        **kwargs,
    ) -> SpeechResult:
        voice_id = self.VOICES.get(voice, voice) if voice else self.VOICES["george"]

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/text-to-speech/{voice_id}",
                headers={
                    "xi-api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "text": text,
                    "model_id": model,
                    "voice_settings": {
                        "stability": 0.35,
                        "similarity_boost": 0.75,
                        "style": 0.65,
                        "use_speaker_boost": True,
                    },
                },
                timeout=60.0,
            )
            response.raise_for_status()

            return SpeechResult(audio=response.content, format="mp3")

    async def stream(
        self,
        text: str,
        voice: str | None = None,
        model: str = "eleven_turbo_v2_5",
        **kwargs,
    ) -> AsyncIterator[bytes]:
        voice_id = self.VOICES.get(voice, voice) if voice else self.VOICES["george"]

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/text-to-speech/{voice_id}/stream",
                headers={
                    "xi-api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "text": text,
                    "model_id": model,
                    "voice_settings": {
                        "stability": 0.35,
                        "similarity_boost": 0.75,
                        "style": 0.65,
                        "use_speaker_boost": True,
                    },
                },
                timeout=60.0,
            ) as response:
                async for chunk in response.aiter_bytes():
                    yield chunk


# ═══════════════════════════════════════════════════════════════════════════════
# GOOGLE TTS
# ═══════════════════════════════════════════════════════════════════════════════

class GoogleTTSProvider(TTSProvider):
    """Google Cloud Text-to-Speech."""

    name = "google-tts"

    VOICES = {
        "en-US-Standard-A": {"languageCode": "en-US", "name": "en-US-Standard-A"},
        "en-US-Standard-B": {"languageCode": "en-US", "name": "en-US-Standard-B"},
        "en-US-Wavenet-A": {"languageCode": "en-US", "name": "en-US-Wavenet-A"},
        "en-US-Neural2-A": {"languageCode": "en-US", "name": "en-US-Neural2-A"},
        "en-GB-Standard-A": {"languageCode": "en-GB", "name": "en-GB-Standard-A"},
        "en-GB-Wavenet-A": {"languageCode": "en-GB", "name": "en-GB-Wavenet-A"},
    }

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY")
        self.base_url = "https://texttospeech.googleapis.com/v1"

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        **kwargs,
    ) -> SpeechResult:
        voice_config = self.VOICES.get(voice, self.VOICES["en-US-Wavenet-A"])

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/text:synthesize",
                params={"key": self.api_key},
                json={
                    "input": {"text": text},
                    "voice": voice_config,
                    "audioConfig": {"audioEncoding": "MP3"},
                },
                timeout=60.0,
            )
            response.raise_for_status()
            result = response.json()

            import base64
            audio = base64.b64decode(result["audioContent"])

            return SpeechResult(audio=audio, format="mp3")


# ═══════════════════════════════════════════════════════════════════════════════
# OPENAI TTS
# ═══════════════════════════════════════════════════════════════════════════════

class OpenAITTSProvider(TTSProvider):
    """OpenAI Text-to-Speech."""

    name = "openai-tts"

    VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.base_url = "https://api.openai.com/v1"

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        model: str = "tts-1",
        **kwargs,
    ) -> SpeechResult:
        voice = voice if voice in self.VOICES else "alloy"

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/audio/speech",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model,
                    "input": text,
                    "voice": voice,
                    "response_format": "mp3",
                },
                timeout=60.0,
            )
            response.raise_for_status()

            return SpeechResult(audio=response.content, format="mp3")


# ═══════════════════════════════════════════════════════════════════════════════
# PROVIDER FACTORY
# ═══════════════════════════════════════════════════════════════════════════════

STT_PROVIDERS: dict[str, type[STTProvider]] = {
    "whisper": WhisperProvider,
    "openai": WhisperProvider,
    "deepgram": DeepgramProvider,
    "assemblyai": AssemblyAIProvider,
}

TTS_PROVIDERS: dict[str, type[TTSProvider]] = {
    "elevenlabs": ElevenLabsProvider,
    "google": GoogleTTSProvider,
    "openai": OpenAITTSProvider,
}


def get_stt_provider(name: str | None = None, **kwargs) -> STTProvider:
    """Get a speech-to-text provider by name."""
    name = name or os.environ.get("HYPERCLAW_STT_PROVIDER", "whisper")
    name = name.lower()

    if name not in STT_PROVIDERS:
        available = ", ".join(sorted(STT_PROVIDERS.keys()))
        raise ValueError(f"Unknown STT provider: {name}. Available: {available}")

    return STT_PROVIDERS[name](**kwargs)


def get_tts_provider(name: str | None = None, **kwargs) -> TTSProvider:
    """Get a text-to-speech provider by name."""
    name = name or os.environ.get("HYPERCLAW_TTS_PROVIDER", "elevenlabs")
    name = name.lower()

    if name not in TTS_PROVIDERS:
        available = ", ".join(sorted(TTS_PROVIDERS.keys()))
        raise ValueError(f"Unknown TTS provider: {name}. Available: {available}")

    return TTS_PROVIDERS[name](**kwargs)
