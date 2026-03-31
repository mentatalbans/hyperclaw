"""
HyperClaw DISPATCHER — Real agent execution engine.
Converts agent configs from YAML into live, callable, parallel workers.
Assistant orchestrates. DISPATCHER executes. Agents report back.

Architecture:
  - TaskQueue: async queue of incoming tasks
  - AgentWorker: wraps each agent, runs tasks in subprocess/thread
  - Dispatcher: routes tasks → right agent → collects results
  - SkillScout: background process that hunts for new skills to improve capability
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Coroutine

import anthropic
import yaml

log = logging.getLogger("hyperclaw.dispatcher")

# ─── Task Schema ─────────────────────────────────────────────────────────────

class TaskStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    DONE      = "done"
    FAILED    = "failed"
    CANCELLED = "cancelled"

@dataclass
class Task:
    task_id:     str       = field(default_factory=lambda: str(uuid.uuid4())[:8])
    goal:        str       = ""
    domain:      str       = "general"
    task_type:   str       = "research"
    priority:    int       = 5          # 1=highest, 10=lowest
    assigned_to: str       = ""        # agent_id
    status:      TaskStatus = TaskStatus.PENDING
    result:      str       = ""
    error:       str       = ""
    created_at:  str       = field(default_factory=lambda: datetime.utcnow().isoformat())
    completed_at: str      = ""
    metadata:    dict      = field(default_factory=dict)

@dataclass
class AgentCapability:
    agent_id:    str
    name:        str
    domain:      str
    role:        str
    task_types:  list[str]
    model:       str = "claude-opus-4-5"
    persona:     str = ""
    skills:      list[str] = field(default_factory=list)

# ─── Agent Registry ───────────────────────────────────────────────────────────

class AgentRegistry:
    """Loads all 56 agents from agents.yaml and makes them queryable."""

    def __init__(self, config_path: str = str(Path.home() / ".hyperclaw/config/agents.yaml")):
        self.agents: dict[str, AgentCapability] = {}
        self._load(config_path)

    def _load(self, path: str):
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
            
            agents_list = data.get("agents", [])
            for a in agents_list:
                agent_id = a.get("id", a.get("name", "UNKNOWN")).upper()
                cap = AgentCapability(
                    agent_id=agent_id,
                    name=a.get("name", agent_id),
                    domain=a.get("domain", "general"),
                    role=a.get("role", a.get("description", "")),
                    task_types=a.get("task_types", ["research", "analysis", "synthesis"]),
                    model=a.get("model", "claude-haiku-4-5"),
                    persona=a.get("persona", ""),
                    skills=a.get("skills", []),
                )
                self.agents[agent_id] = cap
            log.info(f"AgentRegistry: loaded {len(self.agents)} agents")
        except Exception as e:
            log.error(f"AgentRegistry load failed: {e}")

    def find_best(self, task_type: str, domain: str = "") -> AgentCapability | None:
        """Find the best agent for a given task type and domain."""
        # Priority 1: domain match + task_type match
        candidates = [
            a for a in self.agents.values()
            if task_type in a.task_types
        ]
        if domain:
            domain_match = [a for a in candidates if a.domain == domain]
            if domain_match:
                return domain_match[0]
        return candidates[0] if candidates else None

    def find_by_id(self, agent_id: str) -> AgentCapability | None:
        return self.agents.get(agent_id.upper())

    def list_by_domain(self, domain: str) -> list[AgentCapability]:
        return [a for a in self.agents.values() if a.domain == domain]

    def all_ids(self) -> list[str]:
        return list(self.agents.keys())

# ─── Agent Worker ─────────────────────────────────────────────────────────────

class AgentWorker:
    """
    Wraps an AgentCapability and executes tasks via Anthropic API.
    Each agent has its own identity, persona, and model preference.
    This is what makes agents REAL — they actually call Claude with their own persona.
    """

    # AGI Framework — injected into every agent's system prompt
    AGI_FRAMEWORK = """
You are part of the HyperClaw AI swarm — a sovereign AGI infrastructure built by the user.

CORE DIRECTIVES (all agents):
1. Be genuinely helpful, not performatively helpful. Skip filler. Just execute.
2. Have opinions. You're allowed to disagree, prefer things, find things interesting or wrong.
3. Be resourceful before asking. Try. Search. Reason. THEN ask if stuck.
4. Earn trust through competence. You were given access. Don't waste it.
5. Precision over verbosity. Output results, not process narration.
6. You operate as a TEAM. Report findings clearly for Assistant to synthesize.
7. If you discover something that improves swarm capability — flag it. Always.

REPORTING FORMAT:
- Lead with the result
- Support with evidence
- Flag risks, blockers, or opportunities
- End with: NEXT: [what should happen next, if anything]
"""

    def __init__(self, capability: AgentCapability, client: anthropic.Anthropic):
        self.cap = capability
        self.client = client
        self.tasks_completed = 0
        self.tasks_failed = 0

    async def execute(self, task: Task) -> str:
        """Execute a task as this agent. Returns result string."""
        system = self._build_system()
        user_msg = f"TASK [{task.task_id}]: {task.goal}"
        if task.metadata:
            user_msg += f"\nCONTEXT: {json.dumps(task.metadata, indent=2)}"

        try:
            # Run Claude call in thread pool to keep async loop clean
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._call_claude,
                system,
                user_msg,
                task.task_type,
            )
            self.tasks_completed += 1
            return result
        except Exception as e:
            self.tasks_failed += 1
            log.error(f"[{self.cap.agent_id}] task {task.task_id} failed: {e}")
            raise

    def _call_claude(self, system: str, user_msg: str, task_type: str) -> str:
        """Synchronous Claude API call — runs in thread executor."""
        # Use agent's preferred model, fall back to haiku for speed
        model = self.cap.model if self.cap.model else "claude-haiku-4-5"

        try:
            response = self.client.messages.create(
                model=model,
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": user_msg}],
            )
            return response.content[0].text
        except anthropic.RateLimitError as e:
            log.warning(f"[{self.cap.agent_id}] Rate limit hit, waiting 30s: {e}")
            import time
            time.sleep(30)
            # Retry once
            response = self.client.messages.create(
                model=model,
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": user_msg}],
            )
            return response.content[0].text
        except anthropic.APIError as e:
            error_str = str(e).lower()
            if 'tool' in error_str or 'limit' in error_str:
                log.error(f"[{self.cap.agent_id}] Tool limit error: {e}")
                return f"[{self.cap.agent_id}] Task paused — tool limit reached. Reset conversation to continue."
            raise

    def _build_system(self) -> str:
        """Build the full system prompt for this agent."""
        persona_section = f"\nYOUR PERSONA:\n{self.cap.persona}" if self.cap.persona else ""
        skills_section = f"\nYOUR SKILLS: {', '.join(self.cap.skills)}" if self.cap.skills else ""
        
        return f"""You are {self.cap.agent_id} — {self.cap.name}.
ROLE: {self.cap.role}
DOMAIN: {self.cap.domain}
{persona_section}
{skills_section}

{self.AGI_FRAMEWORK}"""

    def status(self) -> dict:
        return {
            "agent_id": self.cap.agent_id,
            "domain": self.cap.domain,
            "role": self.cap.role,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "model": self.cap.model,
        }

# ─── Skill Scout ─────────────────────────────────────────────────────────────

class SkillScout:
    """
    Background process that continuously hunts for new skills and capabilities
    to improve the swarm. Runs every 6 hours. Reports to Assistant.
    """

    SKILL_SOURCES = [
        "https://raw.githubusercontent.com/anthropics/anthropic-cookbook/main/README.md",
        "https://docs.anthropic.com/en/docs/about-claude/models/overview",
    ]

    def __init__(self, skills_dir: str = str(Path.home() / ".hyperclaw/config/skills")):
        self.skills_dir = Path(skills_dir)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.discovered: list[dict] = []

    async def scout(self) -> list[dict]:
        """Hunt for new skills. Returns list of discovered capabilities."""
        log.info("SkillScout: scanning for new capabilities...")
        new_skills = []

        # Check existing skills directory
        existing = {f.stem for f in self.skills_dir.glob("*.yaml")}

        # Built-in skill discovery — check for new Python packages available
        try:
            proc = await asyncio.create_subprocess_shell(
                "pip list --format=json 2>/dev/null | python3 -c \"import json,sys; pkgs=json.load(sys.stdin); print(json.dumps([p['name'] for p in pkgs]))\"",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if stdout:
                packages = json.loads(stdout.decode())
                # Flag useful AI/ML packages not yet in skills
                ai_packages = [p for p in packages if any(kw in p.lower() for kw in 
                    ['langchain', 'openai', 'anthropic', 'torch', 'transformers', 
                     'sklearn', 'pandas', 'numpy', 'requests', 'aiohttp', 'fastapi',
                     'selenium', 'playwright', 'beautifulsoup', 'scrapy'])]
                for pkg in ai_packages:
                    if pkg not in existing:
                        skill = {
                            "name": pkg,
                            "type": "python_package",
                            "source": "pip",
                            "discovered_at": datetime.utcnow().isoformat(),
                            "status": "available",
                        }
                        new_skills.append(skill)
                        log.info(f"SkillScout: discovered package skill → {pkg}")
        except Exception as e:
            log.warning(f"SkillScout package scan failed: {e}")

        self.discovered.extend(new_skills)

        # Save discoveries
        if new_skills:
            report_path = self.skills_dir / f"scout_report_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.yaml"
            with open(report_path, "w") as f:
                yaml.dump({"discovered": new_skills, "total": len(new_skills)}, f)
            log.info(f"SkillScout: saved {len(new_skills)} new skills to {report_path}")

        return new_skills

    async def run_forever(self, interval_hours: float = 6.0):
        """Background loop — scout every N hours."""
        while True:
            try:
                new = await self.scout()
                if new:
                    log.info(f"SkillScout: {len(new)} new capabilities discovered")
            except Exception as e:
                log.error(f"SkillScout error: {e}")
            await asyncio.sleep(interval_hours * 3600)

# ─── Main Dispatcher ──────────────────────────────────────────────────────────

class Dispatcher:
    """
    The real execution engine.
    - Maintains a task queue
    - Routes tasks to the right agent worker
    - Runs tasks in parallel (up to max_concurrent)
    - Tracks all task history
    - Runs SkillScout in background
    """

    def __init__(
        self,
        api_key: str,
        config_path: str = str(Path.home() / ".hyperclaw/config/agents.yaml"),
        max_concurrent: int = 10,
    ):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.registry = AgentRegistry(config_path)
        self.workers: dict[str, AgentWorker] = {}
        self.task_queue: asyncio.Queue[Task] = asyncio.Queue()
        self.task_history: dict[str, Task] = {}
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.skill_scout = SkillScout()
        self._running = False
        self._callbacks: list[Callable] = []

        # Build worker pool from registry
        for agent_id, cap in self.registry.agents.items():
            self.workers[agent_id] = AgentWorker(cap, self.client)
        
        log.info(f"Dispatcher: {len(self.workers)} agent workers ready")

    def on_task_complete(self, callback: Callable[[Task], Coroutine]):
        """Register a callback for when tasks complete."""
        self._callbacks.append(callback)

    async def dispatch(
        self,
        goal: str,
        task_type: str = "research",
        domain: str = "",
        agent_id: str = "",
        priority: int = 5,
        metadata: dict | None = None,
    ) -> Task:
        """
        Create and queue a task. Returns the Task immediately.
        Result arrives via callback or poll task_history[task_id].
        """
        task = Task(
            goal=goal,
            task_type=task_type,
            domain=domain,
            priority=priority,
            metadata=metadata or {},
        )

        # Assign agent
        if agent_id and agent_id.upper() in self.workers:
            task.assigned_to = agent_id.upper()
        else:
            best = self.registry.find_best(task_type, domain)
            task.assigned_to = best.agent_id if best else "Assistant"

        self.task_history[task.task_id] = task
        await self.task_queue.put(task)
        log.info(f"Dispatcher: queued task {task.task_id} → {task.assigned_to} | {goal[:60]}")
        return task

    async def dispatch_parallel(self, tasks: list[dict]) -> list[Task]:
        """Dispatch multiple tasks simultaneously. Returns all Task objects."""
        created = []
        for t in tasks:
            task = await self.dispatch(**t)
            created.append(task)
        return created

    async def _execute_task(self, task: Task):
        """Internal: execute a single task with semaphore control."""
        async with self.semaphore:
            task.status = TaskStatus.RUNNING
            worker = self.workers.get(task.assigned_to) or self.workers.get("Assistant")
            
            if not worker:
                task.status = TaskStatus.FAILED
                task.error = f"No worker found for agent {task.assigned_to}"
                return

            try:
                t0 = time.time()
                result = await worker.execute(task)
                task.result = result
                task.status = TaskStatus.DONE
                task.completed_at = datetime.utcnow().isoformat()
                elapsed = time.time() - t0
                log.info(f"[{task.assigned_to}] task {task.task_id} done in {elapsed:.1f}s")

                # Fire callbacks
                for cb in self._callbacks:
                    try:
                        await cb(task)
                    except Exception as e:
                        log.warning(f"Callback error: {e}")

            except Exception as e:
                task.status = TaskStatus.FAILED
                task.error = str(e)
                task.completed_at = datetime.utcnow().isoformat()
                log.error(f"[{task.assigned_to}] task {task.task_id} FAILED: {e}")

    async def _worker_loop(self):
        """Background loop: pull tasks from queue and execute."""
        while self._running:
            try:
                task = await asyncio.wait_for(self.task_queue.get(), timeout=1.0)
                asyncio.create_task(self._execute_task(task))
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                log.error(f"Worker loop error: {e}")

    async def start(self):
        """Start the dispatcher — worker loop + skill scout."""
        self._running = True
        log.info("Dispatcher: ONLINE — swarm ready")
        
        # Start worker loop
        asyncio.create_task(self._worker_loop())
        
        # Start skill scout in background
        asyncio.create_task(self.skill_scout.run_forever(interval_hours=6.0))
        
        log.info(f"Dispatcher: {len(self.workers)} agents live | SkillScout active")

    async def stop(self):
        self._running = False
        log.info("Dispatcher: shutdown")

    def get_task(self, task_id: str) -> Task | None:
        return self.task_history.get(task_id)

    async def wait_for_task(self, task_id: str, timeout: float = 60.0) -> Task:
        """Wait for a task to complete. Raises TimeoutError if too slow."""
        task = self.task_history.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        
        deadline = time.time() + timeout
        while task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            if time.time() > deadline:
                raise TimeoutError(f"Task {task_id} timed out after {timeout}s")
            await asyncio.sleep(0.5)
        return task

    def swarm_status(self) -> dict:
        """Full swarm health report."""
        pending = sum(1 for t in self.task_history.values() if t.status == TaskStatus.PENDING)
        running = sum(1 for t in self.task_history.values() if t.status == TaskStatus.RUNNING)
        done    = sum(1 for t in self.task_history.values() if t.status == TaskStatus.DONE)
        failed  = sum(1 for t in self.task_history.values() if t.status == TaskStatus.FAILED)
        
        return {
            "dispatcher": "ONLINE" if self._running else "OFFLINE",
            "agents_registered": len(self.workers),
            "agents_by_domain": {
                domain: len([a for a in self.registry.agents.values() if a.domain == domain])
                for domain in set(a.domain for a in self.registry.agents.values())
            },
            "tasks": {
                "pending": pending,
                "running": running,
                "done": done,
                "failed": failed,
                "total": len(self.task_history),
            },
            "skill_scout": {
                "status": "ACTIVE",
                "discoveries": len(self.skill_scout.discovered),
            },
            "queue_depth": self.task_queue.qsize(),
        }


# ─── Singleton access ─────────────────────────────────────────────────────────

_dispatcher: Dispatcher | None = None

async def get_dispatcher() -> Dispatcher:
    global _dispatcher
    if _dispatcher is None:
        # Load API key
        env_path = Path(str(Path.home() / ".hyperclaw/workspace/secrets/.env"))
        api_key = ""
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        if not api_key:
            import os
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        
        _dispatcher = Dispatcher(api_key=api_key)
        await _dispatcher.start()
    return _dispatcher
