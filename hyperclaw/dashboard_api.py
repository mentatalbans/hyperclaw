"""Dashboard auxiliary routes; runtime and task ownership remain in the app."""

import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


@router.get("/api/memory/stats")
async def memory_stats():
    from hyperclaw.server import _orchestrator, _db_pool
    if not _orchestrator or not _orchestrator._memory:
        raise HTTPException(status_code=503, detail="Memory not initialized")
    return {"status": "active", "storage": "database+files" if _db_pool else "files",
            "active_tasks": _orchestrator._coordinator.get_status()["tasks"]["active"]}


@router.post("/api/memory/reload")
async def reload_memory():
    from hyperclaw.server import get_context
    context = await get_context()
    return {"status": "reloaded", **context}


@router.get("/api/trading/status")
async def trading_status():
    """Get the trading engine status."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get("http://localhost:5001/status")
            return resp.json()
    except Exception:
        return {"status": "offline", "error": "ATLAS_TRADING unreachable"}


# ── Trading Routes ────────────────────────────────────────────────────────────
class TradeSignal(BaseModel):
    symbol: str
    side: str  # LONG or SHORT
    size_usd: float = 50
    entry: float = 0


@router.post("/api/trading/signal")
async def send_trade_signal(signal: TradeSignal):
    """Send trading signal to ATLAS_TRADING."""
    try:
        from hyperclaw.trading import send_signal
        return await send_signal(signal.symbol, signal.side, signal.size_usd, signal.entry)
    except Exception as e:
        raise HTTPException(status_code=502, detail="Integration request failed") from e


@router.post("/api/trading/close")
async def close_position(symbol: str = "", close_all: bool = False):
    """Close position(s) on ATLAS_TRADING."""
    try:
        from hyperclaw.trading import close_position
        return await close_position(symbol, close_all)
    except Exception as e:
        raise HTTPException(status_code=502, detail="Integration request failed") from e


@router.post("/api/trading/halt")
async def halt_trading():
    """Emergency halt ATLAS_TRADING trading."""
    try:
        from hyperclaw.trading import halt_trading
        return await halt_trading()
    except Exception as e:
        raise HTTPException(status_code=502, detail="Integration request failed") from e


@router.post("/api/trading/resume")
async def resume_trading():
    """Resume ATLAS_TRADING trading."""
    try:
        from hyperclaw.trading import resume_trading
        return await resume_trading()
    except Exception as e:
        raise HTTPException(status_code=502, detail="Integration request failed") from e


# ── Prometheus Routes ─────────────────────────────────────────────────────────
@router.post("/api/memory/consolidate")
async def consolidate_memory(days_back: int = 1):
    """Run Prometheus memory consolidation."""
    try:
        from hyperclaw.prometheus import run_consolidation
        result = await run_consolidation(days_back)
        return {"status": "ok", "result": result}
    except Exception as e:
        raise HTTPException(status_code=502, detail="Integration request failed") from e


@router.get("/api/prometheus/status")
async def prometheus_status():
    """Get Prometheus consolidation status."""
    try:
        from hyperclaw.prometheus import get_prometheus
        return get_prometheus().status()
    except Exception as e:
        raise HTTPException(status_code=502, detail="Integration request failed") from e


# ── Live Feed Routes ──────────────────────────────────────────────────────────
@router.get("/api/markets")
async def get_markets(symbols: Optional[str] = None):
    """Get live market data from Yahoo Finance."""
    from hyperclaw import feeds
    try:
        symbol_list = symbols.split(",") if symbols else list(feeds.MARKET_SYMBOLS.keys())
        data = await feeds.get_markets(symbol_list)
        return {"markets": data, "count": len(data), "timestamp": time.time()}
    except Exception as e:
        raise HTTPException(status_code=502, detail="Market feed unavailable") from e


@router.get("/api/intel")
async def get_intel(limit: int = 30):
    """Get aggregated intel from RSS feeds."""
    from hyperclaw import feeds
    try:
        items = await feeds.get_intel(limit)
        return {"items": items, "count": len(items), "timestamp": time.time()}
    except Exception as e:
        raise HTTPException(status_code=502, detail="Intel feed unavailable") from e


@router.get("/api/summits")
async def get_summits():
    """Get upcoming summit calendar."""
    from hyperclaw import feeds
    try:
        return {"summits": feeds.get_summits()}
    except Exception as e:
        raise HTTPException(status_code=502, detail="Summit feed unavailable") from e


@router.get("/api/polymarket")
async def get_polymarket(limit: int = 20):
    """Get live Polymarket prediction markets."""
    from hyperclaw import feeds
    try:
        markets = await feeds.get_polymarket(limit)
        return {"markets": markets, "count": len(markets), "timestamp": time.time()}
    except Exception as e:
        raise HTTPException(status_code=502, detail="Market feed unavailable") from e


class TTSRequest(BaseModel):
    text: str
    voice_id: Optional[str] = None


@router.post("/api/tts")
async def text_to_speech(req: TTSRequest):
    """Convert text to speech using ElevenLabs George voice."""
    from hyperclaw import tts
    try:
        voice = req.voice_id or tts.GEORGE_VOICE_ID
        audio_b64 = await tts.text_to_speech_base64(req.text, voice)
        if audio_b64:
            return {"audio": audio_b64, "format": "mp3", "voice": voice}
        raise HTTPException(status_code=502, detail="TTS generation failed")
    except Exception as e:
        raise HTTPException(status_code=502, detail="Voice service unavailable") from e


@router.get("/api/tts/voices")
async def list_voices():
    """List available ElevenLabs voices."""
    from hyperclaw import tts
    try:
        voices = await tts.get_voices()
        return {"voices": voices}
    except Exception as e:
        raise HTTPException(status_code=502, detail="Voice service unavailable") from e

