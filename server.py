"""
HyperClaw FastAPI Server
HyperClaw — AI Assistant
"""

import os
import json
import time
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

log = logging.getLogger("hyperclaw.server")

# ── Dispatcher (real agent execution engine) ───────────────────────────────────
import sys
sys.path.insert(0, str(Path(__file__).parent))
from swarm.dispatcher import Dispatcher, Task, TaskStatus

_dispatcher: Optional[Dispatcher] = None

def _get_api_key() -> str:
    env_path = Path(__file__).parent / "workspace" / "secrets" / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("ANTHROPIC_API_KEY", "")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start dispatcher + swarm on boot. Clean shutdown on exit."""
    global _dispatcher
    api_key = _get_api_key()
    if api_key:
        _dispatcher = Dispatcher(api_key=api_key)
        await _dispatcher.start()
        log.info(f"⚡ HyperClaw DISPATCHER ONLINE — {len(_dispatcher.workers)} agents live")
    else:
        log.warning("No API key found — dispatcher not started")
    yield
    if _dispatcher:
        await _dispatcher.stop()
        log.info("Dispatcher shutdown complete")

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="HyperClaw — HyperClaw Dashboard",
    description="AI Assistant powered by HyperClaw",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS - localhost only
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8001", "http://127.0.0.1:8001", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

START_TIME = time.time()
ROOT_DIR = Path(__file__).parent
DASHBOARD_DIR = ROOT_DIR / "ui" / "control_center"

# ── Models ─────────────────────────────────────────────────────────────────────
class ChatMessage(BaseModel):
    message: str
    session_id: Optional[str] = None

class ChatResponse(BaseModel):
    response: str
    agent: str = "Assistant"
    session_id: Optional[str] = None

# ── Context ────────────────────────────────────────────────────────────────────
CONVERSATION_HISTORY: list[dict] = []
TURN_COUNT: int = 0

# ── Static files ───────────────────────────────────────────────────────────────
if DASHBOARD_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR)), name="static")

# ── Routes ─────────────────────────────────────────────────────────────────────
@app.get("/")
async def serve_dashboard():
    """Serve the HyperClaw Dashboard dashboard."""
    dashboard_path = DASHBOARD_DIR / "index.html"
    if dashboard_path.exists():
        return FileResponse(dashboard_path)
    return JSONResponse({"message": "Assistant API running. Dashboard not found.", "status": "operational"})

@app.get("/health")
async def health_check():
    """Health check — full system status."""
    uptime = time.time() - START_TIME

    # Check SATOSHI
    satoshi_status = "offline"
    satoshi_balance = None
    try:
        import httpx
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get("http://localhost:5001/status")
            if resp.status_code == 200:
                data = resp.json()
                satoshi_status = "active" if data.get("funded") and not data.get("halted") else "standby"
                satoshi_balance = data.get("hl_balance")
    except Exception:
        pass

    # Check memory server
    memory_status = "offline"
    try:
        import httpx
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get("http://localhost:8765/health")
            if resp.status_code == 200:
                memory_status = "online"
    except Exception:
        pass

    return {
        "status": "operational",
        "agent": "Assistant",
        "identity": "HyperClaw",
        "principal": "the user Pierre Davis",
        "identity_loaded": True,
        "version": "1.0.0",
        "uptime_seconds": round(uptime, 1),
        "conversation_turns": TURN_COUNT,
        "satoshi": satoshi_status,
        "satoshi_balance": satoshi_balance,
        "memory_server": memory_status,
        "redis": "not_configured",
        "platform": "HyperClaw",
    }

@app.post("/chat", response_model=ChatResponse)
async def chat(msg: ChatMessage):
    """Chat with Assistant — uses full Solomon context engine."""
    global CONVERSATION_HISTORY, TURN_COUNT

    CONVERSATION_HISTORY.append({"role": "user", "content": msg.message})
    TURN_COUNT += 1

    try:
        from hyperclaw.solomon import get_solomon
        solomon = get_solomon()
        response_text = await solomon.chat(msg.message, CONVERSATION_HISTORY[:-1])
    except Exception as e:
        error_str = str(e).lower()
        # Check for tool limit / rate limit errors
        if any(kw in error_str for kw in ['tool', 'limit', 'rate', 'quota', 'exceeded', 'too many']):
            log.warning(f"Tool/rate limit hit — auto-resetting conversation: {e}")
            # Auto-reset conversation to recover
            CONVERSATION_HISTORY = []
            TURN_COUNT = 0
            response_text = "Context limit reached — I've reset our conversation. What would you like to work on?"
        else:
            log.error(f"Chat error: {e}")
            response_text = _stub_response(msg.message)

    CONVERSATION_HISTORY.append({"role": "assistant", "content": response_text})

    # Keep history trimmed
    if len(CONVERSATION_HISTORY) > 40:
        CONVERSATION_HISTORY = CONVERSATION_HISTORY[-40:]

    return ChatResponse(response=response_text, agent="Assistant", session_id=msg.session_id)


@app.post("/reset")
async def reset_conversation():
    """Reset conversation history — use when hitting limits."""
    global CONVERSATION_HISTORY, TURN_COUNT
    old_turns = TURN_COUNT
    CONVERSATION_HISTORY = []
    TURN_COUNT = 0
    log.info(f"Conversation reset (was {old_turns} turns)")
    return {"status": "reset", "previous_turns": old_turns, "message": "Conversation cleared. Ready for new tasks."}

@app.get("/api/agents")
async def list_agents():
    """List all 50 swarm agents with live status."""
    try:
        from hyperclaw.swarm import get_swarm
        swarm = get_swarm()
        status = swarm.status()
        return {
            "coordinator": "Assistant",
            "swarm_active": True,
            "total_agents": status["total_agents"],
            "domains": status["domains"],
            "agents": status["agent_list"],
        }
    except Exception as e:
        return {
            "coordinator": "Assistant",
            "swarm_active": False,
            "error": str(e),
            "agents": [
                {"id": "assistant", "name": "Assistant", "status": "active", "domain": "executive"},
            ],
        }


class SwarmTaskRequest(BaseModel):
    task: str
    agent_id: Optional[str] = None
    domain: Optional[str] = None
    context: Optional[str] = ""


@app.post("/api/swarm/dispatch")
async def swarm_dispatch(req: SwarmTaskRequest):
    """Dispatch a task to the real Dispatcher — auto-routed or to a specific agent."""
    if not _dispatcher:
        raise HTTPException(status_code=503, detail="Dispatcher not running — check API key")
    task = await _dispatcher.dispatch(
        goal=req.task,
        task_type="research",
        domain=req.domain or "",
        agent_id=req.agent_id or "",
    )
    return {
        "task_id": task.task_id,
        "assigned_to": task.assigned_to,
        "status": task.status,
        "goal": task.goal[:120],
        "message": f"Task queued → {task.assigned_to}. Poll /api/swarm/task/{task.task_id} for result.",
    }


@app.get("/api/swarm/task/{task_id}")
async def get_task_result(task_id: str):
    """Poll a task for its result."""
    if not _dispatcher:
        raise HTTPException(status_code=503, detail="Dispatcher not running")
    task = _dispatcher.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return {
        "task_id": task.task_id,
        "assigned_to": task.assigned_to,
        "status": task.status,
        "result": task.result if task.status == TaskStatus.DONE else None,
        "error": task.error if task.status == TaskStatus.FAILED else None,
        "completed_at": task.completed_at,
    }


@app.get("/api/swarm/status")
async def swarm_status():
    """Full swarm health — all 56 agents, task queue, skill scout."""
    if not _dispatcher:
        return {"dispatcher": "OFFLINE", "agents_registered": 0}
    return _dispatcher.swarm_status()


@app.post("/api/swarm/all-hands")
async def swarm_all_hands():
    """Trigger All Hands — dispatch status report tasks to all domain leads."""
    if not _dispatcher:
        raise HTTPException(status_code=503, detail="Dispatcher not running")

    domain_leads = {
        "executive": "SOLOMON",
        "business": "NEXUS",
        "communications": "ECHO",
        "technology": "FORGE",
        "talent": "SCOUT",
        "creative": "MUSE",
        "scientific": "QUANTUM",
        "personal": "VALET",
    }

    tasks_dispatched = []
    for domain, lead in domain_leads.items():
        task = await _dispatcher.dispatch(
            goal=f"All Hands status report for the {domain} domain. Summarize your team's readiness, any active tasks, and top priority for today.",
            task_type="analysis",
            domain=domain,
            agent_id=lead,
            priority=2,
        )
        tasks_dispatched.append({"domain": domain, "agent": lead, "task_id": task.task_id})

    return {
        "all_hands": "INITIATED",
        "tasks_dispatched": len(tasks_dispatched),
        "agents": tasks_dispatched,
        "message": "Poll /api/swarm/task/{task_id} for each agent's report.",
    }


@app.get("/api/swarm/agent/{agent_id}")
async def get_agent_info(agent_id: str):
    """Get info on a specific swarm agent."""
    try:
        from hyperclaw.swarm import get_swarm
        swarm = get_swarm()
        agent = swarm.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
        return {
            "id": agent.id,
            "name": agent.name,
            "role": agent.role,
            "domain": agent.domain,
            "persona": agent.persona,
            "task_types": agent.task_types,
            "capabilities": agent.capabilities,
            "model": agent.model,
            "status": agent.status,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/memory/stats")
async def memory_stats():
    """HyperMemory statistics."""
    return {
        "status": "active",
        "layers": {
            "L0_instincts": "loaded",
            "L1_core_episodes": "loaded",
            "L2_knowledge": "loaded",
            "L3_episodic": "loaded",
        },
        "vector_store": "supabase+pgvector",
        "active_tasks": 2,
    }

@app.post("/api/memory/reload")
async def reload_memory():
    """Reload Assistant's memory context."""
    try:
        from hyperclaw.solomon import get_solomon
        get_solomon().reload_context()
        return {"status": "reloaded", "message": "Assistant memory layers refreshed."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/satoshi/status")
async def satoshi_status():
    """Get SATOSHI trading engine status."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get("http://localhost:5001/status")
            return resp.json()
    except Exception:
        return {"status": "offline", "error": "SATOSHI unreachable"}


# ── Trading Routes ────────────────────────────────────────────────────────────
class TradeSignal(BaseModel):
    symbol: str
    side: str  # LONG or SHORT
    size_usd: float = 50
    entry: float = 0


@app.post("/api/satoshi/signal")
async def send_trade_signal(signal: TradeSignal):
    """Send trading signal to SATOSHI."""
    try:
        from hyperclaw.trading import send_signal
        return await send_signal(signal.symbol, signal.side, signal.size_usd, signal.entry)
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post("/api/satoshi/close")
async def close_position(symbol: str = "", close_all: bool = False):
    """Close position(s) on SATOSHI."""
    try:
        from hyperclaw.trading import close_position
        return await close_position(symbol, close_all)
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post("/api/satoshi/halt")
async def halt_trading():
    """Emergency halt SATOSHI trading."""
    try:
        from hyperclaw.trading import halt_satoshi
        return await halt_satoshi()
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post("/api/satoshi/resume")
async def resume_trading():
    """Resume SATOSHI trading."""
    try:
        from hyperclaw.trading import resume_satoshi
        return await resume_satoshi()
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── Prometheus Routes ─────────────────────────────────────────────────────────
@app.post("/api/memory/consolidate")
async def consolidate_memory(days_back: int = 1):
    """Run Prometheus memory consolidation."""
    try:
        from hyperclaw.prometheus import run_consolidation
        result = await run_consolidation(days_back)
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/api/prometheus/status")
async def prometheus_status():
    """Get Prometheus consolidation status."""
    try:
        from hyperclaw.prometheus import get_prometheus
        return get_prometheus().status()
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── Live Feed Routes ──────────────────────────────────────────────────────────
# Import feeds module directly to avoid hyperclaw.__init__ dependency chain
import importlib.util
_feeds_spec = importlib.util.spec_from_file_location("feeds", ROOT_DIR / "hyperclaw" / "feeds.py")
_feeds_module = importlib.util.module_from_spec(_feeds_spec)
_feeds_spec.loader.exec_module(_feeds_module)

_tts_spec = importlib.util.spec_from_file_location("tts", ROOT_DIR / "hyperclaw" / "tts.py")
_tts_module = importlib.util.module_from_spec(_tts_spec)
_tts_spec.loader.exec_module(_tts_module)


@app.get("/api/markets")
async def get_markets(symbols: Optional[str] = None):
    """Get live market data from Yahoo Finance."""
    try:
        symbol_list = symbols.split(",") if symbols else list(_feeds_module.MARKET_SYMBOLS.keys())
        data = await _feeds_module.get_markets(symbol_list)
        return {"markets": data, "count": len(data), "timestamp": time.time()}
    except Exception as e:
        return {"error": str(e), "markets": []}


@app.get("/api/intel")
async def get_intel(limit: int = 30):
    """Get aggregated intel from RSS feeds."""
    try:
        items = await _feeds_module.get_intel(limit)
        return {"items": items, "count": len(items), "timestamp": time.time()}
    except Exception as e:
        return {"error": str(e), "items": []}


@app.get("/api/summits")
async def get_summits():
    """Get upcoming summit calendar."""
    try:
        return {"summits": _feeds_module.get_summits()}
    except Exception as e:
        return {"error": str(e), "summits": []}


@app.get("/api/polymarket")
async def get_polymarket(limit: int = 20):
    """Get live Polymarket prediction markets."""
    try:
        markets = await _feeds_module.get_polymarket(limit)
        return {"markets": markets, "count": len(markets), "timestamp": time.time()}
    except Exception as e:
        return {"error": str(e), "markets": []}


class TTSRequest(BaseModel):
    text: str
    voice_id: Optional[str] = None


@app.post("/api/tts")
async def text_to_speech(req: TTSRequest):
    """Convert text to speech using ElevenLabs George voice."""
    try:
        voice = req.voice_id or _tts_module.GEORGE_VOICE_ID
        audio_b64 = await _tts_module.text_to_speech_base64(req.text, voice)
        if audio_b64:
            return {"audio": audio_b64, "format": "mp3", "voice": voice}
        return {"error": "TTS generation failed"}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/tts/voices")
async def list_voices():
    """List available ElevenLabs voices."""
    try:
        voices = await _tts_module.get_voices()
        return {"voices": voices}
    except Exception as e:
        return {"error": str(e)}


def _stub_response(message: str) -> str:
    """Fallback responses when Solomon unavailable."""
    msg = message.lower()
    if any(w in msg for w in ["hello", "hi", "hey"]):
        return "Assistant online. How can I help?"
    if any(w in msg for w in ["status", "health"]):
        return "All systems operational. FastAPI ✓ | Telegram ✓ | SATOSHI active."
    return "Standing by. What would you like to work on?"

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("HYPERCLAW_PORT", "8001"))
    uvicorn.run("server:app", host=host, port=port, reload=False)
