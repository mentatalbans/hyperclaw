"""
HyperClaw Orchestrator
Central coordinator for all agents, integrations, memory, and model routing.
Production-ready with cost-optimized model selection and multi-agent coordination.
"""

import asyncio
import json
import logging
import os
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

    def __init__(self):
        self._initialized = False
        self._model_router: Optional[ModelRouter] = None
        self._coordinator: Optional[AgentCoordinator] = None
        self._memory: Optional[MemoryManager] = None
        self._db_pool = None
        self._integrations: dict[str, Any] = {}
        self._system_prompt: str = ""

    async def initialize(self, db_pool=None):
        """Initialize all components."""
        if self._initialized:
            return

        logger.info("Initializing HyperClaw Orchestrator...")

        # Database pool
        self._db_pool = db_pool

        # Initialize model router (handles Claude, ChatJimmy, etc.)
        self._model_router = get_model_router()
        logger.info(f"Model router initialized with {len(self._model_router.models)} models")

        # Initialize agent coordinator
        self._coordinator = await get_coordinator()
        logger.info(f"Agent coordinator initialized with {len(self._coordinator.agents)} agents")

        # Initialize memory manager
        self._memory = await get_memory_manager(db_pool)
        logger.info("Memory manager initialized")

        # Load system prompt
        self._system_prompt = self._build_system_prompt()

        # Initialize integrations
        await self._init_integrations()

        # Start background workers for task processing
        await self._coordinator.start_workers(num_workers=3)

        self._initialized = True
        logger.info("Orchestrator initialization complete")

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

    async def chat(
        self,
        message: str,
        session_id: str = "default",
        channel: str = "api",
        stream: bool = False,
        force_model: str = None
    ) -> Union[str, AsyncIterator[str]]:
        """
        Main chat interface with intelligent model routing.
        Simple queries go to ChatJimmy, complex tasks to Claude.
        """
        if not self._initialized:
            await self.initialize()

        # Get conversation history
        history = self._memory.get_conversation_history(session_id)

        # Add user message to history
        self._memory.add_message(session_id, "user", message, {"channel": channel})

        # Recall relevant memories
        relevant_memories = await self._memory.recall(message, limit=5)
        memory_context = ""
        if relevant_memories:
            memory_context = "\n\n## Relevant Memories\n"
            for mem in relevant_memories:
                memory_context += f"- [{mem.memory_type}] {mem.content}\n"

        # Build system prompt with memory context
        system = self._system_prompt + memory_context

        # Prepare messages for API
        messages = self._prepare_messages(message, history)

        if stream:
            return self._stream_response(messages, system, session_id, force_model)
        else:
            return await self._get_response(messages, system, session_id, force_model)

    async def _get_response(
        self,
        messages: list[dict],
        system: str,
        session_id: str,
        force_model: str = None
    ) -> str:
        """Get a response using cost-optimized model routing."""
        user_message = messages[-1]["content"] if messages else ""

        try:
            # Use model router for intelligent selection
            response, metadata = await self._model_router.call(
                message=user_message,
                system=system,
                history=messages[:-1],  # Exclude current message
                model_override=force_model,
            )

            # Log model usage
            model_used = metadata.get("model_name", "unknown")
            tier_used = metadata.get("tier", "unknown")
            logger.info(f"Response from {model_used} (tier={tier_used})")

            # Store assistant message
            self._memory.add_message(session_id, "assistant", response, {
                "model": metadata.get("model"),
                "tier": tier_used,
            })

            # Auto-store significant exchanges
            await self._auto_store_memory(user_message, response)

            # Log to daily
            await self._memory.log_to_daily(
                f"Chat ({model_used}): {user_message[:80]}...",
                "conversation"
            )

            return response

        except Exception as e:
            logger.error(f"Chat error: {e}", exc_info=True)
            return f"[Error: {e}]"

    async def _stream_response(
        self,
        messages: list[dict],
        system: str,
        session_id: str,
        force_model: str = None
    ) -> AsyncIterator[str]:
        """Stream a response (falls back to non-streaming for now)."""
        # For streaming, we need to use Anthropic's streaming API directly
        # The model router doesn't support streaming yet, so we'll use
        # the standard model and stream from there
        import anthropic

        user_message = messages[-1]["content"] if messages else ""

        # Determine which model to use
        if force_model:
            model_id = force_model
        else:
            model_config = self._model_router.select_model(user_message)
            model_id = model_config.id

        # Only Anthropic models support streaming currently
        if not model_id.startswith("claude"):
            # Fallback to non-streaming
            response = await self._get_response(messages, system, session_id, force_model)
            yield response
            return

        full_response = []

        try:
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            async_client = anthropic.AsyncAnthropic(api_key=api_key)

            async with async_client.messages.stream(
                model=model_id,
                max_tokens=4096,
                system=system,
                messages=messages,
            ) as stream:
                async for text in stream.text_stream:
                    full_response.append(text)
                    yield text

            # Store complete response
            response_text = "".join(full_response)
            self._memory.add_message(session_id, "assistant", response_text)
            await self._auto_store_memory(user_message, response_text)

        except Exception as e:
            logger.error(f"Streaming error: {e}", exc_info=True)
            yield f"[Error: {e}]"

    def _prepare_messages(self, message: str, history: list[dict]) -> list[dict]:
        """Prepare messages for API call."""
        messages = []
        for msg in history[-20:]:
            messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
        messages.append({"role": "user", "content": message})
        return messages

    async def _auto_store_memory(self, user_msg: str, assistant_msg: str):
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
                source="auto_store"
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

        # Close database pool
        if self._db_pool:
            await self._db_pool.close()

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
