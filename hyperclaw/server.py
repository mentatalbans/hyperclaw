"""
HyperClaw Production Server
Fully integrated FastAPI server with all components connected.
"""

import os
import sys
import time
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from datetime import datetime

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("hyperclaw.server")

# Load environment
HYPERCLAW_ROOT = Path(os.environ.get("HYPERCLAW_ROOT", Path.home() / ".hyperclaw"))
SECRETS_PATH = HYPERCLAW_ROOT / "workspace" / "secrets"

# Try to load from secrets directory first
env_path = SECRETS_PATH / ".env"
if env_path.exists():
    load_dotenv(env_path)
else:
    load_dotenv()

# Add project to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from hyperclaw.orchestrator import get_orchestrator, Orchestrator
from hyperclaw.agent_coordinator import get_coordinator, Task, TaskStatus
from hyperclaw.model_router import get_model_router, ModelTier
from hyperclaw.setup import run_setup, HYPERCLAW_ROOT

# ============================================================================
# GLOBALS
# ============================================================================

_orchestrator: Optional[Orchestrator] = None
_db_pool = None
START_TIME = time.time()


# ============================================================================
# DATABASE CONNECTION
# ============================================================================

async def create_db_pool():
    """Create database connection pool."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.warning("DATABASE_URL not set - running without database")
        return None

    try:
        import asyncpg
        pool = await asyncpg.create_pool(
            db_url,
            min_size=1,
            max_size=10,
            command_timeout=30
        )
        logger.info("Database connection pool created")
        return pool
    except ImportError:
        logger.warning("asyncpg not installed - pip install asyncpg")
        return None
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        return None


# ============================================================================
# LIFESPAN MANAGEMENT
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown."""
    global _orchestrator, _db_pool

    logger.info("=" * 60)
    logger.info("  HyperClaw Server Starting")
    logger.info("=" * 60)

    # Create database pool
    _db_pool = await create_db_pool()

    # Initialize orchestrator
    _orchestrator = await get_orchestrator(_db_pool)

    logger.info("HyperClaw Server ONLINE")
    logger.info(f"  - Model: {os.environ.get('HYPERCLAW_MODEL', 'claude-sonnet-4-20250514')}")
    logger.info(f"  - Database: {'Connected' if _db_pool else 'Not configured'}")
    logger.info(f"  - Workspace: {HYPERCLAW_ROOT}")

    yield

    # Shutdown
    logger.info("Shutting down HyperClaw Server...")
    if _orchestrator:
        await _orchestrator.shutdown()
    if _db_pool:
        await _db_pool.close()
    logger.info("Server shutdown complete")


# ============================================================================
# FASTAPI APP
# ============================================================================

app = FastAPI(
    title="HyperClaw",
    description="AI Assistant Platform with Multi-Agent Orchestration",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files
ROOT_DIR = Path(__file__).parent.parent
DASHBOARD_DIR = ROOT_DIR / "ui" / "control_center"
if DASHBOARD_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR)), name="static")


# ============================================================================
# REQUEST MODELS
# ============================================================================

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = "default"
    stream: Optional[bool] = False


class ChatResponse(BaseModel):
    response: str
    session_id: str
    timestamp: str


class MemoryRequest(BaseModel):
    content: str
    memory_type: Optional[str] = "episode"
    domain: Optional[str] = None
    importance: Optional[float] = 0.5
    is_core: Optional[bool] = False


class RecallRequest(BaseModel):
    query: str
    limit: Optional[int] = 5
    memory_type: Optional[str] = None


# ============================================================================
# CORE ROUTES
# ============================================================================

@app.get("/")
async def root():
    """Serve dashboard or API info."""
    dashboard_path = DASHBOARD_DIR / "index.html"
    if dashboard_path.exists():
        return FileResponse(dashboard_path)
    return JSONResponse({
        "name": "HyperClaw",
        "version": "1.0.0",
        "status": "operational",
        "docs": "/docs",
    })


@app.get("/health")
async def health():
    """Health check endpoint."""
    uptime = time.time() - START_TIME

    health_data = {
        "status": "healthy",
        "uptime_seconds": round(uptime, 1),
        "timestamp": datetime.now().isoformat(),
        "components": {
            "api": True,
            "orchestrator": _orchestrator is not None and _orchestrator._initialized,
            "database": _db_pool is not None,
            "memory": _orchestrator._memory is not None if _orchestrator else False,
        }
    }

    # Check if any critical component is down
    if not health_data["components"]["orchestrator"]:
        health_data["status"] = "degraded"

    return health_data


@app.get("/status")
async def status():
    """Detailed system status."""
    if not _orchestrator:
        return {"error": "Orchestrator not initialized"}
    return _orchestrator.get_status()


# ============================================================================
# CHAT ENDPOINTS
# ============================================================================

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Main chat endpoint."""
    if not _orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    response = await _orchestrator.chat(
        message=request.message,
        session_id=request.session_id,
        channel="api",
        stream=False
    )

    return ChatResponse(
        response=response,
        session_id=request.session_id,
        timestamp=datetime.now().isoformat()
    )


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """Streaming chat endpoint."""
    if not _orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    async def generate():
        async for chunk in await _orchestrator.chat(
            message=request.message,
            session_id=request.session_id,
            channel="api",
            stream=True
        ):
            yield f"data: {chunk}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream"
    )


@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """WebSocket chat endpoint for real-time streaming."""
    await websocket.accept()
    session_id = f"ws_{int(time.time())}"

    try:
        while True:
            data = await websocket.receive_text()
            message = data

            if not _orchestrator:
                await websocket.send_text("[Error: Orchestrator not initialized]")
                continue

            # Stream response
            async for chunk in await _orchestrator.chat(
                message=message,
                session_id=session_id,
                channel="websocket",
                stream=True
            ):
                await websocket.send_text(chunk)

            # Send end marker
            await websocket.send_text("\n[END]")

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {session_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await websocket.close()


@app.post("/reset")
async def reset_session(session_id: str = "default"):
    """Reset a conversation session."""
    if _orchestrator and _orchestrator._memory:
        _orchestrator._memory._conversation_history.pop(session_id, None)
    return {"status": "reset", "session_id": session_id}


# ============================================================================
# MEMORY ENDPOINTS
# ============================================================================

@app.post("/api/memory/remember")
async def remember(request: MemoryRequest):
    """Store a memory explicitly."""
    if not _orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    memory_id = await _orchestrator.remember(
        content=request.content,
        memory_type=request.memory_type,
        domain=request.domain,
        importance=request.importance,
        is_core=request.is_core
    )

    return {"status": "stored", "memory_id": memory_id}


@app.post("/api/memory/recall")
async def recall(request: RecallRequest):
    """Recall relevant memories."""
    if not _orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    memories = await _orchestrator.recall(
        query=request.query,
        limit=request.limit,
        memory_type=request.memory_type
    )

    return {
        "query": request.query,
        "count": len(memories),
        "memories": [
            {
                "id": m.id,
                "content": m.content,
                "type": m.memory_type,
                "domain": m.domain,
                "importance": m.importance,
            }
            for m in memories
        ]
    }


@app.get("/api/memory/context")
async def get_context():
    """Get current system context."""
    if not _orchestrator or not _orchestrator._memory:
        raise HTTPException(status_code=503, detail="Memory not initialized")

    return {
        "system_context": _orchestrator._memory.get_system_context(),
        "working_memory": _orchestrator._memory.get_working_memory(),
    }


# ============================================================================
# AGENT ENDPOINTS
# ============================================================================

@app.get("/api/agents")
async def list_agents():
    """List all available agents."""
    # Try to load from agent config
    try:
        import yaml
        agents_config = ROOT_DIR / "config" / "agents.yaml"
        if agents_config.exists():
            with open(agents_config) as f:
                config = yaml.safe_load(f)
                agents = config.get("agents", [])
                return {
                    "count": len(agents),
                    "agents": [
                        {
                            "id": a.get("id", "unknown"),
                            "name": a.get("name", a.get("id", "Unknown")),
                            "domain": a.get("domain", "general"),
                            "role": a.get("role", ""),
                        }
                        for a in agents
                    ]
                }
    except Exception as e:
        logger.warning(f"Failed to load agents config: {e}")

    return {
        "count": 0,
        "agents": [],
        "message": "Agent configuration not loaded"
    }


@app.post("/api/agents/{agent_id}/dispatch")
async def dispatch_to_agent(agent_id: str, task: str):
    """Dispatch a task to a specific agent."""
    if not _orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    result = await _orchestrator.dispatch_task(goal=task, agent_id=agent_id)
    return {
        "task_id": result.id,
        "agent_id": result.assigned_to,
        "status": result.status.value,
    }


# ============================================================================
# TASK ENDPOINTS
# ============================================================================

class TaskRequest(BaseModel):
    goal: str
    domain: Optional[str] = None
    task_type: Optional[str] = None
    agent_id: Optional[str] = None
    priority: Optional[int] = 5


@app.post("/api/tasks")
async def create_task(request: TaskRequest):
    """Create and queue a new task."""
    if not _orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    task = await _orchestrator.dispatch_task(
        goal=request.goal,
        domain=request.domain,
        task_type=request.task_type,
        agent_id=request.agent_id,
        priority=request.priority,
    )

    return {
        "task_id": task.id,
        "goal": task.goal,
        "domain": task.domain,
        "task_type": task.task_type,
        "assigned_to": task.assigned_to,
        "status": task.status.value,
    }


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    """Get task status and result."""
    if not _orchestrator or not _orchestrator._coordinator:
        raise HTTPException(status_code=503, detail="Coordinator not initialized")

    task = _orchestrator._coordinator.tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    return {
        "task_id": task.id,
        "goal": task.goal,
        "domain": task.domain,
        "task_type": task.task_type,
        "assigned_to": task.assigned_to,
        "status": task.status.value,
        "result": task.result,
        "error": task.error,
        "created_at": task.created_at.isoformat(),
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


@app.post("/api/tasks/{task_id}/execute")
async def execute_task(task_id: str):
    """Execute a specific task immediately."""
    if not _orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    result = await _orchestrator.execute_task(task_id)
    return {"task_id": task_id, "result": result}


@app.get("/api/tasks")
async def list_tasks(status: Optional[str] = None, limit: int = 50):
    """List all tasks, optionally filtered by status."""
    if not _orchestrator or not _orchestrator._coordinator:
        raise HTTPException(status_code=503, detail="Coordinator not initialized")

    tasks = list(_orchestrator._coordinator.tasks.values())

    if status:
        tasks = [t for t in tasks if t.status.value == status]

    # Sort by created_at descending
    tasks.sort(key=lambda t: t.created_at, reverse=True)

    return {
        "count": len(tasks[:limit]),
        "tasks": [
            {
                "task_id": t.id,
                "goal": t.goal[:100],
                "domain": t.domain,
                "assigned_to": t.assigned_to,
                "status": t.status.value,
                "created_at": t.created_at.isoformat(),
            }
            for t in tasks[:limit]
        ]
    }


class CoordinateRequest(BaseModel):
    goal: str
    context: Optional[dict] = None
    max_agents: Optional[int] = 3


@app.post("/api/coordinate")
async def coordinate_goal(request: CoordinateRequest):
    """Coordinate multiple agents to accomplish a complex goal."""
    if not _orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    result = await _orchestrator.coordinate_goal(
        goal=request.goal,
        context=request.context,
    )

    return result


# ============================================================================
# COST MANAGEMENT ENDPOINTS
# ============================================================================

@app.get("/api/costs")
async def get_costs():
    """Get current cost statistics."""
    if not _orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    return _orchestrator.get_cost_stats()


@app.post("/api/costs/budget")
async def set_budget(budget_usd: float):
    """Set daily budget limit."""
    if not _orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    _orchestrator.set_daily_budget(budget_usd)
    return {"status": "ok", "daily_budget_usd": budget_usd}


@app.get("/api/models")
async def list_models():
    """List available models and their costs."""
    router = get_model_router()

    return {
        "models": [
            {
                "id": m.id,
                "name": m.name,
                "tier": m.tier.value,
                "provider": m.provider,
                "cost_per_1k_input": m.cost_per_1k_input,
                "cost_per_1k_output": m.cost_per_1k_output,
                "max_tokens": m.max_tokens,
                "latency_ms": m.latency_ms,
                "capabilities": m.capabilities,
            }
            for m in router.models.values()
        ],
        "prefer_cheap": router._prefer_cheap,
        "daily_budget_usd": router._daily_budget,
    }


# ============================================================================
# INTEGRATION ENDPOINTS
# ============================================================================

@app.get("/api/integrations")
async def list_integrations():
    """List configured integrations."""
    if not _orchestrator:
        return {"integrations": {}}

    return {"integrations": _orchestrator._integrations}


@app.post("/api/integrations/telegram/send")
async def send_telegram(chat_id: str, message: str):
    """Send a Telegram message."""
    if not _orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    success = await _orchestrator.send_telegram(chat_id, message)
    return {"success": success}


# ============================================================================
# SETUP ENDPOINT
# ============================================================================

@app.post("/api/setup")
async def setup_hyperclaw(init_db: bool = False):
    """Run HyperClaw setup."""
    result = await run_setup(init_db=init_db)
    return result.to_dict()


# ============================================================================
# WEBHOOK ENDPOINTS (for integrations)
# ============================================================================

@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Handle Telegram webhook updates."""
    try:
        data = await request.json()

        # Extract message
        message = data.get("message", {})
        text = message.get("text", "")
        chat_id = message.get("chat", {}).get("id")

        if text and chat_id and _orchestrator:
            # Process through orchestrator
            response = await _orchestrator.chat(
                message=text,
                session_id=f"telegram_{chat_id}",
                channel="telegram"
            )

            # Send response back
            await _orchestrator.send_telegram(str(chat_id), response)

        return {"ok": True}

    except Exception as e:
        logger.error(f"Telegram webhook error: {e}")
        return {"ok": False, "error": str(e)}


# ============================================================================
# UTILITY ENDPOINTS
# ============================================================================

@app.get("/api/config")
async def get_config():
    """Get non-sensitive configuration."""
    return {
        "hyperclaw_root": str(HYPERCLAW_ROOT),
        "model": os.environ.get("HYPERCLAW_MODEL", "claude-sonnet-4-20250514"),
        "max_tokens": int(os.environ.get("HYPERCLAW_MAX_TOKENS", 4096)),
        "database_configured": bool(os.environ.get("DATABASE_URL")),
        "integrations_configured": list(_orchestrator._integrations.keys()) if _orchestrator else [],
    }


# ============================================================================
# MAIN
# ============================================================================

def main():
    """Run the server."""
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 8001))

    uvicorn.run(
        "hyperclaw.server:app",
        host=host,
        port=port,
        reload=os.environ.get("RELOAD", "").lower() == "true",
        log_level="info"
    )


if __name__ == "__main__":
    main()
