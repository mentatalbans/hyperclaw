"""
HyperClaw Agent Coordinator
Orchestrates all agents, routes tasks by domain, and manages execution.
"""

import asyncio
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from .model_router import ModelRouter, ModelTier, get_model_router

logger = logging.getLogger("hyperclaw.coordinator")

# Paths
ROOT_DIR = Path(__file__).parent.parent
CONFIG_DIR = ROOT_DIR / "config"


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class AgentConfig:
    """Configuration for an agent."""
    id: str
    name: str
    domain: str
    role: str
    tier: int = 1
    model_preference: str = "claude-sonnet"
    task_types: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    persona: str = ""


@dataclass
class Task:
    """A task to be executed by an agent."""
    id: str
    goal: str
    domain: str
    task_type: str = "general"
    priority: int = 5
    assigned_to: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[str] = None
    error: Optional[str] = None
    parent_id: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None


# Domain keywords for routing
DOMAIN_KEYWORDS = {
    "personal": ["schedule", "calendar", "reminder", "task", "todo", "health", "fitness", "meal", "travel", "home"],
    "business": ["meeting", "email", "report", "strategy", "revenue", "sales", "client", "contract", "deal", "pipeline"],
    "communications": ["message", "telegram", "slack", "email", "notification", "alert", "send", "reply"],
    "scientific": ["research", "data", "analysis", "paper", "study", "experiment", "hypothesis"],
    "creative": ["write", "design", "content", "story", "article", "blog", "video", "image"],
    "technology": ["code", "debug", "deploy", "server", "api", "database", "security", "infrastructure"],
    "talent": ["athlete", "talent", "roster", "brand", "sponsorship", "partnership"],
}

# Task type patterns
TASK_TYPE_PATTERNS = {
    "research": [r"research", r"find out", r"look up", r"investigate", r"what is", r"who is", r"learn about"],
    "analysis": [r"analyze", r"evaluate", r"assess", r"compare", r"review"],
    "synthesis": [r"summarize", r"combine", r"synthesize", r"consolidate", r"compile"],
    "planning": [r"plan", r"strategy", r"schedule", r"organize", r"prioritize"],
    "execution": [r"send", r"create", r"make", r"do", r"execute", r"perform", r"complete"],
    "monitoring": [r"check", r"monitor", r"watch", r"track", r"status"],
    "communication": [r"tell", r"inform", r"notify", r"message", r"email", r"reply"],
}


class AgentCoordinator:
    """
    Central coordinator for all HyperClaw agents.
    Routes tasks to appropriate agents based on domain and capabilities.
    """

    def __init__(self, model_router: ModelRouter = None):
        self.model_router = model_router or get_model_router()
        self.agents: dict[str, AgentConfig] = {}
        self.tasks: dict[str, Task] = {}
        self.task_queue: asyncio.Queue = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._running = False
        self._load_agents()

    def _load_agents(self):
        """Load agent configurations from YAML."""
        agents_file = CONFIG_DIR / "agents.yaml"

        if not agents_file.exists():
            logger.warning(f"Agents config not found: {agents_file}")
            self._create_default_agents()
            return

        try:
            with open(agents_file) as f:
                config = yaml.safe_load(f)

            for agent_data in config.get("agents", []):
                agent = AgentConfig(
                    id=agent_data.get("id", "unknown"),
                    name=agent_data.get("name", agent_data.get("id", "Unknown")),
                    domain=agent_data.get("domain", "general"),
                    role=agent_data.get("role", ""),
                    tier=agent_data.get("tier", 1),
                    model_preference=agent_data.get("model_preference", "claude-sonnet"),
                    task_types=agent_data.get("task_types", []),
                    capabilities=agent_data.get("capabilities", []),
                    persona=agent_data.get("persona", ""),
                )
                self.agents[agent.id] = agent

            logger.info(f"Loaded {len(self.agents)} agents from config")

        except Exception as e:
            logger.error(f"Failed to load agents config: {e}")
            self._create_default_agents()

    def _create_default_agents(self):
        """Create default agents if config not available."""
        defaults = [
            AgentConfig("ATLAS", "Atlas", "personal", "Personal productivity assistant", task_types=["planning", "execution"]),
            AgentConfig("HERALD", "Herald", "communications", "Communications manager", task_types=["communication"]),
            AgentConfig("STRATEGOS", "Strategos", "business", "Business strategist", task_types=["analysis", "planning"]),
            AgentConfig("SCRIBE", "Scribe", "scientific", "Research assistant", task_types=["research", "synthesis"]),
            AgentConfig("AUTHOR", "Author", "creative", "Creative writer", task_types=["synthesis", "execution"]),
            AgentConfig("FORGE", "Forge", "technology", "Technical specialist", task_types=["execution", "analysis"]),
            AgentConfig("NEXUS", "Nexus", "executive", "Executive coordinator", task_types=["planning", "monitoring"], tier=0),
        ]

        for agent in defaults:
            self.agents[agent.id] = agent

        logger.info(f"Created {len(self.agents)} default agents")

    # =========================================================================
    # TASK ROUTING
    # =========================================================================

    def classify_domain(self, message: str) -> str:
        """Classify message into a domain."""
        message_lower = message.lower()

        domain_scores = {}
        for domain, keywords in DOMAIN_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in message_lower)
            if score > 0:
                domain_scores[domain] = score

        if domain_scores:
            return max(domain_scores, key=domain_scores.get)

        return "general"

    def classify_task_type(self, message: str) -> str:
        """Classify message into a task type."""
        message_lower = message.lower()

        for task_type, patterns in TASK_TYPE_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, message_lower):
                    return task_type

        return "general"

    def select_agent(self, task: Task) -> Optional[AgentConfig]:
        """Select the best agent for a task."""
        candidates = []

        for agent_id, agent in self.agents.items():
            score = 0

            # Domain match
            if agent.domain == task.domain:
                score += 10
            elif agent.domain == "executive":
                score += 3  # Executive agents can handle anything

            # Task type match
            if task.task_type in agent.task_types:
                score += 5

            # Tier bonus (lower tier = more capable)
            score += (3 - agent.tier)

            if score > 0:
                candidates.append((agent, score))

        if not candidates:
            # Fallback to NEXUS or first available
            if "NEXUS" in self.agents:
                return self.agents["NEXUS"]
            return list(self.agents.values())[0] if self.agents else None

        # Sort by score, return best
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0][0]

    def determine_model_tier(self, task: Task, agent: AgentConfig) -> ModelTier:
        """Determine which model tier to use for a task."""
        # Simple tasks use fast model
        if task.task_type in ["monitoring", "communication"]:
            return ModelTier.FAST

        # Complex tasks use standard/premium
        if task.task_type in ["analysis", "research", "planning"]:
            if len(task.goal) > 500 or task.priority >= 8:
                return ModelTier.PREMIUM
            return ModelTier.STANDARD

        # Execution tasks depend on complexity
        if len(task.goal) < 100:
            return ModelTier.FAST

        return ModelTier.STANDARD

    # =========================================================================
    # TASK EXECUTION
    # =========================================================================

    async def submit_task(
        self,
        goal: str,
        domain: str = None,
        task_type: str = None,
        priority: int = 5,
        agent_id: str = None,
        metadata: dict = None
    ) -> Task:
        """Submit a new task for execution."""
        task_id = str(uuid.uuid4())[:8]

        # Auto-classify if not provided
        if not domain:
            domain = self.classify_domain(goal)
        if not task_type:
            task_type = self.classify_task_type(goal)

        task = Task(
            id=task_id,
            goal=goal,
            domain=domain,
            task_type=task_type,
            priority=priority,
            metadata=metadata or {},
        )

        # Select agent
        if agent_id and agent_id in self.agents:
            task.assigned_to = agent_id
        else:
            agent = self.select_agent(task)
            if agent:
                task.assigned_to = agent.id

        self.tasks[task_id] = task
        await self.task_queue.put(task)

        logger.info(f"Task {task_id} submitted: {goal[:50]}... -> {task.assigned_to}")

        return task

    async def execute_task(self, task: Task) -> str:
        """Execute a single task."""
        task.status = TaskStatus.RUNNING

        agent = self.agents.get(task.assigned_to)
        if not agent:
            task.status = TaskStatus.FAILED
            task.error = f"Agent {task.assigned_to} not found"
            return task.error

        # Determine model tier
        model_tier = self.determine_model_tier(task, agent)

        # Build agent prompt
        system_prompt = self._build_agent_prompt(agent, task)

        try:
            # Call model through router
            response, metadata = await self.model_router.call(
                message=task.goal,
                system=system_prompt,
                preferred_tier=model_tier,
                context={"task": task, "agent": agent},
            )

            task.result = response
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now()

            logger.info(f"Task {task.id} completed by {agent.id} using {metadata.get('model_name')}")

            return response

        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            logger.error(f"Task {task.id} failed: {e}")
            return f"Error: {e}"

    def _build_agent_prompt(self, agent: AgentConfig, task: Task) -> str:
        """Build system prompt for an agent."""
        prompt_parts = [
            f"You are {agent.name}, a specialized AI agent.",
            f"Role: {agent.role}",
            f"Domain: {agent.domain}",
            "",
            "## Behavioral Guidelines",
            "- Execute tasks efficiently and completely",
            "- Be concise but thorough",
            "- If you cannot complete a task, explain why clearly",
            "- Provide actionable outputs",
            "",
        ]

        if agent.persona:
            prompt_parts.append(f"## Persona\n{agent.persona}\n")

        if task.metadata:
            prompt_parts.append(f"## Task Context\n{task.metadata}\n")

        prompt_parts.append(f"Current time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

        return "\n".join(prompt_parts)

    # =========================================================================
    # MULTI-AGENT COORDINATION
    # =========================================================================

    async def coordinate(
        self,
        goal: str,
        context: dict = None,
        max_agents: int = 3
    ) -> dict:
        """
        Coordinate multiple agents to accomplish a complex goal.
        Breaks down the goal and routes sub-tasks to specialists.
        """
        logger.info(f"Coordinating goal: {goal[:100]}...")

        # First, use NEXUS (or primary agent) to plan
        plan_result = await self._plan_goal(goal, context)

        # Parse sub-tasks from plan
        sub_tasks = self._parse_subtasks(plan_result)

        if not sub_tasks:
            # Simple task, execute directly
            task = await self.submit_task(goal)
            result = await self.execute_task(task)
            return {
                "goal": goal,
                "approach": "direct",
                "result": result,
                "tasks": [{"id": task.id, "status": task.status.value}],
            }

        # Execute sub-tasks (potentially in parallel)
        results = []
        parallel_tasks = []

        for sub_task_goal in sub_tasks[:max_agents]:
            task = await self.submit_task(sub_task_goal, metadata={"parent_goal": goal})
            parallel_tasks.append(self.execute_task(task))

        # Wait for all tasks
        task_results = await asyncio.gather(*parallel_tasks, return_exceptions=True)

        for i, result in enumerate(task_results):
            if isinstance(result, Exception):
                results.append({"error": str(result)})
            else:
                results.append({"result": result})

        # Synthesize results
        synthesis = await self._synthesize_results(goal, results)

        return {
            "goal": goal,
            "approach": "coordinated",
            "sub_tasks": len(sub_tasks),
            "results": results,
            "synthesis": synthesis,
        }

    async def _plan_goal(self, goal: str, context: dict = None) -> str:
        """Use NEXUS to plan how to accomplish a goal."""
        planning_prompt = f"""Analyze this goal and break it into sub-tasks if needed:

Goal: {goal}

If this is a simple task, respond with: SIMPLE: [brief task description]

If this requires multiple steps or specialists, respond with:
SUBTASKS:
1. [First sub-task]
2. [Second sub-task]
...

Be specific and actionable. Maximum 5 sub-tasks."""

        response, _ = await self.model_router.call(
            message=planning_prompt,
            system="You are a task planning specialist. Break down complex goals into actionable sub-tasks.",
            preferred_tier=ModelTier.FAST,  # Planning is fast
        )

        return response

    def _parse_subtasks(self, plan_result: str) -> list[str]:
        """Parse sub-tasks from planning result."""
        if "SIMPLE:" in plan_result:
            return []

        sub_tasks = []
        lines = plan_result.split("\n")

        for line in lines:
            line = line.strip()
            # Match numbered items
            match = re.match(r"^\d+[\.\)]\s*(.+)$", line)
            if match:
                sub_tasks.append(match.group(1))
            # Match bullet points
            elif line.startswith("- "):
                sub_tasks.append(line[2:])

        return sub_tasks

    async def _synthesize_results(self, goal: str, results: list[dict]) -> str:
        """Synthesize results from multiple sub-tasks."""
        results_text = "\n\n".join(
            f"Result {i+1}: {r.get('result', r.get('error', 'No result'))}"
            for i, r in enumerate(results)
        )

        synthesis_prompt = f"""Original goal: {goal}

Sub-task results:
{results_text}

Synthesize these results into a coherent response that addresses the original goal.
Be concise but complete."""

        response, _ = await self.model_router.call(
            message=synthesis_prompt,
            system="You synthesize multiple results into coherent responses.",
            preferred_tier=ModelTier.STANDARD,
        )

        return response

    # =========================================================================
    # BACKGROUND WORKER
    # =========================================================================

    async def start_workers(self, num_workers: int = 3):
        """Start background task workers."""
        if self._running:
            return

        self._running = True

        for i in range(num_workers):
            worker = asyncio.create_task(self._worker_loop(f"worker-{i}"))
            self._workers.append(worker)

        logger.info(f"Started {num_workers} task workers")

    async def stop_workers(self):
        """Stop background workers."""
        self._running = False

        for worker in self._workers:
            worker.cancel()

        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []

        logger.info("Task workers stopped")

    async def _worker_loop(self, worker_id: str):
        """Worker loop that processes tasks from queue."""
        logger.debug(f"{worker_id} started")

        while self._running:
            try:
                task = await asyncio.wait_for(self.task_queue.get(), timeout=1.0)
                await self.execute_task(task)
                self.task_queue.task_done()
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"{worker_id} error: {e}")

        logger.debug(f"{worker_id} stopped")

    # =========================================================================
    # STATUS & METRICS
    # =========================================================================

    def get_status(self) -> dict:
        """Get coordinator status."""
        active_tasks = sum(1 for t in self.tasks.values() if t.status == TaskStatus.RUNNING)
        pending_tasks = sum(1 for t in self.tasks.values() if t.status == TaskStatus.PENDING)
        completed_tasks = sum(1 for t in self.tasks.values() if t.status == TaskStatus.COMPLETED)

        return {
            "agents": len(self.agents),
            "agents_by_domain": self._count_by_domain(),
            "tasks": {
                "total": len(self.tasks),
                "active": active_tasks,
                "pending": pending_tasks,
                "completed": completed_tasks,
            },
            "workers": len(self._workers),
            "running": self._running,
            "model_stats": self.model_router.get_stats(),
        }

    def _count_by_domain(self) -> dict:
        """Count agents by domain."""
        counts = {}
        for agent in self.agents.values():
            counts[agent.domain] = counts.get(agent.domain, 0) + 1
        return counts

    def list_agents(self) -> list[dict]:
        """List all agents."""
        return [
            {
                "id": a.id,
                "name": a.name,
                "domain": a.domain,
                "role": a.role,
                "tier": a.tier,
                "task_types": a.task_types,
            }
            for a in self.agents.values()
        ]


# Singleton
_coordinator: Optional[AgentCoordinator] = None


async def get_coordinator() -> AgentCoordinator:
    """Get or create coordinator singleton."""
    global _coordinator
    if _coordinator is None:
        _coordinator = AgentCoordinator()
    return _coordinator
