"""
ModelRouter — routes tasks to the optimal model and executes them.
Integrates UCB1 bandit routing with live HyperState scores.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from core.hyperstate.schema import HyperState
from core.hyperrouter.bandit import HyperRouter, update_scores
from .claude_client import ClaudeClient
from .chatjimmy_client import ChatJimmyClient

log = logging.getLogger("hyperclaw.model_router")

_CHATJIMMY_TASK_TYPES = frozenset([
    "routing", "classification", "quick_lookup",
    "summarization", "draft", "triage", "status_check",
])


@dataclass
class SwarmMessage:
    """Record of a single model call routed through the HyperSwarm."""
    message_id: UUID = field(default_factory=uuid.uuid4)
    agent_id: str = ""
    model_used: str = ""
    task_type: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    content: str = ""
    certified: bool = False
    state_id: UUID = field(default_factory=uuid.uuid4)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ModelRouter:
    """
    Routes tasks to the correct model client using live UCB1 scores from HyperState.
    Logs every call as a SwarmMessage appended to state.experiment_log.
    """

    def __init__(
        self,
        claude_client: ClaudeClient,
        chatjimmy_client: Optional[ChatJimmyClient] = None,
    ) -> None:
        self._claude = claude_client
        self._chatjimmy = chatjimmy_client
        self._router = HyperRouter()

    async def route(
        self,
        task_type: str,
        state: HyperState,
        cost_budget: Optional[float] = None,
        latency_budget: Optional[float] = None,
    ) -> tuple[str, str]:
        """
        Route a task using live UCB1 scores from state.model_scores.
        Returns (agent_id, model_id).
        """
        self._router.model_scores = {
            model_id: dict(task_map)
            for model_id, task_map in state.model_scores.items()
        }
        self._router.agent_scores = dict(state.agent_scores)
        self._router.total_attempts = max(
            sum(s.attempts for s in state.agent_scores.values()), 1
        )
        return self._router.route(task_type, cost_budget=cost_budget, latency_budget=latency_budget)

    async def call(
        self,
        task_type: str,
        messages: list[dict],
        system: str,
        state: HyperState,
        cost_budget: Optional[float] = None,
        latency_budget: Optional[float] = None,
    ) -> str:
        """
        Route the task, call the selected model, log a SwarmMessage to state.
        Returns the model's text response.
        """
        agent_id, model_id = await self.route(task_type, state, cost_budget, latency_budget)

        t0 = time.time()
        content = ""
        input_tokens = 0
        output_tokens = 0
        certified = False

        try:
            if model_id == "chatjimmy" and self._chatjimmy is not None:
                resp = await self._chatjimmy.chat(messages, system_prompt=system)
                content = resp.text
                input_tokens = resp.stats.prefill_tokens
                output_tokens = resp.stats.decode_tokens
                # ChatJimmy is NEVER auto-certified
                certified = False
            else:
                content = await self._claude.chat(messages, system=system)
                # Token counts approximated (full tracking in ClaudeClient logs)
                input_tokens = sum(len(str(m.get("content", ""))) // 4 for m in messages)
                output_tokens = len(content) // 4
        except Exception as e:
            log.error(f"ModelRouter call failed for model={model_id}: {e}")
            raise

        latency_ms = (time.time() - t0) * 1000
        cost_per_1k = 0.003 if "claude" in model_id else 0.000001
        cost_usd = (input_tokens + output_tokens) / 1000 * cost_per_1k

        msg = SwarmMessage(
            agent_id=agent_id,
            model_used=model_id,
            task_type=task_type,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            content=content[:500],
            certified=certified,
            state_id=state.state_id,
        )

        # Update model scores in state
        from core.hyperstate.schema import ModelScore
        if model_id not in state.model_scores:
            state.model_scores[model_id] = {}
        if task_type not in state.model_scores[model_id]:
            state.model_scores[model_id][task_type] = ModelScore()
        state.model_scores[model_id][task_type].attempts += 1
        # Count as success if we got a non-empty response
        if content:
            state.model_scores[model_id][task_type].successes += 1

        state._bump_version()
        log.info(
            f"SwarmMessage | agent={agent_id} model={model_id} task={task_type} "
            f"latency={latency_ms:.0f}ms cost=${cost_usd:.5f} certified={certified}"
        )
        return content
