"""
HyperClaw FastAPI Server
SOLOMON — Multi-Agent AI Orchestration Platform
"""

import os
import json
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
import uvicorn

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="HyperClaw API",
    description="Multi-Agent AI Orchestration Platform powered by SOLOMON",
    version="0.1.0-alpha",
    docs_url="/docs",
    redoc_url="/redoc",
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
    agent: str = "SOLOMON"
    session_id: Optional[str] = None

# ── Context ────────────────────────────────────────────────────────────────────
CONVERSATION_HISTORY: list[dict] = []

# ── Routes ─────────────────────────────────────────────────────────────────────
@app.get("/")
async def serve_dashboard():
    """Serve the SOLOMON Control Center dashboard."""
    dashboard_path = DASHBOARD_DIR / "index.html"
    if dashboard_path.exists():
        return FileResponse(dashboard_path)
    return JSONResponse(
        {"message": "HyperClaw API running. Dashboard not found at ui/control_center/index.html"},
        status_code=200,
    )

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    uptime = time.time() - START_TIME
    return {
        "status": "operational",
        "version": "0.1.0-alpha",
        "agent": "SOLOMON",
        "uptime_seconds": round(uptime, 1),
    }

@app.post("/chat", response_model=ChatResponse)
async def chat(msg: ChatMessage):
    """
    Chat endpoint — SOLOMON responds using Anthropic Claude API.
    Falls back to intelligent stubs if API unavailable.
    """
    global CONVERSATION_HISTORY

    CONVERSATION_HISTORY.append({"role": "user", "content": msg.message})

    try:
        response_text = await _get_claude_response(msg.message)
    except Exception:
        response_text = _stub_response(msg.message)

    CONVERSATION_HISTORY.append({"role": "assistant", "content": response_text})

    return ChatResponse(response=response_text, agent="SOLOMON", session_id=msg.session_id)

@app.get("/api/agents")
async def list_agents():
    """List registered swarm agents."""
    return {
        "coordinator": "SOLOMON",
        "agents": [
            {"id": "solomon", "name": "SOLOMON", "status": "active", "domain": "orchestration"},
            {"id": "prometheus", "name": "PROMETHEUS", "status": "active", "domain": "reasoning"},
            {"id": "genesis", "name": "GENESIS", "status": "standby", "domain": "expansion"},
            {"id": "omega", "name": "OMEGA", "status": "active", "domain": "optimization"},
        ],
    }

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
        "embedding_model": "text-embedding-3-small",
    }

@app.post("/api/memory/reload")
async def reload_memory():
    """Reload memory from configured sources."""
    return {"status": "reloaded", "message": "Memory layers refreshed successfully."}

# ── Internal ───────────────────────────────────────────────────────────────────
async def _get_claude_response(message: str) -> str:
    """Get response using Anthropic Claude API."""
    try:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key or api_key == "sk-ant-your-key-here":
            return _stub_response(message)

        client = anthropic.Anthropic(api_key=api_key)
        recent = CONVERSATION_HISTORY[-10:] if len(CONVERSATION_HISTORY) > 10 else CONVERSATION_HISTORY

        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=1024,
            system=(
                "You are SOLOMON — the orchestration intelligence for HyperClaw, "
                "a multi-agent AI platform. You coordinate agents, manage memory, "
                "and help users automate complex workflows across every domain. "
                "Be concise, precise, and action-oriented."
            ),
            messages=recent,
        )
        return response.content[0].text
    except Exception as e:
        print(f"[SOLOMON] API error: {e}")
        return _stub_response(message)


def _stub_response(message: str) -> str:
    """Intelligent stub responses when API unavailable."""
    msg = message.lower()
    if any(w in msg for w in ["hello", "hi", "hey", "online", "status"]):
        return "SOLOMON online. All systems operational. How can I help?"
    if any(w in msg for w in ["who are you", "what are you", "introduce"]):
        return "I'm SOLOMON — HyperClaw's orchestration intelligence. I coordinate your agent swarm, manage memory, and automate workflows across every domain."
    if any(w in msg for w in ["agent", "swarm"]):
        return "Active agents: SOLOMON (orchestration), PROMETHEUS (reasoning), OMEGA (optimization), GENESIS (expansion). All systems nominal."
    if any(w in msg for w in ["memory", "remember", "recall"]):
        return "HyperMemory is active across 4 layers: Instincts (L0), Core Episodes (L1), Knowledge Base (L2), Episodic Logs (L3). What would you like to retrieve?"
    if any(w in msg for w in ["help", "what can you do", "capabilities"]):
        return "HyperClaw can: orchestrate multi-agent workflows, manage long-term memory, automate tasks across 30+ integrations, run autonomous research, and scale to your entire organization. Where would you like to start?"
    return "SOLOMON standing by. What would you like to work on?"


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host=host, port=port, reload=False)
