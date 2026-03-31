"""
HyperClaw Trading Module — SATOSHI Integration
"""

from pathlib import Path
import httpx
import logging

logger = logging.getLogger("hyperclaw.trading")

SATOSHI_URL = "http://127.0.0.1:5001"
SATOSHI_ROOT = Path(str(Path.home() / ".hyperclaw/workspace/trading/satoshi"))


async def get_satoshi_status() -> dict:
    """Get SATOSHI trading engine status."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{SATOSHI_URL}/status")
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.warning(f"SATOSHI unreachable: {e}")
    return {"status": "offline", "funded": False, "standby": True}


async def send_signal(symbol: str, side: str, size_usd: float = 50, entry: float = 0) -> dict:
    """Send trading signal to SATOSHI."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{SATOSHI_URL}/signal",
                json={
                    "symbol": symbol,
                    "side": side,
                    "size_usd": size_usd,
                    "entry": entry,
                }
            )
            return resp.json()
    except Exception as e:
        logger.error(f"Signal failed: {e}")
        return {"status": "error", "error": str(e)}


async def close_position(symbol: str = "", close_all: bool = False) -> dict:
    """Close position(s) on SATOSHI."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{SATOSHI_URL}/close",
                json={"symbol": symbol, "close_all": close_all}
            )
            return resp.json()
    except Exception as e:
        logger.error(f"Close failed: {e}")
        return {"status": "error", "error": str(e)}


async def halt_satoshi() -> dict:
    """Emergency halt SATOSHI trading."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{SATOSHI_URL}/halt")
            return resp.json()
    except Exception as e:
        return {"status": "error", "error": str(e)}


async def resume_satoshi() -> dict:
    """Resume SATOSHI trading."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{SATOSHI_URL}/resume")
            return resp.json()
    except Exception as e:
        return {"status": "error", "error": str(e)}
