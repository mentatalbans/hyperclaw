"""
HyperClaw Production Server
Fully integrated FastAPI server with all components connected.
"""

import os
import hmac
import json
import re
import sys
import time
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Optional, TYPE_CHECKING
from datetime import datetime
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("hyperclaw.server")

HYPERCLAW_ROOT = Path(os.environ.get("HYPERCLAW_ROOT", Path.home() / ".hyperclaw"))
if TYPE_CHECKING:
    from hyperclaw.orchestrator import Orchestrator


def _load_environment():
    """Load configuration before importing components that cache credentials."""
    global HYPERCLAW_ROOT
    mount = Path(os.environ.get("SECRETS_MOUNT", "/mnt/secrets"))
    if mount.is_dir():
        for path in sorted(mount.iterdir()):
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text())
                if not isinstance(data, dict):
                    continue
                for key, value in data.items():
                    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) and isinstance(value, str) and "\x00" not in value:
                        os.environ.setdefault(key, value)
                if path.name == "telegram":
                    for source, target in {"BOT_TOKEN": "TELEGRAM_BOT_TOKEN", "ALLOWED_CHAT_IDS": "TELEGRAM_ALLOWED_CHAT_IDS", "WEBHOOK_SECRET": "TELEGRAM_WEBHOOK_SECRET"}.items():
                        value = data.get(source)
                        if isinstance(value, str) and "\x00" not in value:
                            os.environ.setdefault(target, value)
            except (OSError, ValueError):
                logger.warning("Unable to read mounted secret %s", path.name)
    HYPERCLAW_ROOT = Path(os.environ.get("HYPERCLAW_ROOT", Path.home() / ".hyperclaw"))
    load_dotenv(HYPERCLAW_ROOT / ".env")
    load_dotenv(HYPERCLAW_ROOT / "workspace" / "secrets" / ".env")
    load_dotenv()
    from hyperclaw.local import load_profile
    load_profile()


async def get_orchestrator(db_pool=None):
    from hyperclaw.orchestrator import get_orchestrator as factory
    return await factory(db_pool)


async def _close_channel_history():
    """Release the optional legacy Telegram database after channel work stops."""
    telegram = sys.modules.get("hyperclaw.telegram_bot")
    if telegram is not None:
        await telegram.close_legacy_history()

# ============================================================================
# GLOBALS
# ============================================================================

_orchestrator: Optional["Orchestrator"] = None
_db_pool = None
START_TIME = time.time()


# ============================================================================
# DATABASE CONNECTION
# ============================================================================

async def create_db_pool():
    """Create database connection pool."""
    if os.environ.get("HYPERCLAW_ENABLE_DATABASE", "").lower() in {"false", "0", "no"}:
        return None
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
    """Own one runtime and explicitly enabled background services."""
    global _orchestrator, _db_pool
    _load_environment()
    try:
        async with AsyncExitStack() as cleanup:
            _db_pool = await create_db_pool()
            if _db_pool:
                cleanup.push_async_callback(_db_pool.close)
            _orchestrator = await get_orchestrator(_db_pool)
            cleanup.push_async_callback(_orchestrator.shutdown)
            cleanup.push_async_callback(_close_channel_history)
            app.state.orchestrator = _orchestrator
            if os.environ.get("HYPERCLAW_ENABLE_TELEGRAM", "").lower() in {"true", "1", "yes"}:
                if os.environ.get("TELEGRAM_BOT_TOKEN"):
                    from hyperclaw.telegram_bot import get_telegram_bot
                    telegram = get_telegram_bot().build(token=os.environ["TELEGRAM_BOT_TOKEN"])
                    await telegram.initialize()
                    cleanup.push_async_callback(telegram.shutdown)
                    await telegram.start()
                    cleanup.push_async_callback(telegram.stop)
                    await telegram.updater.start_polling(drop_pending_updates=True)
                    cleanup.push_async_callback(telegram.updater.stop)
                else:
                    logger.warning("Telegram enabled without token; polling skipped")
            if os.environ.get("HYPERCLAW_ENABLE_SCHEDULER", "").lower() in {"true", "1", "yes"}:
                from hyperclaw.scheduler import get_scheduler
                scheduler = get_scheduler(_orchestrator.send_telegram)
                scheduler.start()
                cleanup.callback(scheduler.stop)
            logger.info("HyperClaw server online; workspace=%s", HYPERCLAW_ROOT)
            yield
    finally:
        _orchestrator = None
        _db_pool = None
        app.state.orchestrator = None


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
    allow_origins=["http://localhost:8001", "http://127.0.0.1:8001", "http://localhost:3000"],
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
    session_id: str = "default"
    stream: bool = False
    force_model: Optional[str] = None
    attachments: Optional[list[dict]] = None
    tools: Optional[bool] = None

    @field_validator("session_id", mode="before")
    @classmethod
    def normalize_session(cls, value):
        return uuid4().hex if value is None or value == "" else value


class ChatResponse(BaseModel):
    response: str
    session_id: str
    timestamp: str
    agent: str = "Assistant"


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
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")
    return _orchestrator.get_status()


# ============================================================================
# CHAT ENDPOINTS
# ============================================================================

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Main chat endpoint."""
    if not _orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    if request.stream:
        return await chat_stream(request)
    try:
        response = await _orchestrator.chat(
            message=request.message,
            session_id=request.session_id,
            channel="api",
            stream=False,
            force_model=request.force_model,
            attachments=request.attachments,
            tools=request.tools,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Chat inference failed")
        raise HTTPException(status_code=502, detail="Model request failed") from exc

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

    events = _orchestrator.stream_events(
        message=request.message, session_id=request.session_id, channel="api",
        force_model=request.force_model, attachments=request.attachments, tools=request.tools,
    )
    try:
        first = await anext(events, None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Streaming inference failed before response")
        raise HTTPException(status_code=502, detail="Model request failed") from exc

    def encode(event):
        kind, text = event
        prefix = "event: thinking\n" if kind == "thinking" else ""
        return f"{prefix}data: {json.dumps(text)}\n\n"

    async def generate():
        try:
            if first is not None:
                yield encode(first)
            async for event in events:
                yield encode(event)
            yield "data: [DONE]\n\n"
        except Exception:
            logger.exception("Streaming inference interrupted")
            yield 'event: error\ndata: {"error": "Model stream interrupted"}\n\n'
        finally:
            await events.aclose()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """WebSocket chat endpoint for real-time streaming."""
    await websocket.accept()
    session_id = f"ws_{uuid4().hex}"

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
    if not _orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")
    await _orchestrator.reset_session(session_id)
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
    """List the agents used by the running coordinator."""
    coordinator = _require_coordinator()
    agents = coordinator.list_agents()
    return {"count": len(agents), "total_agents": len(agents), "agents": agents,
            "coordinator": "Assistant", "swarm_active": coordinator._running,
            "domains": coordinator.get_status()["agents_by_domain"]}


def _require_coordinator():
    if not _orchestrator or not _orchestrator._coordinator:
        raise HTTPException(status_code=503, detail="Coordinator not initialized")
    return _orchestrator._coordinator


def _resolve_agent(agent_id: Optional[str], domain: Optional[str] = None):
    """Accept canonical IDs, full display names, and unambiguous legacy names."""
    if not agent_id:
        return None
    agents = _require_coordinator().agents
    key = agent_id.casefold().strip()
    for agent in agents.values():
        if key == agent.id.casefold() or key == agent.name.casefold():
            return agent.id
    matches = [agent for agent in agents.values()
               if agent.name.split(" — ", 1)[0].casefold() == key
               and (not domain or agent.domain == domain)]
    if len(matches) == 1:
        return matches[0].id
    if len(matches) > 1:
        raise HTTPException(status_code=400, detail="Ambiguous agent name; provide a canonical ID or domain")
    raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")


@app.post("/api/agents/{agent_id}/dispatch")
async def dispatch_to_agent(agent_id: str, task: str):
    """Dispatch a task to a specific agent."""
    if not _orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    result = await _orchestrator.dispatch_task(goal=task, agent_id=_resolve_agent(agent_id))
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


class SwarmTaskRequest(BaseModel):
    task: str
    agent_id: Optional[str] = None
    domain: Optional[str] = None
    context: str = ""


@app.post("/api/swarm/dispatch")
async def swarm_dispatch(request: SwarmTaskRequest):
    coordinator = _require_coordinator()
    task = await coordinator.submit_task(
        goal=request.task, domain=request.domain,
        agent_id=_resolve_agent(request.agent_id, request.domain),
        metadata={"context": request.context} if request.context else None,
    )
    return {"task_id": task.id, "assigned_to": task.assigned_to,
            "status": task.status.value, "goal": task.goal}


@app.get("/api/swarm/status")
async def swarm_status():
    coordinator = _require_coordinator()
    return {**coordinator.get_status(), "dispatcher": "ONLINE" if coordinator._running else "OFFLINE",
            "agents_registered": len(coordinator.agents)}


@app.get("/api/swarm/agent/{agent_id}")
async def swarm_agent(agent_id: str, domain: Optional[str] = None):
    from dataclasses import asdict
    coordinator = _require_coordinator()
    return asdict(coordinator.agents[_resolve_agent(agent_id, domain)])


@app.post("/api/swarm/all-hands")
async def swarm_all_hands():
    coordinator = _require_coordinator()
    leads = {"executive": "SOLOMON", "business": "NEXUS", "communications": "ECHO",
             "technology": "FORGE", "talent": "SCOUT", "creative": "MUSE",
             "scientific": "QUANTUM", "personal": "VALET"}
    resolved = [(domain, _resolve_agent(name, domain)) for domain, name in leads.items()]
    tasks = []
    for domain, agent_id in resolved:
        task = await coordinator.submit_task(
            goal=f"All Hands status report for the {domain} domain. Summarize readiness, active tasks, and today's top priority.",
            domain=domain, task_type="analysis", agent_id=agent_id, priority=2,
        )
        tasks.append({"domain": domain, "agent": agent_id, "task_id": task.id})
    return {"all_hands": "INITIATED", "tasks_dispatched": len(tasks), "agents": tasks}


@app.post("/api/tasks")
async def create_task(request: TaskRequest):
    """Create and queue a new task."""
    if not _orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    task = await _orchestrator.dispatch_task(
        goal=request.goal,
        domain=request.domain,
        task_type=request.task_type,
        agent_id=_resolve_agent(request.agent_id, request.domain),
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


@app.get("/api/swarm/task/{task_id}")
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

    coordinator = _require_coordinator()
    if task_id not in coordinator.tasks:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    try:
        result = await _orchestrator.execute_task(task_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Task execution failed") from exc
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
    if not _orchestrator or not _orchestrator._model_router:
        raise HTTPException(status_code=503, detail="Model router not initialized")
    router = _orchestrator._model_router

    return {
        "models": [
            {
                "id": m.id,
                "name": m.name,
                "tier": m.tier.value,
                "provider": m.provider,
                "cost_per_1k_input": m.cost_per_1k_input,
                "cost_per_1k_output": m.cost_per_1k_output,
                "pricing_known": m.pricing_known,
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
    from hyperclaw.telegram_bot import _allowed_chat_ids
    if not chat_id.lstrip("-").isdigit() or int(chat_id) not in _allowed_chat_ids():
        raise HTTPException(status_code=403, detail="Chat is not allowed")
    success = await _orchestrator.send_telegram(chat_id, message)
    if not success:
        raise HTTPException(status_code=502, detail="Telegram delivery failed")
    return {"success": success}


# ============================================================================
# SETUP ENDPOINT
# ============================================================================

@app.post("/api/setup")
async def setup_hyperclaw(init_db: bool = False):
    """Run HyperClaw setup."""
    from hyperclaw.setup import run_setup
    result = await run_setup(init_db=init_db)
    return result.to_dict()


# ============================================================================
# WEBHOOK ENDPOINTS (for integrations)
# ============================================================================

@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Handle Telegram webhook updates."""
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(status_code=503, detail="Telegram webhook is not configured")
    header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(header.encode(), secret.encode()):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("Expected an update object")
        message = data.get("message", {})
        if not isinstance(message, dict):
            raise ValueError("Expected a message object")
        text = message.get("text", "")
        chat_id = message.get("chat", {}).get("id")
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid Telegram update") from exc
    from hyperclaw.telegram_bot import _allowed_chat_ids
    if not isinstance(chat_id, int) or chat_id not in _allowed_chat_ids():
        raise HTTPException(status_code=403, detail="Chat is not allowed")
    if not _orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")
    if text:
        try:
            response = await _orchestrator.chat(message=text, session_id=f"telegram_{chat_id}", channel="telegram")
            if not await _orchestrator.send_telegram(str(chat_id), response):
                raise RuntimeError("Telegram delivery failed")
        except Exception as exc:
            logger.exception("Telegram webhook processing failed")
            raise HTTPException(status_code=502, detail="Telegram request failed") from exc
    return {"ok": True}


# ============================================================================
# UTILITY ENDPOINTS
# ============================================================================

@app.get("/api/config")
async def get_config():
    """Get non-sensitive configuration."""
    candidates = _orchestrator._model_router.inference.candidates() if _orchestrator else []
    return {
        "hyperclaw_root": str(HYPERCLAW_ROOT),
        "model": candidates[0][1] if candidates else None,
        "provider": candidates[0][0].name if candidates else None,
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

    _load_environment()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", os.environ.get("HYPERCLAW_PORT", "8001")))

    uvicorn.run(
        "hyperclaw.server:app",
        host=host,
        port=port,
        reload=os.environ.get("RELOAD", "").lower() == "true",
        log_level="info"
    )


from hyperclaw.dashboard_api import router as dashboard_router

app.include_router(dashboard_router)


if __name__ == "__main__":
    main()
