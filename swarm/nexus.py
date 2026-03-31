"""
HyperSwarm NEXUS — Central swarm facilitator. Decomposes goals, orchestrates agents, assembles results.
"""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from core.hyperstate.schema import HyperState
from swarm.agents.base import BaseAgent

if TYPE_CHECKING:
    from core.hyperstate.state_manager import StateManager
    from memory.causal_graph import CausalGraph
    from models.router import ModelRouter
    from security.hypershield import HyperShield
    from swarm.bid_protocol import BidCoordinator

log = logging.getLogger("hyperclaw.nexus")


class NexusAgent(BaseAgent):
    """
    HyperSwarm facilitator — manages HyperState, coordinates bids, decomposes tasks.
    NEXUS is the always-on orchestrator: every goal passes through it.
    """

    agent_id = "NEXUS"
    domain = "recursive"
    description = "HyperSwarm facilitator — manages HyperState, coordinates bids, decomposes tasks"
    supported_task_types = ["planning", "routing", "coordination"]
    preferred_model = "claude-sonnet-4-6"

    def __init__(
        self,
        bid_coordinator: "BidCoordinator",
        model_router: "ModelRouter",
        state_manager: "StateManager",
        causal_graph: "CausalGraph",
        hyper_shield: "HyperShield",
    ) -> None:
        super().__init__(model_router, state_manager, causal_graph, hyper_shield)
        self.bid_coordinator = bid_coordinator
        self._registry: dict = {}  # agent_id → BaseAgent, injected post-init

    def set_registry(self, registry: dict) -> None:
        self._registry = registry

    async def decompose(self, goal: str, domain: str) -> list[dict]:
        """
        Use Claude to break a goal into subtasks.
        Returns [{subtask_id, task_type, description, domain, depends_on}]
        """
        system = (
            "You are NEXUS, a task decomposition engine. "
            "Break the given goal into 2-5 concrete subtasks. "
            "For each subtask, specify: task_type (one of: research, analysis, synthesis, planning, code, health, finance), "
            "description (one sentence), and depends_on (list of subtask indices that must complete first, or []). "
            "Respond as a JSON array only. Example: "
            '[{"task_type":"research","description":"Research X","depends_on":[]},{"task_type":"synthesis","description":"Synthesize findings","depends_on":[0]}]'
        )
        # Create a temporary state for decomposition
        temp_state = HyperState(domain=domain, task=__import__("core.hyperstate.schema", fromlist=["Task"]).Task(goal=goal, task_type="planning"))

        raw = await self.model_router.call(
            task_type="planning",
            messages=[{"role": "user", "content": f"Goal: {goal}\nDomain: {domain}"}],
            system=system,
            state=temp_state,
        )

        # Parse JSON
        import json, re
        try:
            # Extract JSON array
            match = re.search(r'\[.*\]', raw, re.DOTALL)
            subtasks_raw = json.loads(match.group(0)) if match else []
        except Exception:
            # Fallback: single subtask
            subtasks_raw = [{"task_type": "synthesis", "description": goal, "depends_on": []}]

        subtasks = []
        for i, s in enumerate(subtasks_raw[:5]):
            subtasks.append({
                "subtask_id": str(uuid.uuid4()),
                "task_type": s.get("task_type", "research"),
                "description": s.get("description", ""),
                "domain": domain,
                "depends_on": s.get("depends_on", []),
            })
        return subtasks

    async def orchestrate(self, goal: str, domain: str) -> HyperState:
        """
        Full orchestration pipeline:
        1. create_state → 2. decompose → 3. dispatch → 4. HERALD assembly → 5. impact record
        """
        from core.hyperstate.schema import Task
        state = await self.state_manager.create_state(
            goal=goal,
            domain=domain,
            task_type="planning",
        )
        log.info(f"NEXUS: orchestrating goal='{goal[:60]}' state={state.state_id}")

        subtasks = await self.decompose(goal, domain)
        results: list[str] = []

        import time
        from core.hyperstate.schema import ExperimentEntry

        for subtask in subtasks:
            task_type = subtask["task_type"]
            description = subtask["description"]
            t0 = time.time()

            # Find best agent from registry
            agent = self._find_agent(task_type, domain)
            if agent:
                try:
                    result = await agent.run(description, state, {"domain": domain})
                    agent_label = agent.agent_id
                except Exception as e:
                    log.warning(f"NEXUS: agent {agent.agent_id} failed: {e}")
                    result = f"(agent error: {e})"
                    agent_label = agent.agent_id
            else:
                # Direct model call as fallback
                result = await self.model_router.call(
                    task_type=task_type,
                    messages=[{"role": "user", "content": description}],
                    system="Complete this task concisely and helpfully.",
                    state=state,
                )
                agent_label = f"NEXUS-{task_type}"

            results.append(f"[{agent_label}] {result[:800]}")

            # Record in experiment log
            entry = ExperimentEntry(
                method=agent_label,
                model_used="claude-sonnet-4-6",
                result=result[:1000],
                certified=len(result) > 50,
                latency_ms=(time.time() - t0) * 1000,
            )
            state.experiment_log.append(entry)

        # HERALD assembly
        herald = self._registry.get("HERALD")
        combined = "\n\n".join(results)
        if herald:
            final = await herald.run(
                f"Assemble this into a final deliverable for the goal: '{goal}'\n\nAgent outputs:\n{combined}",
                state,
                {},
            )
        else:
            final = combined

        # Update state
        state.task.goal = goal
        await self.state_manager.update_state(state.state_id, {"routing_weights": state.routing_weights})
        return state

    def _find_agent(self, task_type: str, domain: str) -> "BaseAgent | None":
        """Find the best registered agent for this task_type and domain."""
        candidates = [
            a for a in self._registry.values()
            if task_type in a.supported_task_types and a.agent_id != "NEXUS"
        ]
        # Prefer domain match
        domain_match = [a for a in candidates if a.domain == domain]
        pool = domain_match or candidates
        return pool[0] if pool else None

    async def run(self, task: str, state: HyperState, context: dict) -> str:
        domain = context.get("domain", state.domain)
        final_state = await self.orchestrate(task, domain)
        return f"NEXUS orchestration complete. State: {final_state.state_id}"
