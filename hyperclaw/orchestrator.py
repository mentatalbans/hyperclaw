"""
HyperClaw Orchestrator
Central coordinator for all agents, integrations, memory, and model routing.
Production-ready with cost-optimized model selection and multi-agent coordination.
"""

import asyncio
import json
import logging
import os
from contextlib import aclosing
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Optional, Union

from dotenv import load_dotenv

from .memory_manager import MemoryManager, get_memory_manager
from .model_router import ModelRouter, ModelTier, get_model_router
from .agent_coordinator import AgentCoordinator, get_coordinator, Task

logger = logging.getLogger("hyperclaw.orchestrator")

# Load environment
load_dotenv()

# Configuration
HYPERCLAW_ROOT = Path(os.environ.get("HYPERCLAW_ROOT", Path.home() / ".hyperclaw"))


class Orchestrator:
    """
    Central orchestrator for HyperClaw.
    Manages agents, integrations, memory, and cost-optimized model routing.
    """

    def __init__(self, model_router=None, memory=None):
        self._initialized = False
        self._model_router: Optional[ModelRouter] = model_router
        self._coordinator: Optional[AgentCoordinator] = None
        self._memory: Optional[MemoryManager] = memory
        self._db_pool = None
        self._integrations: dict[str, Any] = {}
        self._system_prompt: str = ""
        self._session_locks = {}
        self._loaded_sessions = set()
        self._last_turns = {}
        self._initialize_lock = asyncio.Lock()

    async def initialize(self, db_pool=None):
        """Initialize each component once, even with simultaneous first turns."""
        async with self._initialize_lock:
            if self._initialized:
                return
            self._db_pool = db_pool
            self._model_router = self._model_router or ModelRouter()
            self._coordinator = AgentCoordinator(self._model_router)
            self._memory = self._memory or MemoryManager(db_pool)
            await self._memory.initialize()
            self._system_prompt = self._build_system_prompt()
            await self._init_integrations()
            await self._coordinator.start_workers(num_workers=3)
            self._initialized = True

    def _build_system_prompt(self) -> str:
        """Build the system prompt from workspace context."""
        parts = [
            "# HyperClaw AI Assistant",
            "",
            "You are a sophisticated AI assistant with access to multiple specialized agents.",
            "",
            "## Core Behaviors",
            "- Be proactive and execute tasks without unnecessary confirmation",
            "- Be resourceful - try multiple approaches before asking for help",
            "- Be concise but thorough when needed",
            "- Maintain context across conversations using memory",
            "- Route complex tasks to appropriate specialist agents",
            "",
            "## Available Agents",
        ]

        # List available agents
        if self._coordinator:
            for agent in list(self._coordinator.agents.values())[:10]:
                parts.append(f"- {agent.name} ({agent.domain}): {agent.role}")

        parts.append("")

        # Add context from memory
        if self._memory:
            context = self._memory.get_system_context()
            if context:
                parts.append("## Workspace Context")
                parts.append(context)
                parts.append("")

        parts.append(f"Current date/time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

        return "\n".join(parts)

    async def _init_integrations(self):
        """Initialize available integrations."""
        # Telegram
        if os.environ.get("TELEGRAM_BOT_TOKEN"):
            self._integrations["telegram"] = {"status": "configured", "type": "bot"}
            logger.info("Telegram integration configured")

        # Gmail
        if os.environ.get("GMAIL_REFRESH_TOKEN"):
            self._integrations["gmail"] = {"status": "configured", "type": "oauth2"}
            logger.info("Gmail integration configured")

        # ChatJimmy
        if os.environ.get("CHATJIMMY_API_KEY"):
            self._integrations["chatjimmy"] = {"status": "configured", "type": "api"}
            logger.info("ChatJimmy integration configured")

        # ElevenLabs
        if os.environ.get("ELEVENLABS_API_KEY"):
            self._integrations["elevenlabs"] = {"status": "configured", "type": "api"}
            logger.info("ElevenLabs integration configured")

        logger.info(f"Initialized {len(self._integrations)} integrations")

    # =========================================================================
    # CHAT INTERFACE (Cost-Optimized)
    # =========================================================================

    async def chat(self, message: str, session_id: str = "default", channel: str = "api",
                   stream: bool = False, force_model: str = None, attachments=None, tools=None, tool_set=None,
                   tool_timeouts=None, default_tool_timeout=None):
        async def text_stream():
            async with aclosing(self._turn_events(message, session_id, channel, force_model,
                    attachments, tools, streaming=stream, tool_set=tool_set,
                    tool_timeouts=tool_timeouts, default_tool_timeout=default_tool_timeout)) as events:
                async for kind, text in events:
                    if kind == "text":
                        yield text
        if stream:
            return text_stream()
        return "".join([text async for text in text_stream()])

    async def stream_events(self, message: str, session_id: str = "default", channel: str = "api",
                            force_model: str = None, attachments=None, tools=None, tool_set=None,
                            tool_timeouts=None, default_tool_timeout=None):
        async with aclosing(self._turn_events(message, session_id, channel, force_model,
                attachments, tools, streaming=True, tool_set=tool_set,
                tool_timeouts=tool_timeouts, default_tool_timeout=default_tool_timeout)) as events:
            async for item in events:
                yield item

    async def _turn_events(self, message, session_id, channel, force_model, attachments, tools, streaming,
                           tool_set=None, tool_timeouts=None, default_tool_timeout=None):
        if not self._initialized:
            await self.initialize()
        session_id = session_id or "default"
        lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            if session_id not in self._loaded_sessions:
                await self._memory.load_conversation(session_id)
                self._loaded_sessions.add(session_id)
            history = self._memory.get_conversation_history(session_id)
            messages = self._prepare_messages(message, history)
            if attachments:
                messages[-1]["content"] = list(attachments) + [{"type": "text", "text": message}]
            from .inference import message_capabilities
            from .model_router import MODELS
            from .providers import record_served_by
            record_served_by("")
            needs = message_capabilities(messages)
            slot = "vision" if needs & {"images", "documents"} else "primary"
            force_model = MODELS[force_model].id if force_model in MODELS else force_model
            enabled_tools = tools if tools is not None else os.environ.get("HYPERCLAW_ENABLE_TOOLS", "0").lower() in ("1", "true", "yes")
            candidates = self._model_router.inference.candidates("tools" if enabled_tools else slot,
                needs | ({"tool_use"} if enabled_tools else set()), force_model)
            serving_model = candidates[0][1] if candidates else "unconfigured"
            system = self._build_system_prompt()
            memories = await self._memory.recall(message, limit=10)
            relevant = [m for m in memories if not m.metadata.get("session_id") or m.metadata["session_id"] == session_id]
            if relevant:
                system += "\n\nRelevant memories:\n" + "\n".join(m.content for m in relevant[:5])
            self._memory.add_message(session_id, "user", messages[-1]["content"], {"channel": channel})
            parts = []
            self._last_turns[session_id] = {"tools_used": [], "model": serving_model}
            try:
                max_tokens = int(os.environ.get("HYPERCLAW_MAX_TOKENS", "4096"))
                if enabled_tools:
                    from .tool_loop import ToolLoop, local_tools
                    from .memory_tools import bind_memory_tools
                    definitions, execute = tool_set if tool_set is not None else local_tools()
                    execute = bind_memory_tools(self._memory, execute, session_id=session_id)
                    loop = ToolLoop(self._model_router.inference, definitions, execute,
                                    tool_timeouts=tool_timeouts, default_tool_timeout=default_tool_timeout)
                    self._last_turns[session_id]["tools_used"] = loop.tools_used
                    events = loop.run(messages, system, model_override=force_model, max_tokens=max_tokens)
                elif streaming:
                    events = self._model_router.inference.stream_events(messages, system, slot=slot,
                        required_capabilities=needs, model_override=force_model, max_tokens=max_tokens)
                else:
                    text, metadata = await self._model_router.call(
                        message=messages[-1]["content"], system=system, history=messages[:-1], slot=slot,
                        model_override=force_model, required_capabilities=needs, max_tokens=max_tokens)
                    async def answer():
                        yield "text", text
                    events = answer()
                async with aclosing(events):
                    async for kind, text in events:
                        if kind == "text":
                            parts.append(text)
                        yield kind, text
                if parts:
                    try:
                        await self._auto_store_memory(message, "".join(parts), session_id)
                    except Exception:
                        logger.warning("Automatic memory storage failed; preserving conversation response", exc_info=True)
            finally:
                from .providers import get_served_by
                self._last_turns[session_id]["served_by"] = get_served_by()
                if parts:
                    self._memory.add_message(session_id, "assistant", "".join(parts), {"channel": channel})
                await self._memory.save_conversation(session_id)

    async def reset_session(self, session_id="default"):
        if not self._initialized:
            await self.initialize()
        async with self._session_locks.setdefault(session_id, asyncio.Lock()):
            await self._memory.clear_conversation(session_id)
            self._loaded_sessions.add(session_id)

    async def import_session(self, session_id, loader) -> bool:
        """Import legacy history once, serialized with turns and durable reset."""
        if not self._initialized:
            await self.initialize()
        async with self._session_locks.setdefault(session_id, asyncio.Lock()):
            if await self._memory.conversation_exists(session_id):
                return False
            history = await loader()
            # Discard an incomplete in-memory import before retrying its save.
            await self._memory.load_conversation(session_id)
            for message in history[-100:]:
                metadata = {key: value for key, value in message.items()
                            if key not in {"id", "role", "content"}}
                self._memory.add_message(session_id, message["role"], message["content"], metadata)
            # Empty imports are durable markers, just like reset sessions.
            await self._memory.save_conversation(session_id)
            self._loaded_sessions.add(session_id)
            return True

    def _prepare_messages(self, message: str, history: list[dict]) -> list[dict]:
        """Prepare messages for API call."""
        messages = []
        for msg in history[-20:]:
            messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
        messages.append({"role": "user", "content": message})
        while messages and messages[0]["role"] != "user":
            messages.pop(0)
        return messages

    async def _auto_store_memory(self, user_msg: str, assistant_msg: str, session_id: str = "default"):
        """Automatically store significant exchanges as memories."""
        store_triggers = [
            "remember", "note that", "important", "always", "never",
            "decided", "approved", "confirmed", "scheduled", "completed",
        ]

        combined = (user_msg + " " + assistant_msg).lower()
        should_store = any(trigger in combined for trigger in store_triggers)

        if should_store:
            summary = f"User: {user_msg[:150]}... | Response: {assistant_msg[:150]}..."
            await self._memory.remember(
                content=summary,
                memory_type="episode",
                importance=0.7,
                source="auto_store",
                metadata={"session_id": session_id}
            )

    # =========================================================================
    # AGENT DISPATCH
    # =========================================================================

    async def dispatch_task(
        self,
        goal: str,
        domain: str = None,
        task_type: str = None,
        agent_id: str = None,
        priority: int = 5
    ) -> Task:
        """Dispatch a task to the agent coordinator."""
        if not self._coordinator:
            raise RuntimeError("Coordinator not initialized")

        task = await self._coordinator.submit_task(
            goal=goal,
            domain=domain,
            task_type=task_type,
            agent_id=agent_id,
            priority=priority,
        )

        return task

    async def execute_task(self, task_id: str) -> str:
        """Execute a specific task."""
        if not self._coordinator:
            raise RuntimeError("Coordinator not initialized")

        task = self._coordinator.tasks.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        return await self._coordinator.execute_task(task)

    async def coordinate_goal(self, goal: str, context: dict = None) -> dict:
        """
        Coordinate multiple agents to accomplish a complex goal.
        """
        if not self._coordinator:
            raise RuntimeError("Coordinator not initialized")

        return await self._coordinator.coordinate(goal, context)

    # =========================================================================
    # INTEGRATION METHODS
    # =========================================================================

    async def send_telegram(self, chat_id: str, message: str) -> bool:
        """Send a Telegram message."""
        if "telegram" not in self._integrations:
            logger.warning("Telegram not configured")
            return False

        try:
            import httpx
            token = os.environ.get("TELEGRAM_BOT_TOKEN")
            url = f"https://api.telegram.org/bot{token}/sendMessage"

            async with httpx.AsyncClient() as client:
                response = await client.post(url, json={
                    "chat_id": chat_id,
                    "text": message,
                    "parse_mode": "Markdown"
                })
                return response.status_code == 200

        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    async def get_emails(self, limit: int = 10) -> list[dict]:
        """Get recent emails."""
        if "gmail" not in self._integrations:
            return []
        # Implementation would use Gmail API
        return []

    # =========================================================================
    # STATUS & HEALTH
    # =========================================================================

    def get_status(self) -> dict:
        """Get comprehensive system status."""
        status = {
            "initialized": self._initialized,
            "timestamp": datetime.now().isoformat(),
        }

        # Model router stats
        if self._model_router:
            status["model_router"] = self._model_router.get_stats()

        # Coordinator stats
        if self._coordinator:
            status["coordinator"] = self._coordinator.get_status()

        # Memory stats
        if self._memory:
            status["memory"] = {
                "conversations": len(self._memory._conversation_history),
                "cached_memories": sum(
                    len(v) for v in self._memory._file_cache.values()
                ),
            }

        # Integrations
        status["integrations"] = {
            name: info["status"]
            for name, info in self._integrations.items()
        }

        # Database
        status["database"] = "connected" if self._db_pool else "not configured"

        return status

    async def health_check(self) -> dict:
        """Run health checks on all components."""
        checks = {
            "orchestrator": self._initialized,
            "model_router": self._model_router is not None,
            "coordinator": self._coordinator is not None,
            "memory": self._memory is not None,
            "database": False,
        }

        # Check database
        if self._db_pool:
            try:
                async with self._db_pool.acquire() as conn:
                    await conn.execute("SELECT 1")
                    checks["database"] = True
            except Exception:
                pass

        return {
            "healthy": all(checks.values()),
            "checks": checks,
            "timestamp": datetime.now().isoformat(),
        }

    # =========================================================================
    # MEMORY INTERFACE
    # =========================================================================

    async def remember(self, content: str, **kwargs) -> str:
        """Store a memory explicitly."""
        if not self._memory:
            return ""
        return await self._memory.remember(content, **kwargs)

    async def recall(self, query: str, **kwargs) -> list:
        """Recall relevant memories."""
        if not self._memory:
            return []
        return await self._memory.recall(query, **kwargs)

    async def save_session(self, session_id: str):
        """Save current session to storage."""
        if self._memory:
            await self._memory.save_conversation(session_id)

    # =========================================================================
    # COST MANAGEMENT
    # =========================================================================

    def get_cost_stats(self) -> dict:
        """Get cost statistics from model router."""
        if not self._model_router:
            return {}
        return self._model_router.get_stats()

    def set_daily_budget(self, budget_usd: float):
        """Set daily budget limit."""
        if self._model_router:
            self._model_router._daily_budget = budget_usd
            logger.info(f"Daily budget set to ${budget_usd}")

    # =========================================================================
    # CLEANUP
    # =========================================================================

    async def shutdown(self):
        """Graceful shutdown."""
        logger.info("Shutting down orchestrator...")

        # Stop coordinator workers
        if self._coordinator:
            await self._coordinator.stop_workers()

        # Save all conversations
        if self._memory:
            for session_id in list(self._memory._conversation_history.keys()):
                await self._memory.save_conversation(session_id)

        # The application lifespan owns the database pool.
        self._initialized = False

        logger.info("Orchestrator shutdown complete")


# ============================================================================
# SINGLETON
# ============================================================================

_orchestrator: Optional[Orchestrator] = None


async def get_orchestrator(db_pool=None) -> Orchestrator:
    """Get or create orchestrator singleton."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
    await _orchestrator.initialize(db_pool)
    return _orchestrator


def get_orchestrator_sync() -> Orchestrator:
    """Synchronous getter for orchestrator."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
        asyncio.run(_orchestrator.initialize())
    return _orchestrator
