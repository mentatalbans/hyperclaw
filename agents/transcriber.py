#!/usr/bin/env python3
"""
Assistant Audio/Video Transcription — Transcribe calls, meetings, videos.
Uses OpenAI Whisper API or local whisper.cpp.
"""

import os
import sys
import json
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List
import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

HYPERCLAW_ROOT = Path(__file__).parent.parent
TRANSCRIPTS_DIR = HYPERCLAW_ROOT / "workspace" / "transcripts"
TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)


def load_env():
    """Load environment from .env file."""
    env_file = HYPERCLAW_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip().strip('"'))


load_env()


def get_openai_key():
    return os.environ.get("OPENAI_API_KEY", "")


def transcribe_audio(
    audio_path: str,
    language: str = None,
    prompt: str = None,
    output_format: str = "text"
) -> Dict:
    """Transcribe audio file using OpenAI Whisper API."""
    api_key = get_openai_key()
    if not api_key:
        return {"error": "OPENAI_API_KEY not set"}

    path = Path(audio_path).expanduser()
    if not path.exists():
        return {"error": f"File not found: {audio_path}"}

    # Check file size (max 25MB for Whisper API)
    file_size = path.stat().st_size
    if file_size > 25 * 1024 * 1024:
        return {"error": f"File too large ({file_size / 1024 / 1024:.1f}MB). Max 25MB. Use split_audio first."}

    try:
        with open(path, "rb") as f:
            files = {"file": (path.name, f, "audio/mpeg")}
            data = {"model": "whisper-1"}

            if language:
                data["language"] = language
            if prompt:
                data["prompt"] = prompt
            if output_format in ["json", "verbose_json", "srt", "vtt"]:
                data["response_format"] = output_format

            with httpx.Client(timeout=300) as client:  # 5 min timeout for long files
                resp = client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files=files,
                    data=data
                )

                if resp.status_code == 200:
                    if output_format in ["json", "verbose_json"]:
                        result = resp.json()
                    else:
                        result = {"text": resp.text}

                    # Save transcript
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    transcript_path = TRANSCRIPTS_DIR / f"{path.stem}_{timestamp}.txt"
                    transcript_path.write_text(result.get("text", str(result)))

                    result["saved_to"] = str(transcript_path)
                    return result

                return {"error": f"API error: {resp.status_code} - {resp.text}"}

    except Exception as e:
        return {"error": str(e)}


def transcribe_video(video_path: str, **kwargs) -> Dict:
    """Transcribe video by extracting audio first."""
    path = Path(video_path).expanduser()
    if not path.exists():
        return {"error": f"File not found: {video_path}"}

    # Extract audio using ffmpeg
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp_path = tmp.name

        result = subprocess.run([
            "ffmpeg", "-i", str(path),
            "-vn", "-acodec", "libmp3lame", "-q:a", "4",
            "-y", tmp_path
        ], capture_output=True, text=True, timeout=300)

        if result.returncode != 0:
            return {"error": f"ffmpeg failed: {result.stderr}"}

        # Transcribe the extracted audio
        transcript = transcribe_audio(tmp_path, **kwargs)

        # Clean up
        Path(tmp_path).unlink()

        return transcript

    except FileNotFoundError:
        return {"error": "ffmpeg not installed. Install with: brew install ffmpeg"}
    except Exception as e:
        return {"error": str(e)}


def record_audio(duration: int = 30, output_path: str = None) -> Dict:
    """Record audio from microphone."""
    if not output_path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(TRANSCRIPTS_DIR / f"recording_{timestamp}.wav")

    try:
        # Use macOS sox or ffmpeg to record
        result = subprocess.run([
            "sox", "-d", "-r", "16000", "-c", "1", output_path,
            "trim", "0", str(duration)
        ], capture_output=True, text=True, timeout=duration + 10)

        if result.returncode == 0:
            return {"recorded": output_path, "duration": duration}

        # Fallback to ffmpeg
        result = subprocess.run([
            "ffmpeg", "-f", "avfoundation", "-i", ":0",
            "-t", str(duration), "-y", output_path
        ], capture_output=True, text=True, timeout=duration + 10)

        if result.returncode == 0:
            return {"recorded": output_path, "duration": duration}

        return {"error": "Recording failed. Install sox: brew install sox"}

    except Exception as e:
        return {"error": str(e)}


def record_and_transcribe(duration: int = 30) -> Dict:
    """Record audio and transcribe it."""
    # Record
    record_result = record_audio(duration)
    if "error" in record_result:
        return record_result

    # Transcribe
    transcript = transcribe_audio(record_result["recorded"])

    return {
        "recorded": record_result["recorded"],
        "duration": duration,
        "transcript": transcript.get("text", ""),
        "saved_to": transcript.get("saved_to")
    }


def split_audio(audio_path: str, chunk_minutes: int = 10) -> List[str]:
    """Split large audio file into chunks for transcription."""
    path = Path(audio_path).expanduser()
    if not path.exists():
        return []

    try:
        chunks = []
        chunk_dir = TRANSCRIPTS_DIR / f"chunks_{path.stem}"
        chunk_dir.mkdir(exist_ok=True)

        # Get duration
        result = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path)
        ], capture_output=True, text=True)

        duration = float(result.stdout.strip())
        chunk_seconds = chunk_minutes * 60

        for i, start in enumerate(range(0, int(duration), chunk_seconds)):
            chunk_path = chunk_dir / f"chunk_{i:03d}.mp3"
            subprocess.run([
                "ffmpeg", "-i", str(path),
                "-ss", str(start), "-t", str(chunk_seconds),
                "-acodec", "libmp3lame", "-q:a", "4",
                "-y", str(chunk_path)
            ], capture_output=True, timeout=120)

            if chunk_path.exists():
                chunks.append(str(chunk_path))

        return chunks

    except Exception as e:
        return []


def transcribe_long_audio(audio_path: str, chunk_minutes: int = 10) -> Dict:
    """Transcribe long audio by splitting into chunks."""
    chunks = split_audio(audio_path, chunk_minutes)
    if not chunks:
        return {"error": "Failed to split audio"}

    full_transcript = []
    for i, chunk in enumerate(chunks):
        print(f"Transcribing chunk {i+1}/{len(chunks)}...")
        result = transcribe_audio(chunk)
        if "text" in result:
            full_transcript.append(result["text"])

    # Combine and save
    combined_text = "\n\n".join(full_transcript)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = TRANSCRIPTS_DIR / f"{Path(audio_path).stem}_full_{timestamp}.txt"
    output_path.write_text(combined_text)

    return {
        "text": combined_text,
        "chunks": len(chunks),
        "saved_to": str(output_path)
    }


def list_transcripts() -> List[Dict]:
    """List all saved transcripts."""
    transcripts = []
    for path in TRANSCRIPTS_DIR.glob("*.txt"):
        transcripts.append({
            "name": path.name,
            "path": str(path),
            "size": path.stat().st_size,
            "created": datetime.fromtimestamp(path.stat().st_ctime).isoformat()
        })
    return sorted(transcripts, key=lambda x: x["created"], reverse=True)


def get_transcript(name: str) -> str:
    """Get a transcript by name."""
    path = TRANSCRIPTS_DIR / name
    if path.exists():
        return path.read_text()

    # Try partial match
    for p in TRANSCRIPTS_DIR.glob(f"*{name}*.txt"):
        return p.read_text()

    return "Transcript not found"


def summarize_transcript(transcript_text: str) -> str:
    """Summarize a transcript using Claude (placeholder)."""
    # This would call Claude API to summarize
    # For now, return word count
    words = len(transcript_text.split())
    return f"Transcript has {words} words. Full summarization requires Claude API call."


# CLI functions for TUI integration
def audio_transcribe(path: str, language: str = None) -> Dict:
    """Transcribe an audio file."""
    return transcribe_audio(path, language=language)


def video_transcribe(path: str, language: str = None) -> Dict:
    """Transcribe a video file."""
    return transcribe_video(path, language=language)


def audio_record(duration: int = 30) -> Dict:
    """Record audio from microphone."""
    return record_audio(duration)


def audio_record_transcribe(duration: int = 30) -> Dict:
    """Record and transcribe."""
    return record_and_transcribe(duration)


def transcripts_list() -> List[Dict]:
    """List all transcripts."""
    return list_transcripts()


def transcript_get(name: str) -> str:
    """Get a specific transcript."""
    return get_transcript(name)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: transcriber.py <transcribe|record|list> [args]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "transcribe":
        path = sys.argv[2] if len(sys.argv) > 2 else ""
        result = transcribe_audio(path)
        print(json.dumps(result, indent=2))

    elif cmd == "video":
        path = sys.argv[2] if len(sys.argv) > 2 else ""
        result = transcribe_video(path)
        print(json.dumps(result, indent=2))

    elif cmd == "record":
        duration = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        result = record_and_transcribe(duration)
        print(json.dumps(result, indent=2))

    elif cmd == "list":
        for t in list_transcripts():
            print(f"{t['name']} ({t['size']} bytes)")

    else:
        print(f"Unknown command: {cmd}")
